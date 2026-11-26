"""Starlette 应用组装。

一个进程同时提供三套入口：

* `/v1/*` —— api-key 认证网关（登录/身份/工具清单/MCP 服务注册）
* `/ui/*`  —— React + Tailwind 可视化界面（登录页 / 注册页）
* `/mcp`  —— MCP Streamable HTTP 端点

三者共用同一个 `BearerAuthMiddleware`，所以 MCP 客户端也必须带 api-key。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ...core.security import SensitiveFilter
from ...runtime import file_proxy
from ..mcp.tools import build_mcp_server
from .auth_routes import build_routes
from .errors import ApiError, InternalFailure
from .file_routes import build_file_routes
from .middleware import BearerAuthMiddleware, send_json
from .admin_routes import build_routes as build_admin_routes
from .platform_routes import build_routes as build_platform_routes
from .state import ServerState, build_state

_log = logging.getLogger("erp_tools.server")

#: 前端构建产物（`web/` 里 `npm run build` 输出到这里，且不进 git）
STATIC_DIR = Path(__file__).with_name("static")
_SPA_INDEX = STATIC_DIR / "index.html"

_SPA_MISSING_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>HB-MCP 前端未构建</title>
<style>
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#0f172a;color:#e2e8f0;
      display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
 main{max-width:34rem;padding:2rem;border:1px solid #1e293b;border-radius:1rem;background:#111827}
 code{background:#1e293b;padding:.15rem .4rem;border-radius:.35rem}
 a{color:#38bdf8}
</style></head><body><main>
<h1>前端还没构建</h1>
<p>界面源码在 <code>web/</code>（React + Tailwind）。先构建一次：</p>
<pre><code>cd web
npm install
npm run build</code></pre>
<p>开发模式可另开一个终端跑 <code>npm run dev</code>（自动代理到 <code>/v1</code>）。</p>
<p>接口本身不受影响：<a href="/healthz">/healthz</a>、<code>POST /v1/auth/login</code>、
<code>POST /v1/mcp/register</code>。</p>
</main></body></html>"""


class SpaFiles(StaticFiles):
    """静态资源 + 前端路由回退：非 `assets/*` 的 404 一律交给 `index.html`。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or path.startswith(("assets/", "static/")):
                raise
            return await super().get_response("index.html", scope)


async def _ui_missing(request: Request) -> HTMLResponse:
    return HTMLResponse(_SPA_MISSING_HTML, status_code=503)


async def _root(request: Request) -> RedirectResponse:
    return RedirectResponse("/ui/", status_code=307)


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

    # 附件代理的签名密钥：首次启动自动生成，放在运行时数据目录
    file_proxy.configure(file_proxy.load_secret_in(state.server.data_dir))

    routes = (
        build_routes(state)
        + build_platform_routes(state)
        + build_admin_routes(state)
        + build_file_routes(state)
    )

    # 前端（React SPA）。构建产物缺失时给一张说明页，接口照常可用。
    if _SPA_INDEX.is_file():
        routes.append(Mount("/ui", app=SpaFiles(directory=str(STATIC_DIR), html=True),
                            name="ui"))
    else:
        _log.warning("前端未构建：%s 不存在（cd web && npm install && npm run build）",
                     _SPA_INDEX)
        routes.append(Route("/ui{rest:path}", _ui_missing, methods=["GET"]))
    routes.append(Route("/", _root, methods=["GET"]))

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
    app.add_middleware(
        BearerAuthMiddleware,
        store=state.store,
        trust_proxy=state.server.trust_proxy,
    )
    app.state.server_state = state
    return app


__all__ = ["create_app", "build_state", "ServerState", "send_json"]
