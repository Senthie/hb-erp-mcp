"""api-key 与 MCP 服务注册信息的 PostgreSQL 持久化（生产后端）。

与 SQLite 版 :class:`~erp_tools.runtime.auth_store.AuthStore` 保持同一套方法签名，
上层（中间件 / 路由）只依赖行为，不关心具体数据库。

设计要点与 SQLite 版一致：

* **不存 ERP 密码**，库里只有 ``sha256(api_key)``；
* api-key 没有 ``expires_at``，寿命等于它绑定的 ERP 会话；
* 同一账号允许多把 key 并存。

PostgreSQL 额外承担「MCP 服务信息注册」——见 :mod:`erp_tools.runtime.mcp_services`。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from ..core.errors import ConfigError
from ..core.security import generate_api_key, hash_api_key
from .audit import AuditEntry
from .auth_store import ApiKeyRecord
from .call_log import ToolCallLog
from .mcp_services import McpService
from .platform_users import ROLE_USER, PlatformSession, PlatformUser

try:  # pragma: no cover - 缺少驱动时给出可执行的提示
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        key_hash    TEXT PRIMARY KEY,
        username    TEXT NOT NULL,
        user_id     TEXT,
        owner_id    TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_used   TIMESTAMPTZ,
        created_ip  TEXT,
        revoked     BOOLEAN NOT NULL DEFAULT FALSE,
        note        TEXT
    )
    """,
    "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS owner_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys (username)",
    "CREATE INDEX IF NOT EXISTS idx_api_keys_owner ON api_keys (owner_id)",
    """
    CREATE TABLE IF NOT EXISTS mcp_services (
        id           BIGSERIAL PRIMARY KEY,
        name         TEXT NOT NULL UNIQUE,
        version      TEXT NOT NULL DEFAULT '',
        endpoint     TEXT,
        transport    TEXT NOT NULL DEFAULT 'streamable-http',
        environment  TEXT NOT NULL DEFAULT 'production',
        description  TEXT,
        owner        TEXT,
        tools        INTEGER,
        metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mcp_services_env ON mcp_services (environment)",
    """
    CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'user',
        disabled      BOOLEAN NOT NULL DEFAULT FALSE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_login_at TIMESTAMPTZ,
        erp_username  TEXT,
        erp_user_id   TEXT,
        erp_bound_at  TIMESTAMPTZ
    )
    """,
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled BOOLEAN NOT NULL DEFAULT FALSE",
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        last_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_ip TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)",
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id          BIGSERIAL PRIMARY KEY,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
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
        detail      JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs (action)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs (actor_id)",
    """
    CREATE TABLE IF NOT EXISTS mcp_call_logs (
        id           BIGSERIAL PRIMARY KEY,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
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
        arguments    JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_calls_created ON mcp_call_logs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_calls_tool ON mcp_call_logs (tool)",
    "CREATE INDEX IF NOT EXISTS idx_calls_actor ON mcp_call_logs (actor_id)",
)

#: 一条语句一次执行：psycopg 带参数时走扩展协议，不接受多语句拼接
_PASSWORD_IN_DSN = re.compile(r"(://[^:@/\s]+):[^/\s]*@")

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

_SERVICE_COLUMNS = (
    "id, name, version, endpoint, transport, environment, description, owner, "
    "tools, metadata, created_at, updated_at"
)


def mask_dsn(dsn: str) -> str:
    """把连接串里的密码打码，便于写进日志/错误信息。"""
    return _PASSWORD_IN_DSN.sub(r"\1:***@", dsn or "")


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(value)


def _to_record(row: dict[str, Any]) -> ApiKeyRecord:
    return ApiKeyRecord(
        key_hash=row["key_hash"],
        username=row["username"],
        user_id=row.get("user_id"),
        created_at=_iso(row.get("created_at")),
        last_used=_iso(row.get("last_used")) or None,
        created_ip=row.get("created_ip"),
        revoked=bool(row.get("revoked")),
        note=row.get("note"),
        owner_id=row.get("owner_id"),
    )


