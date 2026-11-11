"""Starlette 应用组装。

一个进程同时提供两套入口：

* `/v1/*` —— api-key 认证网关（登录/身份/工具清单）
* `/mcp`  —— MCP Streamable HTTP 端点

两者共用同一个 `BearerAuthMiddleware`，所以 MCP 客户端也必须带 api-key。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

from ...core.security import SensitiveFilter
from ..mcp.tools import build_mcp_server
from .auth_routes import build_routes
from .errors import ApiError, InternalFailure
from .middleware import BearerAuthMiddleware, send_json
from .state import ServerState, build_state

_log = logging.getLogger("erp_tools.server")


def _install_log_filter() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, SensitiveFilter) for f in root.filters):
        root.addFilter(SensitiveFilter())


def _transport_security(state: ServerState) -> TransportSecuritySettings | None:
    """校验 Host/Origin（防 DNS rebinding）。

    只要显式给了 `--allow-host` 就生效——包括监听回环地址、由**同容器/同 Pod 的**
    nginx 转发进来的场景（此时外部 Host 不是 localhost，交给 SDK 默认保护会 421）。
    没给白名单时返回 None：回环模式用 SDK 自带的本机保护，
    团队模式在 `ServerConfig.validate()` 阶段已经强制要求白名单。
    """
    hosts = [h for h in state.server.allow_hosts if h.strip()]
    if not hosts:
        return None
    origins = [f"{scheme}://{host}" for host in hosts for scheme in ("http", "https")]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.to_dict(), status_code=exc.status, headers=exc.headers)


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("未处理异常：%s %s", request.method, request.url.path)
    fallback = InternalFailure("服务内部错误")
    return JSONResponse(fallback.to_dict(), status_code=500)


def create_app(state: ServerState, *, enable_mcp: bool = True) -> Starlette:
    """组装网关应用（认证中间件 + 路由 + MCP 挂载 + 错误处理）。"""
    _install_log_filter()

    routes = build_routes(state)

    mcp_app = None
    if enable_mcp:
        mcp = build_mcp_server(state)
        mcp_app = mcp.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            # stateless：每个 POST 独立处理，身份靠 contextvar，不依赖会话
            stateless_http=True,
            transport_security=_transport_security(state),
            host=state.server.host,
        )
        # 必须放在最后：Mount("/") 会兜住所有未被前面路由匹配的路径
        routes.append(Mount("/", app=mcp_app))

    @asynccontextmanager
    async def lifespan(app: Starlette):
        _log.info("erp-tools 网关启动：%s", state.server.describe())
        try:
            if mcp_app is None:
                yield
            else:
                # 子应用的 session manager 靠它启动；父应用不会自动跑
                async with mcp_app.router.lifespan_context(mcp_app):
                    yield
        finally:
            state.close()

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        exception_handlers={
            ApiError: _handle_api_error,
            InternalFailure: _handle_api_error,
            Exception: _handle_unexpected,
        },
    )
    app.add_middleware(BearerAuthMiddleware, store=state.store)
    app.state.server_state = state
    return app


__all__ = ["create_app", "build_state", "ServerState", "send_json"]
