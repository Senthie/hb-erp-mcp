"""认证、MCP 服务注册与元信息路由。

`POST /v1/auth/login` 与 `POST /v1/mcp/register` 是**无需 api-key 的写接口**，
因此防护最重：IP 双维度限流、连续失败锁定、统一错误文案、恒定耗时失败路径；
注册接口另外支持可选的共享密钥（`ERPSRV_REGISTER_SECRET`）。
"""

from __future__ import annotations

import hmac
import logging

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ... import __version__
from ...core.auth import normalize_page_path
from ...core.errors import AuthError, ConfigError, ErpError, NetworkError, UpstreamError
from ...core.security import hash_api_key
from ...runtime import file_proxy
from ...runtime.context import require_record
from ...runtime.mcp_services import normalize_registration
from ...runtime.sessions import UserSession
from ...tools.registry import describe_tools
from .audit import record_audit
from .errors import (
    ApiError,
    BadRequest,
    Forbidden,
    InternalFailure,
    Locked,
    RateLimited,
    Unauthorized,
)
from .state import ServerState

_log = logging.getLogger("erp_tools.server")

#: 登录响应里回给客户端的页面键（避免把整份菜单吐出去）
KNOWN_PAGES = {
    "quote_index": "BaoJia/BJ_KHBJXQ/Index.aspx",
    "quote": "BaoJia/BJ_KHBJXQ/Add.aspx",
    "attendance": "RenShi/RS_KQDA_M/Add.aspx",
}

#: 用于失败路径的「恒定耗时」兜底哈希（长度与真实哈希一致）
_DUMMY_HASH = "0" * 64


def client_ip(request: Request, state: ServerState) -> str:
    if state.server.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def _pick_pages(mapping: dict[str, str]) -> dict[str, str]:
    normalized = {normalize_page_path(k): str(v) for k, v in (mapping or {}).items()}
    return {
        name: normalized[normalize_page_path(path)]
        for name, path in KNOWN_PAGES.items()
        if normalize_page_path(path) in normalized
    }


def authenticate(state: ServerState, username: str, password: str, ip: str
                 ) -> tuple[str, UserSession]:
    """校验 ERP 账号并签发 api-key。

    **阻塞调用**（连 ERP、写库），路由侧必须放进线程池执行。
    抛 ``ApiError`` 的子类，由全局异常处理器统一转成 JSON。
    """
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        raise BadRequest("username 与 password 必填")

    user_key = f"user:{username.lower()}"
    ip_key = f"ip:{ip}"
    for key in (ip_key, user_key):
        allowed, retry_after = state.buckets.allow(key)
        if not allowed:
            raise RateLimited(retry_after)

    locked_for = state.guard.check(user_key)
    if locked_for is not None:
        raise Locked(locked_for)

    try:
        session = state.provider.login(username, password)
    except (AuthError, ConfigError) as exc:
        state.guard.fail(user_key)
        # 恒定耗时：失败路径也做一次等长哈希比对
        _ = hash_api_key(password) == _DUMMY_HASH
        raise Unauthorized(
            "账号或密码错误",
            code="invalid_credentials",
            hint="确认 ERP 工号与密码；连续失败会临时锁定账号",
        ) from exc
    except (NetworkError, UpstreamError) as exc:
        state.guard.fail(user_key)
        raise InternalFailure(
            "无法连接 ERP，请稍后重试",
            code="erp_unreachable",
            status=502,
        ) from exc
    except ErpError as exc:
        state.guard.fail(user_key)
        raise InternalFailure(
            "ERP 登录失败", code="erp_login_failed", status=502,
        ) from exc

    state.guard.success(user_key)
    raw_key = state.store.issue(username, user_id=session.user_id, ip=ip)
    return raw_key, session


