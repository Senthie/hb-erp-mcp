"""MCP 工具调用日志：谁、用哪把 token、调了哪个工具、耗时、成功与否。

和 `audit_logs`（账号/管理动作）分开存：

* `audit_logs`  —— 低频、审计向（登录、绑定、改用户…）
* `mcp_call_logs` —— 高频、可观测向（每个 MCP 工具调用一行）

写入失败**绝不能**影响工具本身（调用方统一 try/except）。
参数只留「定位用」的摘要，密码 / token / cookie 一律打码或丢弃。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..core.security import scrub

#: 单个参数值最多保留多少字符
MAX_ARG_CHARS = 160
#: 最多保留多少个参数
MAX_ARGS = 12
#: 错误信息最多保留多少字符
MAX_ERROR_CHARS = 500

#: 这些键一律不落库（连打码都不留）
SENSITIVE_KEYS = (
    "password", "passwd", "pwd", "secret", "token", "apikey", "api_key",
    "authorization", "cookie", "session", "credential",
)

STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"


def sanitize_arguments(arguments: dict[str, Any] | None) -> dict[str, str]:
    """把工具入参压成一份可安全入库的摘要。"""
    out: dict[str, str] = {}
    for key, value in list((arguments or {}).items())[:MAX_ARGS]:
        name = str(key)
        lowered = name.lower()
        if any(flag in lowered for flag in SENSITIVE_KEYS):
            out[name] = "***"
            continue
        if value is None or value == "":
            continue
        text = scrub(str(value))
        if len(text) > MAX_ARG_CHARS:
            text = text[:MAX_ARG_CHARS] + "…"
        out[name] = text
    return out


def build_call_log(
    tool: str,
    *,
    status: str = STATUS_SUCCESS,
    duration_ms: int = 0,
    actor_id: str | None = None,
    actor_name: str | None = None,
    actor_type: str = "api_key",
    token_id: str | None = None,
    erp_username: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    error: str | None = None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error_text = scrub(error or "").strip()[:MAX_ERROR_CHARS]
    return {
        "tool": tool,
        "status": status,
        "duration_ms": max(0, int(duration_ms)),
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_type": actor_type,
        "token_id": token_id,
        "erp_username": erp_username,
        "ip": ip,
        "user_agent": (user_agent or "")[:256] or None,
        "error": error_text or None,
        "arguments": sanitize_arguments(arguments),
    }


@dataclass(frozen=True)
class ToolCallLog:
    id: int | None = None
    created_at: str = ""
    tool: str = ""
    status: str = STATUS_SUCCESS
    duration_ms: int = 0
    actor_id: str | None = None
    actor_name: str | None = None
    actor_type: str = "api_key"
    token_id: str | None = None
    erp_username: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    error: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "ToolCallLog":
        raw_args = row.get("arguments")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except ValueError:
                raw_args = {}
        if not isinstance(raw_args, dict):
            raw_args = {}
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            created_at=str(row.get("created_at") or ""),
            tool=str(row.get("tool") or ""),
            status=str(row.get("status") or STATUS_SUCCESS),
            duration_ms=int(row.get("duration_ms") or 0),
            actor_id=row.get("actor_id"),
            actor_name=row.get("actor_name"),
            actor_type=str(row.get("actor_type") or "api_key"),
            token_id=row.get("token_id"),
            erp_username=row.get("erp_username"),
            ip=row.get("ip"),
            user_agent=row.get("user_agent"),
            error=row.get("error"),
            arguments=dict(raw_args),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "tool": self.tool,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "actor_type": self.actor_type,
            "token_id": self.token_id,
            "erp_username": self.erp_username,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "error": self.error,
            "arguments": self.arguments,
        }


__all__ = [
    "MAX_ARG_CHARS",
    "MAX_ARGS",
    "SENSITIVE_KEYS",
    "STATUS_FAILURE",
    "STATUS_SUCCESS",
    "ToolCallLog",
    "build_call_log",
    "sanitize_arguments",
]