def _to_user(row: dict[str, Any]) -> PlatformUser:
    data = dict(row)
    data["created_at"] = _iso(data.get("created_at"))
    for key in ("last_login_at", "erp_bound_at"):
        data[key] = _iso(data.get(key)) or None
    return PlatformUser.from_mapping(data)


def _to_audit(row: dict[str, Any]) -> AuditEntry:
    data = dict(row)
    data["created_at"] = _iso(data.get("created_at"))
    return AuditEntry.from_mapping(data)


def _to_call(row: dict[str, Any]) -> ToolCallLog:
    data = dict(row)
    data["created_at"] = _iso(data.get("created_at"))
    return ToolCallLog.from_mapping(data)


def _to_session(row: dict[str, Any]) -> PlatformSession:
    data = dict(row)
    for key in ("created_at", "expires_at", "last_seen"):
        data[key] = _iso(data.get(key))
    return PlatformSession.from_mapping(data)


class PgStore:
    """`postgresql://` 库的唯一访问点。线程安全（单连接 + 互斥锁 + 断线重连）。"""

    def __init__(self, dsn: str, *, connect_timeout: int = 10) -> None:
        if not (dsn or "").strip():
            raise ConfigError("PostgreSQL 连接串（ERPSRV_PG_DSN）不能为空")
        self.dsn = dsn.strip()
        self.connect_timeout = max(1, int(connect_timeout))
        self._lock = threading.RLock()
        self._conn: Any = None

    # ---------- 连接 ----------
    @property
    def safe_dsn(self) -> str:
        return mask_dsn(self.dsn)

    def _connect(self):
        if psycopg is None:  # pragma: no cover
            raise ConfigError(
                "缺少 PostgreSQL 驱动：请执行 `uv sync --extra server`（会装上 psycopg）"
            )
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                self.dsn,
                autocommit=True,
                row_factory=dict_row,
                connect_timeout=self.connect_timeout,
                application_name="hb-mcp",
            )
            for statement in _SCHEMA_STATEMENTS:
                self._conn.execute(statement)
        return self._conn

    def _reset(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - 关连接失败无所谓
                pass

    def _run(self, fn):
        """在锁内执行 ``fn(conn)``；遇到连接层错误重连并重试一次。"""
        with self._lock:
            try:
                return fn(self._connect())
            except Exception as exc:
                if psycopg is None or not isinstance(exc, psycopg.OperationalError):
                    raise
                self._reset()
                return fn(self._connect())

    # ---------- 生命周期 ----------
    def init_schema(self) -> "PgStore":
        try:
            self._run(lambda conn: conn.execute("SELECT 1").fetchone())
        except Exception as exc:  # noqa: BLE001 - 统一转成配置错误，启动时好读
            raise ConfigError(
                f"连不上 PostgreSQL（{self.safe_dsn}）：{exc}"
            ) from exc
        return self

    def close(self) -> None:
        with self._lock:
            self._reset()

    def __enter__(self) -> "PgStore":
        return self.init_schema()

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- api-key：写入 ----------
    def issue(self, username: str, *, user_id: str | None = None,
              ip: str | None = None, note: str | None = None,
              owner_id: str | None = None) -> str:
        raw = generate_api_key()
        self._run(lambda conn: conn.execute(
            "INSERT INTO api_keys (key_hash, username, user_id, owner_id, created_ip, "
            "note) VALUES (%s, %s, %s, %s, %s, %s)",
            (hash_api_key(raw), username, user_id, owner_id, ip, note),
        ))
        return raw

    def touch(self, key_hash: str, *, min_interval: float = 0.0) -> None:
        def op(conn):
            if min_interval > 0:
                row = conn.execute(
                    "SELECT last_used FROM api_keys WHERE key_hash = %s", (key_hash,)
                ).fetchone()
                last = (row or {}).get("last_used")
                if isinstance(last, datetime):
                    age = (datetime.now(timezone.utc) - last.astimezone(timezone.utc))
                    if age.total_seconds() < min_interval:
                        return
            conn.execute(
                "UPDATE api_keys SET last_used = now() WHERE key_hash = %s", (key_hash,)
            )

        self._run(op)

    def set_user_id(self, key_hash: str, user_id: str | None) -> None:
        self._run(lambda conn: conn.execute(
            "UPDATE api_keys SET user_id = %s WHERE key_hash = %s", (user_id, key_hash)
        ))

    # ---------- api-key：读取 ----------
    def resolve(self, raw_key: str | None) -> ApiKeyRecord | None:
        if not raw_key:
            return None
        row = self._run(lambda conn: conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = %s AND revoked = FALSE",
            (hash_api_key(raw_key),),
        ).fetchone())
        return _to_record(row) if row else None

    def resolve_any(self, raw_key: str | None) -> ApiKeyRecord | None:
        if not raw_key:
            return None
        row = self._run(lambda conn: conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = %s", (hash_api_key(raw_key),)
        ).fetchone())
        return _to_record(row) if row else None

    def get_by_hash(self, key_hash: str) -> ApiKeyRecord | None:
        row = self._run(lambda conn: conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = %s", (key_hash,)
        ).fetchone())
        return _to_record(row) if row else None

    def list_for_user(self, username: str, *, include_revoked: bool = False
                      ) -> list[ApiKeyRecord]:
        sql = "SELECT * FROM api_keys WHERE username = %s"
        if not include_revoked:
            sql += " AND revoked = FALSE"
        sql += " ORDER BY created_at DESC"
        rows = self._run(lambda conn: conn.execute(sql, (username,)).fetchall())
        return [_to_record(r) for r in rows]

    def count(self, *, include_revoked: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM api_keys"
        if not include_revoked:
            sql += " WHERE revoked = FALSE"
        row = self._run(lambda conn: conn.execute(sql).fetchone())
        return int((row or {}).get("n") or 0)

    # ---------- api-key：撤销 ----------
    def revoke(self, raw_key: str) -> bool:
        return self.revoke_hash(hash_api_key(raw_key or ""))

    def revoke_hash(self, key_hash: str) -> bool:
        row = self._run(lambda conn: conn.execute(
            "UPDATE api_keys SET revoked = TRUE "
            "WHERE key_hash = %s AND revoked = FALSE RETURNING key_hash",
            (key_hash,),
        ).fetchone())
        return bool(row)

    def revoke_user(self, username: str) -> int:
        rows = self._run(lambda conn: conn.execute(
            "UPDATE api_keys SET revoked = TRUE "
            "WHERE username = %s AND revoked = FALSE RETURNING key_hash",
            (username,),
        ).fetchall())
        return len(rows)

    # ---------- MCP 服务注册信息 ----------
    def register_service(self, values: dict[str, Any]) -> McpService:
        """按 ``name`` 幂等写入：同名服务重复注册＝更新元数据。"""
        row = self._run(lambda conn: conn.execute(
            "INSERT INTO mcp_services "
            "(name, version, endpoint, transport, environment, description, owner, "
            " tools, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
            "ON CONFLICT (name) DO UPDATE SET "
            "version = EXCLUDED.version, endpoint = EXCLUDED.endpoint, "
            "transport = EXCLUDED.transport, environment = EXCLUDED.environment, "
            "description = EXCLUDED.description, owner = EXCLUDED.owner, "
            "tools = EXCLUDED.tools, metadata = EXCLUDED.metadata, "
            "updated_at = now() "
            f"RETURNING {_SERVICE_COLUMNS}",
            (
                values["name"], values.get("version") or "", values.get("endpoint"),
                values.get("transport") or "streamable-http",
                values.get("environment") or "production",
                values.get("description"), values.get("owner"), values.get("tools"),
                values.get("metadata") or "{}",
            ),
        ).fetchone())
        return McpService.from_mapping(row or {"name": values["name"]})

    def get_service(self, name: str) -> McpService | None:
        row = self._run(lambda conn: conn.execute(
            f"SELECT {_SERVICE_COLUMNS} FROM mcp_services WHERE name = %s", (name,)
        ).fetchone())
        return McpService.from_mapping(row) if row else None

    def list_services(self, *, limit: int = 50) -> list[McpService]:
        rows = self._run(lambda conn: conn.execute(
            f"SELECT {_SERVICE_COLUMNS} FROM mcp_services "
            "ORDER BY updated_at DESC LIMIT %s",
            (max(1, int(limit)),),
        ).fetchall())
        return [McpService.from_mapping(r) for r in rows]

    # ---------- 平台账号 ----------
    def create_user(self, username: str, password_hash: str, *,
                    role: str = ROLE_USER) -> PlatformUser | None:
        """新建平台账号；用户名已存在（唯一约束）时返回 ``None``。"""
        user_id = uuid.uuid4().hex
        row = self._run(lambda conn: conn.execute(
            "INSERT INTO users (id, username, password_hash, role) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (username) DO NOTHING "
            f"RETURNING {_USER_COLUMNS}",
            (user_id, username, password_hash, role),
        ).fetchone())
        return _to_user(row) if row else None

    def get_user(self, user_id: str) -> PlatformUser | None:
        row = self._run(lambda conn: conn.execute(
            f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user_id,)
        ).fetchone())
        return _to_user(row) if row else None

    def get_credentials(self, username: str) -> tuple[PlatformUser, str] | None:
        row = self._run(lambda conn: conn.execute(
            f"SELECT {_USER_COLUMNS}, password_hash FROM users WHERE username = %s",
            (username,),
        ).fetchone())
        if row is None:
            return None
        return _to_user(row), str(row.get("password_hash") or "")

    def mark_login(self, user_id: str) -> None:
        self._run(lambda conn: conn.execute(
            "UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,)
        ))

    def bind_erp(self, user_id: str, erp_username: str,
                 erp_user_id: str | None = None) -> PlatformUser | None:
        row = self._run(lambda conn: conn.execute(
            "UPDATE users SET erp_username = %s, erp_user_id = %s, "
            "erp_bound_at = now() WHERE id = %s "
            f"RETURNING {_USER_COLUMNS}",
            (erp_username, erp_user_id, user_id),
        ).fetchone())
        return _to_user(row) if row else None

    # ---------- 登录会话 ----------
    def create_session(self, token_hash: str, user_id: str, expires_at: str,
                       ip: str | None = None) -> None:
        self._run(lambda conn: conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_ip) "
            "VALUES (%s, %s, %s::timestamptz, %s)",
            (token_hash, user_id, expires_at, ip),
        ))

    def get_session(self, token_hash: str) -> PlatformSession | None:
        """未过期才返回；顺带刷新 last_seen。"""
        if not token_hash:
            return None
        row = self._run(lambda conn: conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM sessions "
            "WHERE token_hash = %s AND expires_at > now()",
            (token_hash,),
        ).fetchone())
        if row is None:
            return None
        self._run(lambda conn: conn.execute(
            "UPDATE sessions SET last_seen = now() WHERE token_hash = %s", (token_hash,)
        ))
        return _to_session(row)

    def delete_session(self, token_hash: str) -> bool:
        row = self._run(lambda conn: conn.execute(
            "DELETE FROM sessions WHERE token_hash = %s RETURNING token_hash",
            (token_hash,),
        ).fetchone())
        return bool(row)

    def purge_expired_sessions(self) -> int:
        rows = self._run(lambda conn: conn.execute(
            "DELETE FROM sessions WHERE expires_at <= now() RETURNING token_hash"
        ).fetchall())
        return len(rows)

    # ---------- 平台账号名下的 token ----------
    def list_for_owner(self, owner_id: str, *,
                       include_revoked: bool = False) -> list[ApiKeyRecord]:
        sql = "SELECT * FROM api_keys WHERE owner_id = %s"
        if not include_revoked:
            sql += " AND revoked = FALSE"
        sql += " ORDER BY created_at DESC"
        rows = self._run(lambda conn: conn.execute(sql, (owner_id,)).fetchall())
        return [_to_record(r) for r in rows]

    def revoke_owner_prefix(self, owner_id: str, prefix: str) -> bool:
        """按「id 前缀」撤销该账号名下的一把 token（id 是 key_hash 的前若干位）。"""
        prefix = (prefix or "").strip().lower()
        if len(prefix) < 8:
            return False
        row = self._run(lambda conn: conn.execute(
            "UPDATE api_keys SET revoked = TRUE "
            "WHERE owner_id = %s AND revoked = FALSE AND key_hash LIKE %s "
            "RETURNING key_hash",
            (owner_id, f"{prefix}%"),
        ).fetchone())
        return bool(row)

    def revoke_owner(self, owner_id: str) -> int:
        rows = self._run(lambda conn: conn.execute(
            "UPDATE api_keys SET revoked = TRUE "
            "WHERE owner_id = %s AND revoked = FALSE RETURNING key_hash",
            (owner_id,),
        ).fetchall())
        return len(rows)

    # ---------- 平台账号管理（管理员后台） ----------
    def update_user(self, user_id: str, *, role: str | None = None,
                    disabled: bool | None = None) -> PlatformUser | None:
        sets: list[str] = []
        params: list[Any] = []
        if role is not None:
            sets.append("role = %s")
            params.append(role)
        if disabled is not None:
            sets.append("disabled = %s")
            params.append(bool(disabled))
        if not sets:
            return self.get_user(user_id)
        params.append(user_id)
        row = self._run(lambda conn: conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE id = %s "
            f"RETURNING {_USER_COLUMNS}",
            tuple(params),
        ).fetchone())
        return _to_user(row) if row else None

    def list_users(self, *, query: str | None = None, limit: int = 50,
                   offset: int = 0) -> list[PlatformUser]:
        sql = f"SELECT {_USER_COLUMNS} FROM users"
        params: list[Any] = []
        text = (query or "").strip()
        if text:
            sql += " WHERE username ILIKE %s OR erp_username ILIKE %s"
            params += [f"%{text}%", f"%{text}%"]
        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params += [max(1, int(limit)), max(0, int(offset))]
        rows = self._run(lambda conn: conn.execute(sql, tuple(params)).fetchall())
        return [_to_user(r) for r in rows]

    def count_users(self, *, query: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM users"
        params: list[Any] = []
        text = (query or "").strip()
        if text:
            sql += " WHERE username ILIKE %s OR erp_username ILIKE %s"
            params += [f"%{text}%", f"%{text}%"]
        row = self._run(lambda conn: conn.execute(sql, tuple(params)).fetchone())
        return int((row or {}).get("n") or 0)

    def unbind_erp(self, user_id: str) -> PlatformUser | None:
        row = self._run(lambda conn: conn.execute(
            "UPDATE users SET erp_username = NULL, erp_user_id = NULL, "
            "erp_bound_at = NULL WHERE id = %s "
            f"RETURNING {_USER_COLUMNS}",
            (user_id,),
        ).fetchone())
        return _to_user(row) if row else None

    def count_sessions(self, user_id: str) -> int:
        row = self._run(lambda conn: conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id = %s", (user_id,)
        ).fetchone())
        return int((row or {}).get("n") or 0)

    def delete_user_sessions(self, user_id: str) -> int:
        rows = self._run(lambda conn: conn.execute(
            "DELETE FROM sessions WHERE user_id = %s RETURNING token_hash", (user_id,)
        ).fetchall())
        return len(rows)

    def delete_user(self, user_id: str) -> bool:
        """删除平台账号：会话直接删，token 置撤销（保留审计痕迹）。"""

        def op(conn):
            with conn.transaction():
                conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
                conn.execute(
                    "UPDATE api_keys SET revoked = TRUE "
                    "WHERE owner_id = %s AND revoked = FALSE",
                    (user_id,),
                )
                return conn.execute(
                    "DELETE FROM users WHERE id = %s RETURNING id", (user_id,)
                ).fetchone()

        return bool(self._run(op))

    # ---------- 审计日志 ----------
    def add_audit(self, entry: dict[str, Any]) -> None:
        self._run(lambda conn: conn.execute(
            "INSERT INTO audit_logs (action, result, actor_id, actor_name, actor_type, "
            "target_type, target_id, status_code, ip, user_agent, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            (
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
        ))

    def list_audit(self, *, action: str | None = None, actor: str | None = None,
                   result: str | None = None, since: str | None = None,
                   until: str | None = None, limit: int = 100,
                   offset: int = 0) -> list[AuditEntry]:
        where: list[str] = []
        params: list[Any] = []
        if action:
            where.append("action = %s")
            params.append(action)
        if result:
            where.append("result = %s")
            params.append(result)
        if actor:
            where.append("(actor_name ILIKE %s OR actor_id ILIKE %s)")
            params += [f"%{actor}%", f"%{actor}%"]
        if since:
            where.append("created_at >= %s::timestamptz")
            params.append(since)
        if until:
            where.append("created_at <= %s::timestamptz")
            params.append(until)
        sql = f"SELECT {_AUDIT_COLUMNS} FROM audit_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
        params += [max(1, int(limit)), max(0, int(offset))]
        rows = self._run(lambda conn: conn.execute(sql, tuple(params)).fetchall())
        return [_to_audit(r) for r in rows]

    # ---------- MCP 工具调用日志 ----------
    def add_call_log(self, entry: dict[str, Any]) -> None:
        self._run(lambda conn: conn.execute(
            "INSERT INTO mcp_call_logs (tool, status, duration_ms, actor_id, "
            "actor_name, actor_type, token_id, erp_username, ip, user_agent, "
            "error, arguments) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            (
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
        ))

    def list_call_logs(self, *, tool: str | None = None, actor: str | None = None,
                       status: str | None = None, since: str | None = None,
                       until: str | None = None, limit: int = 100,
                       offset: int = 0) -> list[ToolCallLog]:
        where: list[str] = []
        params: list[Any] = []
        if tool:
            where.append("tool ILIKE %s")
            params.append(f"%{tool}%")
        if actor:
            where.append(
                "(actor_name ILIKE %s OR actor_id ILIKE %s OR erp_username ILIKE %s)"
            )
            params += [f"%{actor}%", f"%{actor}%", f"%{actor}%"]
        if status:
            where.append("status = %s")
            params.append(status)
        if since:
            where.append("created_at >= %s::timestamptz")
            params.append(since)
        if until:
            where.append("created_at <= %s::timestamptz")
            params.append(until)
        sql = f"SELECT {_CALL_COLUMNS} FROM mcp_call_logs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
        params += [max(1, int(limit)), max(0, int(offset))]
        rows = self._run(lambda conn: conn.execute(sql, tuple(params)).fetchall())
        return [_to_call(r) for r in rows]

    def count_call_logs(self, *, actor_id: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM mcp_call_logs"
        params: list[Any] = []
        if actor_id:
            sql += " WHERE actor_id = %s"
            params.append(actor_id)
        row = self._run(lambda conn: conn.execute(sql, tuple(params)).fetchone())
        return int((row or {}).get("n") or 0)

    # ---------- 元信息 ----------
    def describe(self) -> dict[str, str]:
        from urllib.parse import urlsplit

        parts = urlsplit(self.dsn)
        return {
            "backend": "postgresql",
            "host": parts.hostname or "",
            "port": str(parts.port or 5432),
            "database": (parts.path or "/").lstrip("/"),
        }


__all__ = ["PgStore", "mask_dsn"]