def build_routes(state: ServerState) -> list[Route]:
    # ══════════════════ 健康检查 ══════════════════
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "service": "HB-MCP",
            "version": __version__,
            "keys": state.store.count(),
            "global_concurrency": state.server.global_concurrency,
            "store": state.server.store_backend,
            "register_secret_required": bool(state.server.register_secret),
            "signup_enabled": state.server.allow_signup,
        })

    # ══════════════════ MCP 服务注册 ══════════════════
    async def register_service(request: Request) -> JSONResponse:
        """登记（或更新）本 MCP 服务的元信息：按 name 幂等。"""
        ip = client_ip(request, state)
        allowed, retry_after = state.registration.allow(f"ip:{ip}")
        if not allowed:
            raise RateLimited(retry_after)

        secret = state.server.register_secret
        if secret:
            sent = request.headers.get("x-register-secret", "")
            if not hmac.compare_digest(sent, secret):
                raise Forbidden(
                    "注册密钥不正确",
                    code="register_secret_invalid",
                    hint="请求头需带 X-Register-Secret，与服务端 ERPSRV_REGISTER_SECRET 一致",
                )

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            raise BadRequest('请求体必须是 JSON：{"name": "HB-MCP", "version": "0.3.0"}')
        if not isinstance(payload, dict):
            raise BadRequest("请求体必须是 JSON 对象")

        try:
            values = normalize_registration(payload)
        except ValueError as exc:
            raise BadRequest(str(exc), code="invalid_service_info") from exc

        service = await anyio.to_thread.run_sync(
            lambda: state.store.register_service(values)
        )
        _log.info("MCP 服务已登记：%s v%s（来自 %s）",
                  service.name, service.version or "-", ip)
        await record_audit(state, request, "mcp.register",
                           target_type="mcp_service", target_id=service.name,
                           status_code=201,
                           detail={"version": service.version,
                                   "environment": service.environment})
        return JSONResponse({
            "status": "registered",
            "service": service.to_dict(),
            "note": "同名服务再次注册会覆盖元数据（按 name 幂等）",
        }, status_code=201)

    async def list_services(request: Request) -> JSONResponse:
        raw_limit = request.query_params.get("limit") or "50"
        try:
            limit = int(raw_limit)
        except ValueError:
            raise BadRequest("limit 必须是整数") from None
        limit = max(1, min(limit, 200))
        services = await anyio.to_thread.run_sync(
            lambda: state.store.list_services(limit=limit)
        )
        return JSONResponse({
            "count": len(services),
            "services": [item.to_dict() for item in services],
        })

    # ══════════════════ 登录 ══════════════════
    async def login(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            raise BadRequest("请求体必须是 JSON：{\"username\": ..., \"password\": ...}")
        if not isinstance(payload, dict):
            raise BadRequest("请求体必须是 JSON 对象")

        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        ip = client_ip(request, state)
        try:
            raw_key, session = await anyio.to_thread.run_sync(
                lambda: authenticate(state, username, password, ip)
            )
        except ApiError as exc:
            await record_audit(state, request, "auth.login", result="failure",
                               actor_name=username, target_type="erp_account",
                               target_id=username, status_code=exc.status,
                               detail={"reason": exc.code})
            raise
        await record_audit(state, request, "auth.login", actor_name=username,
                           target_type="erp_account", target_id=username,
                           status_code=200,
                           detail={"token": raw_key[:8] + "…"})
        pages = _pick_pages(session.pages)
        return JSONResponse({
            "api_key": raw_key,
            "username": username,
            "user_id": session.user_id,
            "erp_pages": pages,
            "note": (
                "api-key 不设过期时间，但会随 ERP 会话一起失效；"
                "会话过期后请重新登录获取新 key"
            ),
        })

    # ══════════════════ 当前身份 ══════════════════
    async def me(request: Request) -> JSONResponse:
        record = require_record()
        alive = await anyio.to_thread.run_sync(
            lambda: state.provider.is_alive(record.username)
        )
        if not alive:
            await anyio.to_thread.run_sync(
                lambda: state.store.revoke_hash(record.key_hash)
            )
            raise Unauthorized(
                "ERP 会话已过期，api-key 已一并作废",
                code="erp_session_expired",
                hint="请重新调用 POST /v1/auth/login",
            )
        return JSONResponse({
            "username": record.username,
            "user_id": record.user_id,
            "created_at": record.created_at,
            "last_used": record.last_used,
            "erp_alive": True,
            "file_proxy": file_proxy.describe(),
        })

    # ══════════════════ 登出 ══════════════════
    async def logout(request: Request) -> Response:
        record = require_record()
        await anyio.to_thread.run_sync(lambda: state.store.revoke_hash(record.key_hash))
        return Response(status_code=204)

    # ══════════════════ 工具清单 ══════════════════
    async def tools(request: Request) -> JSONResponse:
        require_record()
        return JSONResponse({"tools": describe_tools()})

    return [
        Route("/healthz", healthz, methods=["GET", "HEAD"]),
        Route("/v1/mcp/register", register_service, methods=["POST"]),
        Route("/v1/mcp/services", list_services, methods=["GET"]),
        Route("/v1/auth/login", login, methods=["POST"]),
        Route("/v1/auth/logout", logout, methods=["POST"]),
        Route("/v1/me", me, methods=["GET"]),
        Route("/v1/tools", tools, methods=["GET"]),
    ]


__all__ = ["KNOWN_PAGES", "authenticate", "build_routes", "client_ip"]
