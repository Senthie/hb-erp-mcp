"""API Key 认证中间件（裸 ASGI）。

为什么不用 Starlette 的 `BaseHTTPMiddleware` 或 `@app.middleware("http")`：
它们会让下游应用跑在独立任务里，contextvar 的可见性不保证，容易出现
「中间件里设置了身份，工具里读到 None」。裸 ASGI 是同一个协程调用链，稳。

放行清单（无需认证）：`/healthz`、`/v1/auth/login`、`/v1/mcp/register`、
`/v1/mcp/services`、前端页面 `/ui/*`、`/`（跳转到 `/ui/`）、以及任何 `OPTIONS` 预检。
"""

from __future__ import annotations

import json
from typing import Any

import anyio

from ...core.security import is_well_formed_api_key
from ...runtime.auth_store import ApiKeyStore
from ...runtime.context import (
    reset_base,
    reset_current,
    reset_request_meta,
    set_base,
    set_current,
    set_request_meta,
)

#: `/v1/files` 是附件代理：鉴权靠 URL 里的签名（不含 api-key，可直接贴浏览器）
#: `/v1/mcp/register`、`/v1/mcp/services` 是 MCP 服务自身的登记与目录（不涉及 ERP 数据）
PUBLIC_PATHS = frozenset({
    "/healthz",
    "/v1/auth/login",
    "/v1/files",
    "/v1/mcp/register",
    "/v1/mcp/services",
    "/",
})
#: `/v1/platform/*` 用 HttpOnly cookie 会话鉴权，不看 Bearer；`/ui` 是前端静态资源
PUBLIC_PREFIXES = ("/docs", "/openapi.json", "/ui", "/v1/platform", "/v1/admin")


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


def _request_base(scope: dict) -> str:
    """从 ASGI scope 拼出 `scheme://host[:port]`（工具生成代理链接时用）。"""
    headers = scope.get("headers") or []
    host = ""
    proto = ""
    for name, value in headers:
        key = name.lower()
        if key == b"host" and not host:
            host = value.decode("latin-1").strip()
        elif key in (b"x-forwarded-proto", b"x-scheme") and not proto:
            proto = value.decode("latin-1").split(",")[0].strip()
    if not host:
        server = scope.get("server") or ()
        if len(server) == 2:
            host = f"{server[0]}:{server[1]}"
    scheme = proto or scope.get("scheme") or "http"
    return f"{scheme}://{host}".rstrip("/") if host else ""


def _header_text(scope: dict, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1").strip()
    return ""


def _client_ip(scope: dict, trust_proxy: bool) -> str:
    if trust_proxy:
        forwarded = _header_text(scope, b"x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
    client = scope.get("client") or ()
    return client[0] if client else "unknown"


class BearerAuthMiddleware:
    """把 api-key 解析成 `ApiKeyRecord` 并写入 contextvar。"""

    def __init__(self, app, *, store: ApiKeyStore, trust_proxy: bool = False) -> None:
        self.app = app
        self.store = store
        self.trust_proxy = trust_proxy

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        base_token = set_base(_request_base(scope))
        meta_tokens = set_request_meta(
            _client_ip(scope, self.trust_proxy),
            _header_text(scope, b"user-agent"),
        )
        try:
            await self._dispatch(scope, receive, send, method, path)
        finally:
            reset_request_meta(meta_tokens)
            reset_base(base_token)

    async def _dispatch(self, scope, receive, send, method: str, path: str) -> None:
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
