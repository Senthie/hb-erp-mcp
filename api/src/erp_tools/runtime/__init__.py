"""跨入口（MCP / CLI / HTTP）共享的运行时。

* `auth_store`  API Key 的持久化（SQLite）
* `context`    当前请求绑定的身份（contextvars）
* `sessions`   ERP 会话池 + 全局并发闸门
"""

from .auth_store import ApiKeyRecord, AuthStore
from .context import current_record, require_record, reset_current, set_current
from .sessions import SessionExpired, SessionProvider, UserSession

__all__ = [
    "ApiKeyRecord",
    "AuthStore",
    "SessionExpired",
    "SessionProvider",
    "UserSession",
    "current_record",
    "require_record",
    "reset_current",
    "set_current",
]
