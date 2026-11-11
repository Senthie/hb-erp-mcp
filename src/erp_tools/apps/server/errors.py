"""HTTP 错误模型：所有失败都以 ``{error, message, hint?, ...}`` 返回。

这样 MCP 客户端、CLI、curl 拿到的错误形状一致，便于自动处理。
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """可安全暴露给调用方的错误。"""

    status: int = 400
    code: str = "error"

    def __init__(self, message: str = "", *, code: str | None = None,
                 status: int | None = None, hint: str | None = None,
                 headers: dict[str, str] | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(message or code or self.code)
        self.message = message
        if code:
            self.code = code
        if status is not None:
            self.status = status
        self.hint = hint
        self.headers = dict(headers or {})
        self.extra = dict(extra or {})

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code}
        if self.message:
            body["message"] = self.message
        if self.hint:
            body["hint"] = self.hint
        body.update(self.extra)
        return body


class BadRequest(ApiError):
    status = 400
    code = "bad_request"


class Unauthorized(ApiError):
    status = 401
    code = "unauthorized"

    def __init__(self, message: str = "", *, code: str | None = None, **kwargs) -> None:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("WWW-Authenticate", "Bearer")
        super().__init__(message, code=code, headers=headers, **kwargs)


class Forbidden(ApiError):
    status = 403
    code = "forbidden"


class NotFound(ApiError):
    status = 404
    code = "not_found"


class Locked(ApiError):
    status = 423
    code = "locked"

    def __init__(self, retry_after: float, message: str = "") -> None:
        seconds = max(1, int(round(retry_after)))
        super().__init__(
            message or f"连续登录失败次数过多，请 {seconds} 秒后再试",
            headers={"Retry-After": str(seconds)},
            extra={"retry_after": seconds},
        )


class RateLimited(ApiError):
    status = 429
    code = "rate_limited"

    def __init__(self, retry_after: float, message: str = "") -> None:
        seconds = max(1, int(round(retry_after)))
        super().__init__(
            message or f"请求过于频繁，请 {seconds} 秒后重试",
            headers={"Retry-After": str(seconds)},
            extra={"retry_after": seconds},
        )


class UpstreamFailure(ApiError):
    """ERP 侧不可达/报错。"""

    status = 502
    code = "erp_unreachable"


class InternalFailure(ApiError):
    status = 500
    code = "internal_error"


__all__ = [
    "ApiError", "BadRequest", "Forbidden", "InternalFailure", "Locked", "NotFound",
    "RateLimited", "Unauthorized", "UpstreamFailure",
]
