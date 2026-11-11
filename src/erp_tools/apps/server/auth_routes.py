"""认证与元信息路由。

`POST /v1/auth/login` 是**唯一无需 api-key 的写接口**，因此防护最重：
IP + 账号双维度限流、连续失败锁定、统一错误文案、恒定耗时失败路径。
"""

from __future__ import annotations

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ... import __version__
from ...core.auth import normalize_page_path
from ...core.errors import AuthError, ConfigError, ErpError, NetworkError, UpstreamError
from ...core.security import hash_api_key
from ...runtime.context import require_record
from ...tools.registry import describe_tools
from .errors import BadRequest, InternalFailure, Locked, RateLimited, Unauthorized
from .state import ServerState

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


def build_routes(state: ServerState) -> list[Route]:
    # ══════════════════ 健康检查 ══════════════════
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "version": __version__,
            "keys": state.store.count(),
            "global_concurrency": state.server.global_concurrency,
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
        if not username or not password:
            raise BadRequest("username 与 password 必填")

        ip = client_ip(request, state)
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
            session = await anyio.to_thread.run_sync(
                lambda: state.provider.login(username, password)
            )
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
        raw_key = await anyio.to_thread.run_sync(
            lambda: state.store.issue(username, user_id=session.user_id, ip=ip)
        )
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
        Route("/v1/auth/login", login, methods=["POST"]),
        Route("/v1/auth/logout", logout, methods=["POST"]),
        Route("/v1/me", me, methods=["GET"]),
        Route("/v1/tools", tools, methods=["GET"]),
    ]


__all__ = ["KNOWN_PAGES", "build_routes", "client_ip"]
