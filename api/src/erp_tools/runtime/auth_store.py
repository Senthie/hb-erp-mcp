"""API Key 持久化（SQLite）。

生产环境用 PostgreSQL（:mod:`erp_tools.runtime.pg_store`）；本模块保留为
**本地开发 / 单元测试**后端——零依赖、一个文件即走，且方法签名与 ``PgStore`` 一致
（两者都满足 :class:`ApiKeyStore` 协议）。

设计要点：

* ERP 密码**只以 AES-GCM 密文**存在 `erp_credentials`（密钥在数据目录的
  `erp_credentials.key`，与库分开），用于会话过期后自动重新登录；
  可用 `server.store_erp_password: false` 关闭（此时过期需人工重新绑定）。
* 库里只有 ``sha256(api_key)``，没有明文 key。
* **没有 expires_at**：key 的有效期等于它绑定的 ERP 会话。
  会话一死且无法自动重登，调用方探测到就把这一行置 ``revoked=1``（保留痕迹，便于审计）。
* 同一账号允许多把 key 并存（多设备/多客户端）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..core.security import generate_api_key, hash_api_key
from .audit import AuditEntry
from .call_log import ToolCallLog
from .mcp_services import McpService
from .platform_users import ROLE_USER, PlatformSession, PlatformUser

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS api_keys (
    key_hash    TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    user_id     TEXT,
    owner_id    TEXT,
    created_at  TEXT NOT NULL,
    last_used   TEXT,
    created_ip  TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(username);

CREATE TABLE IF NOT EXISTS mcp_services (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    version     TEXT NOT NULL DEFAULT '',
    endpoint    TEXT,
    transport   TEXT NOT NULL DEFAULT 'streamable-http',
    environment TEXT NOT NULL DEFAULT 'production',
    description TEXT,
    owner       TEXT,
    tools       INTEGER,
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login_at TEXT,
    erp_username  TEXT,
    erp_user_id   TEXT,
    erp_bound_at  TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    created_ip TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    action      TEXT NOT NULL,
    result      TEXT NOT NULL DEFAULT 'success',
    actor_id    TEXT,
    actor_name  TEXT,
    actor_type  TEXT NOT NULL DEFAULT 'anonymous',
    target_type TEXT,
    target_id   TEXT,
    status_code INTEGER,
    ip          TEXT,
    user_agent  TEXT,
    detail      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id);

CREATE TABLE IF NOT EXISTS mcp_call_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    tool         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'success',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    actor_id     TEXT,
    actor_name   TEXT,
    actor_type   TEXT,
    token_id     TEXT,
    erp_username TEXT,
    ip           TEXT,
    user_agent   TEXT,
    error        TEXT,
    arguments    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_calls_created ON mcp_call_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_calls_tool ON mcp_call_logs(tool);
CREATE INDEX IF NOT EXISTS idx_calls_actor ON mcp_call_logs(actor_id);

-- ERP 密码密文（AES-GCM，密钥在数据目录的 erp_credentials.key，与库分开存）
CREATE TABLE IF NOT EXISTS erp_credentials (
    erp_username TEXT PRIMARY KEY,
    secret       TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

_SERVICE_COLUMNS = (
    "id, name, version, endpoint, transport, environment, description, owner, "
    "tools, metadata, created_at, updated_at"
)

_USER_COLUMNS = (
    "id, username, created_at, last_login_at, erp_username, erp_user_id, erp_bound_at, "
    "role, disabled"
)

_SESSION_COLUMNS = (
    "token_hash, user_id, created_at, expires_at, last_seen, created_ip"
)

_AUDIT_COLUMNS = (
    "id, created_at, action, result, actor_id, actor_name, actor_type, "
    "target_type, target_id, status_code, ip, user_agent, detail"
)

_CALL_COLUMNS = (
    "id, created_at, tool, status, duration_ms, actor_id, actor_name, actor_type, "
    "token_id, erp_username, ip, user_agent, error, arguments"
)

_SELECT_COLUMNS = (
    "key_hash, username, user_id, owner_id, created_at, last_used, created_ip, "
    "revoked, note"
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ApiKeyRecord:
    key_hash: str
    username: str
    user_id: str | None = None
    created_at: str = ""
    last_used: str | None = None
    created_ip: str | None = None
    revoked: bool = False
    note: str | None = None
    #: 签发它的平台账号（CLI 直接登录换的 key 没有 owner）
    owner_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row | Iterable) -> "ApiKeyRecord":
        values = tuple(row)
        owner_id = values[3] if len(values) >= 9 else None
        if len(values) >= 9:
            (key_hash, username, user_id, _owner, created_at,
             last_used, created_ip, revoked, note) = values
        else:
            (key_hash, username, user_id, created_at,
             last_used, created_ip, revoked, note) = values
        return cls(
            key_hash=key_hash,
            username=username,
            user_id=user_id,
            created_at=created_at or "",
            last_used=last_used,
            created_ip=created_ip,
            revoked=bool(revoked),
            note=note,
            owner_id=owner_id,
        )


class ApiKeyStore(Protocol):
    """api-key + 服务注册的存储接口。

    中间件与路由只依赖这个协议；生产用 :class:`~erp_tools.runtime.pg_store.PgStore`，
    测试用 :class:`AuthStore`。
    """

    def init_schema(self) -> "ApiKeyStore": ...
    def close(self) -> None: ...

    def issue(self, username: str, *, user_id: str | None = None,
              ip: str | None = None, note: str | None = None,
              owner_id: str | None = None) -> str: ...
    def touch(self, key_hash: str, *, min_interval: float = 0.0) -> None: ...
    def set_user_id(self, key_hash: str, user_id: str | None) -> None: ...
    def resolve(self, raw_key: str | None) -> ApiKeyRecord | None: ...
    def resolve_any(self, raw_key: str | None) -> ApiKeyRecord | None: ...
    def get_by_hash(self, key_hash: str) -> ApiKeyRecord | None: ...
    def list_for_user(self, username: str, *,
                      include_revoked: bool = False) -> list[ApiKeyRecord]: ...
    def count(self, *, include_revoked: bool = False) -> int: ...
    def revoke(self, raw_key: str) -> bool: ...
    def revoke_hash(self, key_hash: str) -> bool: ...
    def revoke_user(self, username: str) -> int: ...

    def register_service(self, values: dict[str, Any]) -> McpService: ...
    def get_service(self, name: str) -> McpService | None: ...
    def list_services(self, *, limit: int = 50) -> list[McpService]: ...

    # 平台账号与登录会话
    def create_user(self, username: str, password_hash: str, *,
                    role: str = ROLE_USER) -> PlatformUser | None: ...
    def get_user(self, user_id: str) -> PlatformUser | None: ...
    def get_credentials(self, username: str) -> tuple[PlatformUser, str] | None: ...
    def mark_login(self, user_id: str) -> None: ...
    def bind_erp(self, user_id: str, erp_username: str,
                 erp_user_id: str | None = None) -> PlatformUser | None: ...
    def create_session(self, token_hash: str, user_id: str, expires_at: str,
                       ip: str | None = None) -> None: ...
    def get_session(self, token_hash: str) -> PlatformSession | None: ...
    def delete_session(self, token_hash: str) -> bool: ...
    def purge_expired_sessions(self) -> int: ...
    def list_for_owner(self, owner_id: str, *,
                       include_revoked: bool = False) -> list[ApiKeyRecord]: ...
    def revoke_owner_prefix(self, owner_id: str, prefix: str) -> bool: ...
    def revoke_owner(self, owner_id: str) -> int: ...

    # 管理员：账号管理 + 审计
    def update_user(self, user_id: str, *, role: str | None = None,
                    disabled: bool | None = None) -> PlatformUser | None: ...
    def list_users(self, *, query: str | None = None, limit: int = 50,
                   offset: int = 0) -> list[PlatformUser]: ...
    def count_users(self, *, query: str | None = None) -> int: ...
    def unbind_erp(self, user_id: str) -> PlatformUser | None: ...
    def count_sessions(self, user_id: str) -> int: ...
    def delete_user_sessions(self, user_id: str) -> int: ...
    def delete_user(self, user_id: str) -> bool: ...
    def add_audit(self, entry: dict[str, Any]) -> None: ...
    def list_audit(self, *, action: str | None = None, actor: str | None = None,
                   result: str | None = None, since: str | None = None,
                   until: str | None = None, limit: int = 100,
                   offset: int = 0) -> list[AuditEntry]: ...

    # MCP 工具调用日志
    def add_call_log(self, entry: dict[str, Any]) -> None: ...
    def list_call_logs(self, *, tool: str | None = None, actor: str | None = None,
                       status: str | None = None, since: str | None = None,
                       until: str | None = None, limit: int = 100,
                       offset: int = 0) -> list[ToolCallLog]: ...
    def count_call_logs(self, *, actor_id: str | None = None) -> int: ...

    # ERP 密码密文（自动重登用；明文永不入库）
    def set_erp_secret(self, erp_username: str, secret: str) -> None: ...
    def get_erp_secret(self, erp_username: str) -> str | None: ...
    def delete_erp_secret(self, erp_username: str) -> bool: ...


class AuthStore:
    """`auth.db` 的唯一访问点。线程安全（单连接 + 互斥锁）。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ---------- 生命周期 ----------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()
        return self._conn

    def _migrate(self) -> None:
        """老库升级：补 `api_keys.owner_id` 与 `users.role/disabled`。"""
        assert self._conn is not None
        key_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(api_keys)")}
        if "owner_id" not in key_columns:
            self._conn.execute("ALTER TABLE api_keys ADD COLUMN owner_id TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_owner ON api_keys(owner_id)"
        )

        user_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(users)")}
        if "role" not in user_columns:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
        if "disabled" not in user_columns:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0"
            )

    def init_schema(self) -> "AuthStore":
        _ = self.conn
        return self

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "AuthStore":
        return self.init_schema()

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 写入 ----------
    def issue(self, username: str, *, user_id: str | None = None,
              ip: str | None = None, note: str | None = None,
              owner_id: str | None = None) -> str:
        """签发一把新 key，返回**明文**（调用方只能保存这一次）。"""
        raw = generate_api_key()
        with self._lock:
            self.conn.execute(
                "INSERT INTO api_keys "
                "(key_hash, username, user_id, owner_id, created_at, created_ip, "
                " revoked, note) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (hash_api_key(raw), username, user_id, owner_id, _utcnow(), ip, note),
            )
            self.conn.commit()
        return raw

    def touch(self, key_hash: str, *, min_interval: float = 0.0) -> None:
        """记录最近使用时间。`min_interval` 可用来减少写盘（默认每次都写）。"""
        with self._lock:
            if min_interval > 0:
                row = self.conn.execute(
                    "SELECT last_used FROM api_keys WHERE key_hash = ?", (key_hash,)
                ).fetchone()
                if row and row["last_used"]:
                    try:
                        last = datetime.fromisoformat(row["last_used"])
                        age = (datetime.now(timezone.utc) - last).total_seconds()
                        if age < min_interval:
                            return
                    except ValueError:
                        pass
            self.conn.execute(
                "UPDATE api_keys SET last_used = ? WHERE key_hash = ?",
                (_utcnow(), key_hash),
            )
            self.conn.commit()

    def set_user_id(self, key_hash: str, user_id: str | None) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE api_keys SET user_id = ? WHERE key_hash = ?", (user_id, key_hash)
            )
            self.conn.commit()

    # ---------- 读取 ----------
    def resolve(self, raw_key: str | None) -> ApiKeyRecord | None:
        """校验明文 key；未撤销才返回记录。"""
        if not raw_key:
            return None
        with self._lock:
            row = self.conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM api_keys "
                "WHERE key_hash = ? AND revoked = 0",
                (hash_api_key(raw_key),),
            ).fetchone()
        return ApiKeyRecord.from_row(row) if row else None

    def resolve_any(self, raw_key: str | None) -> ApiKeyRecord | None:
        """包含已撤销的记录，用于给出更准确的错误原因。"""
        if not raw_key:
            return None
        with self._lock:
            row = self.conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM api_keys WHERE key_hash = ?",
                (hash_api_key(raw_key),),
            ).fetchone()
        return ApiKeyRecord.from_row(row) if row else None

    def get_by_hash(self, key_hash: str) -> ApiKeyRecord | None:
        with self._lock:
            row = self.conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM api_keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
        return ApiKeyRecord.from_row(row) if row else None

    def list_for_user(self, username: str, *, include_revoked: bool = False
                      ) -> list[ApiKeyRecord]:
        sql = f"SELECT {_SELECT_COLUMNS} FROM api_keys WHERE username = ?"
        if not include_revoked:
            sql += " AND revoked = 0"
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self.conn.execute(sql, (username,)).fetchall()
        return [ApiKeyRecord.from_row(r) for r in rows]

    def count(self, *, include_revoked: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM api_keys"
        if not include_revoked:
            sql += " WHERE revoked = 0"
        with self._lock:
            return int(self.conn.execute(sql).fetchone()[0])

    # ---------- 撤销 ----------
    def revoke(self, raw_key: str) -> bool:
        return self.revoke_hash(hash_api_key(raw_key or ""))

    def revoke_hash(self, key_hash: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE key_hash = ? AND revoked = 0",
                (key_hash,),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def revoke_user(self, username: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE username = ? AND revoked = 0",
                (username,),
            )
            self.conn.commit()
            return cur.rowcount

    # ---------- MCP 服务注册信息 ----------
    def register_service(self, values: dict[str, Any]) -> McpService:
        """按 ``name`` 幂等写入：同名服务重复注册＝更新元数据。"""
        name = values["name"]
        now = _utcnow()
        params = (
            name,
            values.get("version") or "",
            values.get("endpoint"),
            values.get("transport") or "streamable-http",
            values.get("environment") or "production",
            values.get("description"),
            values.get("owner"),
            values.get("tools"),
            values.get("metadata") or "{}",
            now,
            now,
        )
        with self._lock:
            self.conn.execute(
                "INSERT INTO mcp_services (name, version, endpoint, transport, "
                "environment, description, owner, tools, metadata, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET version = excluded.version, "
                "endpoint = excluded.endpoint, transport = excluded.transport, "
                "environment = excluded.environment, "
                "description = excluded.description, owner = excluded.owner, "
                "tools = excluded.tools, metadata = excluded.metadata, "
                "updated_at = excluded.updated_at",
                params,
            )
            self.conn.commit()
            row = self.conn.execute(
                f"SELECT {_SERVICE_COLUMNS} FROM mcp_services WHERE name = ?", (name,)
            ).fetchone()
        return McpService.from_mapping(dict(row) if row else {"name": name})

    def get_service(self, name: str) -> McpService | None:
        with self._lock:
            row = self.conn.execute(
                f"SELECT {_SERVICE_COLUMNS} FROM mcp_services WHERE name = ?", (name,)
            ).fetchone()
        return McpService.from_mapping(dict(row)) if row else None

    def list_services(self, *, limit: int = 50) -> list[McpService]:
        with self._lock:
            rows = self.conn.execute(
                f"SELECT {_SERVICE_COLUMNS} FROM mcp_services "
                "ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [McpService.from_mapping(dict(r)) for r in rows]

    # ---------- 平台账号 ----------
    def create_user(self, username: str, password_hash: str, *,
                    role: str = ROLE_USER) -> PlatformUser | None:
        """新建平台账号；用户名已存在时返回 ``None``。"""
        user_id = uuid.uuid4().hex
        now = _utcnow()
        with self._lock:
            try:
                self.conn.execute(
                    "INSERT INTO users (id, username, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, username, password_hash, role, now),
                )
            except sqlite3.IntegrityError:
                return None
            self.conn.commit()
        return PlatformUser(id=user_id, username=username, created_at=now, role=role)

    def get_user(self, user_id: str) -> PlatformUser | None:
        with self._lock:
            row = self.conn.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return PlatformUser.from_mapping(dict(row)) if row else None

    def get_credentials(self, username: str) -> tuple[PlatformUser, str] | None:
        """返回 ``(用户, 密码哈希)``；用户名不存在返回 ``None``。"""
        with self._lock:
            row = self.conn.execute(
                f"SELECT {_USER_COLUMNS}, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return PlatformUser.from_mapping(data), str(data.get("password_hash") or "")

    def mark_login(self, user_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (_utcnow(), user_id)
            )
            self.conn.commit()

    def bind_erp(self, user_id: str, erp_username: str,
                 erp_user_id: str | None = None) -> PlatformUser | None:
        """绑定 ERP 账号（一个平台账号只存一个）。"""
        now = _utcnow()
        with self._lock:
            self.conn.execute(
                "UPDATE users SET erp_username = ?, erp_user_id = ?, erp_bound_at = ? "
                "WHERE id = ?",
                (erp_username, erp_user_id, now, user_id),
            )
            self.conn.commit()
        return self.get_user(user_id)

    # ---------- 登录会话 ----------
    def create_session(self, token_hash: str, user_id: str, expires_at: str,
                       ip: str | None = None) -> None:
        now = _utcnow()
        with self._lock:
            self.conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, "
                "last_seen, created_ip) VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash, user_id, now, expires_at, now, ip),
            )
            self.conn.commit()

    def get_session(self, token_hash: str) -> PlatformSession | None:
        """未过期才返回；顺带刷新 last_seen。"""
        if not token_hash:
            return None
        now = _utcnow()
        with self._lock:
            row = self.conn.execute(
                f"SELECT {_SESSION_COLUMNS} FROM sessions "
                "WHERE token_hash = ? AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, token_hash)
            )
            self.conn.commit()
        return PlatformSession.from_mapping(dict(row))

    def delete_session(self, token_hash: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
            )
            self.conn.commit()
            return cur.rowcount > 0

    def purge_expired_sessions(self) -> int:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (_utcnow(),)
            )
            self.conn.commit()
            return cur.rowcount

    # ---------- 平台账号名下的 token ----------
    def list_for_owner(self, owner_id: str, *,
                       include_revoked: bool = False) -> list[ApiKeyRecord]:
        sql = f"SELECT {_SELECT_COLUMNS} FROM api_keys WHERE owner_id = ?"
        if not include_revoked:
            sql += " AND revoked = 0"
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self.conn.execute(sql, (owner_id,)).fetchall()
        return [ApiKeyRecord.from_row(r) for r in rows]

    def revoke_owner_prefix(self, owner_id: str, prefix: str) -> bool:
        """按「id 前缀」撤销该账号名下的一把 token（id 是 key_hash 的前若干位）。"""
        prefix = (prefix or "").strip().lower()
        if len(prefix) < 8:
            return False
        with self._lock:
            cur = self.conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE owner_id = ? AND revoked = 0 "
                "AND key_hash LIKE ?",
                (owner_id, f"{prefix}%"),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def revoke_owner(self, owner_id: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE owner_id = ? AND revoked = 0",
                (owner_id,),
            )
            self.conn.commit()
            return cur.rowcount

    # ---------- 平台账号管理（管理员后台） ----------
    def update_user(self, user_id: str, *, role: str | None = None,
                    disabled: bool | None = None) -> PlatformUser | None:
        sets: list[str] = []
        params: list[object] = []
        if role is not None:
            sets.append("role = ?")
            params.append(role)
        if disabled is not None:
            sets.append("disabled = ?")
            params.append(1 if disabled else 0)
        if not sets:
            return self.get_user(user_id)
        params.append(user_id)
        with self._lock:
            self.conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
            self.conn.commit()
        return self.get_user(user_id)

    def list_users(self, *, query: str | None = None, limit: int = 50,
                   offset: int = 0) -> list[PlatformUser]:
        sql = f"SELECT {_USER_COLUMNS} FROM users"
        params: list[object] = []
        text = (query or "").strip()
        if text:
            sql += " WHERE username LIKE ? OR erp_username LIKE ?"
            params += [f"%{text}%", f"%{text}%"]
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [max(1, int(limit)), max(0, int(offset))]
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [PlatformUser.from_mapping(dict(r)) for r in rows]

    def count_users(self, *, query: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM users"
        params: list[object] = []
        text = (query or "").strip()
        if text:
            sql += " WHERE username LIKE ? OR erp_username LIKE ?"
            params += [f"%{text}%", f"%{text}%"]
        with self._lock:
            return int(self.conn.execute(sql, params).fetchone()[0])

    def unbind_erp(self, user_id: str) -> PlatformUser | None:
        with self._lock:
            self.conn.execute(
                "UPDATE users SET erp_username = NULL, erp_user_id = NULL, "
                "erp_bound_at = NULL WHERE id = ?",
                (user_id,),
            )
            self.conn.commit()
        return self.get_user(user_id)

    def count_sessions(self, user_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user_id,)
            ).fetchone()
        return int(row[0] if row else 0)

    def delete_user_sessions(self, user_id: str) -> int:
        with self._lock:
            cur = self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self.conn.commit()
            return cur.rowcount

    def delete_user(self, user_id: str) -> bool:
        """删除平台账号：会话直接删，token 置撤销（保留审计痕迹）。"""
        with self._lock:
            self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE owner_id = ? AND revoked = 0",
                (user_id,),
            )
            cur = self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.conn.commit()
            return cur.rowcount > 0

    # ---------- 审计日志 ----------
    def add_audit(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO audit_logs (created_at, action, result, actor_id, "
                "actor_name, actor_type, target_type, target_id, status_code, ip, "
                "user_agent, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _utcnow(),
                    entry["action"],
                    entry.get("result") or "success",
                    entry.get("actor_id"),
                    entry.get("actor_name"),
                    entry.get("actor_type") or "anonymous",
                    entry.get("target_type"),
                    entry.get("target_id"),
                    entry.get("status_code"),
                    entry.get("ip"),
                    entry.get("user_agent"),
                    json.dumps(entry.get("detail") or {}, ensure_ascii=False),
                ),
            )
            self.conn.commit()

    def list_audit(self, *, action: str | None = None, actor: str | None = None,
                   result: str | None = None, since: str | None = None,
                   until: str | None = None, limit: int = 100,
                   offset: int = 0) -> list[AuditEntry]:
        where: list[str] = []
        params: list[object] = []
        if action:
            where.append("action = ?")
            params.append(action)
        if result:
            where.append("result = ?")
            params.append(result)
        if actor:
            where.append("(actor_name LIKE ? OR actor_id LIKE ?)")
            params += [f"%{actor}%", f"%{actor}%"]
        if since:
            where.append("created_at >= ?")
            params.append(since)
        if until:
            where.append("created_at <= ?")
            params.append(until)
        sql = f"SELECT {_AUDIT_COLUMNS} FROM audit_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [max(1, int(limit)), max(0, int(offset))]
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [AuditEntry.from_mapping(dict(r)) for r in rows]

    # ---------- MCP 工具调用日志 ----------
    def add_call_log(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO mcp_call_logs (created_at, tool, status, duration_ms, "
                "actor_id, actor_name, actor_type, token_id, erp_username, ip, "
                "user_agent, error, arguments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _utcnow(),
                    entry["tool"],
                    entry.get("status") or "success",
                    int(entry.get("duration_ms") or 0),
                    entry.get("actor_id"),
                    entry.get("actor_name"),
                    entry.get("actor_type"),
                    entry.get("token_id"),
                    entry.get("erp_username"),
                    entry.get("ip"),
                    entry.get("user_agent"),
                    entry.get("error"),
                    json.dumps(entry.get("arguments") or {}, ensure_ascii=False),
                ),
            )
            self.conn.commit()

    def list_call_logs(self, *, tool: str | None = None, actor: str | None = None,
                       status: str | None = None, since: str | None = None,
                       until: str | None = None, limit: int = 100,
                       offset: int = 0) -> list[ToolCallLog]:
        where: list[str] = []
        params: list[object] = []
        if tool:
            where.append("tool LIKE ?")
            params.append(f"%{tool}%")
        if actor:
            where.append("(actor_name LIKE ? OR actor_id LIKE ? OR erp_username LIKE ?)")
            params += [f"%{actor}%", f"%{actor}%", f"%{actor}%"]
        if status:
            where.append("status = ?")
            params.append(status)
        if since:
            where.append("created_at >= ?")
            params.append(since)
        if until:
            where.append("created_at <= ?")
            params.append(until)
        sql = f"SELECT {_CALL_COLUMNS} FROM mcp_call_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [max(1, int(limit)), max(0, int(offset))]
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [ToolCallLog.from_mapping(dict(r)) for r in rows]

    def count_call_logs(self, *, actor_id: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM mcp_call_logs"
        params: list[object] = []
        if actor_id:
            sql += " WHERE actor_id = ?"
            params.append(actor_id)
        with self._lock:
            return int(self.conn.execute(sql, params).fetchone()[0])

    # ---------- ERP 密码密文（自动重登用） ----------
    def set_erp_secret(self, erp_username: str, secret: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO erp_credentials (erp_username, secret, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(erp_username) DO UPDATE SET "
                "secret = excluded.secret, updated_at = excluded.updated_at",
                (erp_username, secret, _utcnow()),
            )
            self.conn.commit()

    def get_erp_secret(self, erp_username: str) -> str | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT secret FROM erp_credentials WHERE erp_username = ?",
                (erp_username,),
            ).fetchone()
        return str(row["secret"]) if row else None

    def delete_erp_secret(self, erp_username: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM erp_credentials WHERE erp_username = ?", (erp_username,)
            )
            self.conn.commit()
            return bool(cur.rowcount)


__all__ = ["ApiKeyRecord", "ApiKeyStore", "AuthStore"]
