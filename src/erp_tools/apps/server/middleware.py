"""API Key 认证中间件（裸 ASGI）。

为什么不用 Starlette 的 `BaseHTTPMiddleware` 或 `@app.middleware("http")`：
它们会让下游应用跑在独立任务里，contextvar 的可见性不保证，容易出现
「中间件里设置了身份，工具里读到 None」。裸 ASGI 是同一个协程调用链，稳。

放行清单（无需认证）：`/healthz`、`/v1/auth/login`、以及任何 `OPTIONS` 预检。
"""

from __future__ import annotations

import json
from typing import Any

import anyio

from ...core.security import is_well_formed_api_key
from ...runtime.auth_store import AuthStore
from ...runtime.context import reset_current, set_current

PUBLIC_PATHS = frozenset({"/healthz", "/v1/auth/login"})
PUBLIC_PREFIXES = ("/docs", "/openapi.json")


def bearer_from_headers(headers: list[tuple[bytes, bytes]]) -> str | None:
    """从 ASGI headers 里取 `Authorization: Bearer xxx`。"""
    for name, value in headers:
        if name.lower() == b"authorization":
            text = value.decode("latin-1").strip()
            scheme, _, token = text.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


async def send_json(send: Any, status: int, payload: dict,
                    headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    raw_headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    for key, value in (headers or []):
        raw_headers.append((key, value))
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})


def _is_public(method: str, path: str) -> bool:
    if method.upper() == "OPTIONS":
        return True
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


class BearerAuthMiddleware:
    """把 api-key 解析成 `ApiKeyRecord` 并写入 contextvar。"""

    def __init__(self, app, *, store: AuthStore) -> None:
        self.app = app
        self.store = store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if _is_public(method, path):
            await self.app(scope, receive, send)
            return

        raw = bearer_from_headers(scope.get("headers") or [])
        if not raw:
            await send_json(send, 401, {
                "error": "missing_bearer",
                "message": "缺少 Authorization: Bearer <api-key>",
                "hint": "先调用 POST /v1/auth/login 获取 api-key",
            }, [(b"www-authenticate", b"Bearer")])
            return

        if not is_well_formed_api_key(raw):
            await send_json(send, 401, {
                "error": "invalid_api_key",
                "message": "api-key 格式不正确",
                "hint": "api-key 形如 erp_xxxxxxxx",
            }, [(b"www-authenticate", b'Bearer error="invalid_token"')])
            return

        record = await anyio.to_thread.run_sync(self.store.resolve, raw)
        if record is None:
            revoked = await anyio.to_thread.run_sync(self.store.resolve_any, raw)
            code = "api_key_revoked" if revoked else "invalid_api_key"
            await send_json(send, 401, {
                "error": code,
                "message": "api-key 已失效" if revoked else "api-key 不存在",
                "hint": "请重新调用 POST /v1/auth/login",
            }, [(b"www-authenticate", b'Bearer error="invalid_token"')])
            return

        # 减少写盘：同一把 key 60 秒内只更新一次 last_used
        await anyio.to_thread.run_sync(
            lambda: self.store.touch(record.key_hash, min_interval=60.0)
        )

        token = set_current(record)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current(token)


__all__ = ["BearerAuthMiddleware", "PUBLIC_PATHS", "bearer_from_headers", "send_json"]
