"""API Key 持久化（SQLite）。

设计要点：

* **不存 ERP 密码**。登录只用于换取 cookie，密码不进库、不进日志。
* 库里只有 ``sha256(api_key)``，没有明文 key。
* **没有 expires_at**：key 的有效期等于它绑定的 ERP 会话。
  会话一死，调用方探测到就把这一行置 ``revoked=1``（保留痕迹，便于审计）。
* 同一账号允许多把 key 并存（多设备/多客户端）。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..core.security import generate_api_key, hash_api_key

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS api_keys (
    key_hash    TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    user_id     TEXT,
    created_at  TEXT NOT NULL,
    last_used   TEXT,
    created_ip  TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(username);
"""

_SELECT_COLUMNS = (
    "key_hash, username, user_id, created_at, last_used, created_ip, revoked, note"
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

    @classmethod
    def from_row(cls, row: sqlite3.Row | Iterable) -> "ApiKeyRecord":
        (key_hash, username, user_id, created_at,
         last_used, created_ip, revoked, note) = tuple(row)
        return cls(
            key_hash=key_hash,
            username=username,
            user_id=user_id,
            created_at=created_at or "",
            last_used=last_used,
            created_ip=created_ip,
            revoked=bool(revoked),
            note=note,
        )


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
            self._conn.commit()
        return self._conn

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
              ip: str | None = None, note: str | None = None) -> str:
        """签发一把新 key，返回**明文**（调用方只能保存这一次）。"""
        raw = generate_api_key()
        with self._lock:
            self.conn.execute(
                "INSERT INTO api_keys "
                "(key_hash, username, user_id, created_at, created_ip, revoked, note) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (hash_api_key(raw), username, user_id, _utcnow(), ip, note),
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


__all__ = ["ApiKeyRecord", "AuthStore"]
