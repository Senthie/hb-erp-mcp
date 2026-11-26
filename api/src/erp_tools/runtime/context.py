"""请求上下文：把「当前这次调用是谁」传给工具实现。

用 contextvars 而不是全局变量，是为了让同一个进程内的并发请求互不串号。

注意：**中间件必须用裸 ASGI 写**。Starlette 的 `BaseHTTPMiddleware` 会让
下游跑在独立任务里，contextvar 的写入方向不保证，容易「读到 None」。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from .auth_store import ApiKeyRecord

current_record: ContextVar[ApiKeyRecord | None] = ContextVar(
    "erp_current_api_key", default=None
)

#: 本次请求的网关基地址（`scheme://host[:port]`），由认证中间件写入。
#: 工具里生成「附件代理直链」时用它，这样客户端用哪个地址连进来就回哪个地址。
current_base: ContextVar[str] = ContextVar("erp_current_base", default="")

#: 本次请求的客户端 IP / User-Agent（MCP 调用日志用）
current_ip: ContextVar[str] = ContextVar("erp_current_ip", default="")
current_user_agent: ContextVar[str] = ContextVar("erp_current_user_agent", default="")


class Unauthenticated(RuntimeError):
    """需要身份但没有身份（应映射成 401）。"""


def set_current(record: ApiKeyRecord | None) -> Token:
    return current_record.set(record)


def reset_current(token: Token) -> None:
    try:
        current_record.reset(token)
    except (ValueError, LookupError):  # 跨任务重设时可能失败，忽略即可
        pass


def current() -> ApiKeyRecord | None:
    return current_record.get()


def set_base(url: str) -> Token:
    return current_base.set((url or "").rstrip("/"))


def reset_base(token: Token) -> None:
    try:
        current_base.reset(token)
    except (ValueError, LookupError):
        pass


def set_request_meta(ip: str, user_agent: str) -> tuple[Token, Token]:
    return current_ip.set(ip or ""), current_user_agent.set(user_agent or "")


def reset_request_meta(tokens: tuple[Token, Token]) -> None:
    for token in tokens:
        try:
            (current_ip if token.var is current_ip else current_user_agent).reset(token)
        except (ValueError, LookupError):
            pass


def request_ip() -> str:
    return current_ip.get()


def request_user_agent() -> str:
    return current_user_agent.get()


def base_url() -> str:
    """当前请求的网关基地址；不在请求上下文里时返回空串。"""
    return current_base.get()


def require_record() -> ApiKeyRecord:
    record = current_record.get()
    if record is None:
        raise Unauthenticated("缺少身份上下文（请求未通过 api-key 校验）")
    return record


__all__ = [
    "Unauthenticated",
    "base_url",
    "current",
    "current_base",
    "current_ip",
    "current_record",
    "current_user_agent",
    "request_ip",
    "request_user_agent",
    "require_record",
    "reset_base",
    "reset_current",
    "reset_request_meta",
    "set_base",
    "set_current",
    "set_request_meta",
]
