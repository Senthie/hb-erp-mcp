"""平台账号与登录会话（HB-MCP 自己的用户体系）。

和 ERP 账号是两层东西：

* **平台账号**：登录本平台用的用户名 + 密码（scrypt 哈希入库），一个账号只能绑定
  **一个** ERP 账号（绑定成功后签发 MCP token）。
* **ERP 账号**：真正访问华博 ERP 的工号；密码只在绑定那一次请求里用，不落盘。

会话 token 只放在 HttpOnly cookie 里，库里存 sha256。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

#: 平台登录名的长度与字符限制（小写归一后入库）
MIN_USERNAME = 3
MAX_USERNAME = 32
MIN_PASSWORD = 8
MAX_PASSWORD = 128
#: 登录会话有效期：7 天
SESSION_TTL_SECONDS = 7 * 24 * 3600

#: 平台账号角色
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLES = (ROLE_USER, ROLE_ADMIN)

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def normalize_username(raw: str | None) -> str:
    """归一化并校验平台登录名；非法抛 ``ValueError``（中文消息）。"""
    text = (raw or "").strip().lower()
    if not text:
        raise ValueError("用户名必填")
    if len(text) < MIN_USERNAME or len(text) > MAX_USERNAME:
        raise ValueError(f"用户名长度需在 {MIN_USERNAME}~{MAX_USERNAME} 之间")
    if not _USERNAME_RE.match(text):
        raise ValueError("用户名只能包含字母、数字、点、下划线和短横线，且需以字母或数字开头")
    return text


def validate_password(raw: str | None) -> str:
    """校验平台密码（不做复杂度洁癖，只要求长度）。"""
    text = raw or ""
    if len(text) < MIN_PASSWORD:
        raise ValueError(f"密码至少 {MIN_PASSWORD} 位")
    if len(text) > MAX_PASSWORD:
        raise ValueError(f"密码最长 {MAX_PASSWORD} 位")
    return text


@dataclass(frozen=True)
class PlatformUser:
    """平台账号。``erp_username`` 非空表示已绑定 ERP 账号。"""

    id: str
    username: str
    created_at: str = ""
    last_login_at: str | None = None
    erp_username: str | None = None
    erp_user_id: str | None = None
    erp_bound_at: str | None = None
    #: `user` / `admin`
    role: str = ROLE_USER
    #: 禁用后无法登录，已有会话立即失效
    disabled: bool = False

    @property
    def erp_bound(self) -> bool:
        return bool(self.erp_username)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "PlatformUser":
        return cls(
            id=str(row.get("id") or ""),
            username=str(row.get("username") or ""),
            created_at=str(row.get("created_at") or ""),
            last_login_at=row.get("last_login_at"),
            erp_username=row.get("erp_username"),
            erp_user_id=row.get("erp_user_id"),
            erp_bound_at=row.get("erp_bound_at"),
            role=str(row.get("role") or ROLE_USER),
            disabled=bool(row.get("disabled")),
        )

    def to_dict(self) -> dict[str, Any]:
        """对外形态：**绝不包含密码哈希**。"""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_admin": self.is_admin,
            "disabled": self.disabled,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "erp": {
                "bound": self.erp_bound,
                "username": self.erp_username,
                "user_id": self.erp_user_id,
                "bound_at": self.erp_bound_at,
            },
        }


@dataclass(frozen=True)
class PlatformSession:
    """一次浏览器登录会话（库里只有 token 的 sha256）。"""

    token_hash: str
    user_id: str
    created_at: str = ""
    expires_at: str = ""
    last_seen: str = ""
    created_ip: str | None = None

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "PlatformSession":
        return cls(
            token_hash=str(row.get("token_hash") or ""),
            user_id=str(row.get("user_id") or ""),
            created_at=str(row.get("created_at") or ""),
            expires_at=str(row.get("expires_at") or ""),
            last_seen=str(row.get("last_seen") or ""),
            created_ip=row.get("created_ip"),
        )


def expiry_from_now(ttl_seconds: int = SESSION_TTL_SECONDS) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=max(60, int(ttl_seconds)))


def public_token(record) -> dict[str, Any]:
    """把 API Key 记录转成「可给前端看」的形态：token 只给 id 前缀，明文永不再现。"""
    key_hash = getattr(record, "key_hash", "") or ""
    return {
        "id": key_hash[:16],
        "label": getattr(record, "note", None) or "",
        "erp_username": getattr(record, "username", ""),
        "created_at": getattr(record, "created_at", ""),
        "last_used": getattr(record, "last_used", None),
        "revoked": bool(getattr(record, "revoked", False)),
    }


__all__ = [
    "MAX_PASSWORD",
    "MAX_USERNAME",
    "MIN_PASSWORD",
    "MIN_USERNAME",
    "ROLES",
    "ROLE_ADMIN",
    "ROLE_USER",
    "SESSION_TTL_SECONDS",
    "PlatformSession",
    "PlatformUser",
    "expiry_from_now",
    "normalize_username",
    "public_token",
    "validate_password",
]
