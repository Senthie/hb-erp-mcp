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


def require_record() -> ApiKeyRecord:
    record = current_record.get()
    if record is None:
        raise Unauthenticated("缺少身份上下文（请求未通过 api-key 校验）")
    return record


__all__ = [
    "Unauthenticated",
    "current",
    "current_record",
    "require_record",
    "reset_current",
    "set_current",
]
