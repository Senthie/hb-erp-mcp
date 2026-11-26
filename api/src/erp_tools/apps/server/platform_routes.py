"""平台账号：注册 / 登录 / 会话 / ERP 绑定 / MCP token 管理。

这是「人」的入口（前端 `/ui` 用的就是它们），流程是：

    注册或登录平台账号 → 绑定一个 ERP 账号 → 申请 MCP token → 拿到 AI 客户端配置

与 `/v1/auth/login` 的区别：那条是 ERP 账号直接换 api-key（CLI 用，兼容保留），
这条是平台账号登录一次，之后只靠 HttpOnly cookie 会话，密码不进任何响应。

安全要点：

* 平台密码用 scrypt 存储，校验走恒定耗时比较；用户名不存在也做一次等价开销的哈希；
* 会话 token 只放 HttpOnly cookie，库里只存 sha256，7 天过期；
* 一个平台账号只能绑定一个 ERP 账号；换绑到**别的**ERP 账号会自动撤销该账号所有 token；
* 未绑定 ERP 不允许申请 token（token 必须对应一个可用的 ERP 身份）。
"""

from __future__ import annotations

import json
import logging

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ...core.errors import AuthError, ConfigError, ErpError, NetworkError, UpstreamError
from ...core.security import (
    generate_session_token,
    hash_api_key,
    hash_password,
    hash_session_token,
    is_session_token,
    verify_password,
)
from ...runtime.context import base_url
from ...runtime.platform_users import (
    ROLE_ADMIN,
    ROLE_USER,
    SESSION_TTL_SECONDS,
    PlatformSession,
    PlatformUser,
    expiry_from_now,
    normalize_username,
    public_token,
    validate_password,
)
from .audit import record_audit
from .auth_routes import client_ip
from .errors import (
    BadRequest,
    Conflict,
    Forbidden,
    InternalFailure,
    Locked,
    NotFound,
    RateLimited,
    Unauthorized,
)
from .state import ServerState, store_erp_secret

_log = logging.getLogger("erp_tools.server.platform")

#: 浏览器会话 cookie（HttpOnly；库里只有它的 sha256）
SESSION_COOKIE = "hb_session"
#: 未绑定 ERP 时用来做「用户不存在也走一遍 scrypt」的假哈希（进程内算一次）
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("hb-mcp-dummy-password")
    return _DUMMY_HASH


def _cookie_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    return proto.split(",")[0].strip().lower() == "https"


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


def _session_token(request: Request) -> str | None:
    raw = request.cookies.get(SESSION_COOKIE)
    return raw if is_session_token(raw) else None


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise BadRequest("请求体必须是 JSON 对象") from None
    if not isinstance(payload, dict):
        raise BadRequest("请求体必须是 JSON 对象")
    return payload


async def _require_user(state: ServerState, request: Request
                        ) -> tuple[PlatformSession, PlatformUser]:
    """校验 cookie 会话 → ``(会话, 用户)``；未登录/过期一律 401。"""
    token = _session_token(request)
    if not token:
        raise Unauthorized(
            "请先登录", code="login_required", hint="POST /v1/platform/login"
        )
    session = await anyio.to_thread.run_sync(
        lambda: state.store.get_session(hash_session_token(token))
    )
    if session is None:
        raise Unauthorized("登录已过期，请重新登录", code="session_expired")
    user = await anyio.to_thread.run_sync(lambda: state.store.get_user(session.user_id))
    if user is None:
        raise Unauthorized("账号不存在，请重新登录", code="login_required")
    if user.disabled:
        await anyio.to_thread.run_sync(
            lambda: state.store.delete_session(hash_session_token(token))
        )
        raise Forbidden("账号已被禁用，请联系管理员", code="account_disabled")
    return session, user


async def _start_session(state: ServerState, user_id: str, ip: str) -> str:
    """新建登录会话并返回**明文** token（只进 cookie）。"""
    token = generate_session_token()
    expires = expiry_from_now().isoformat(timespec="seconds")
    await anyio.to_thread.run_sync(
        lambda: state.store.create_session(hash_session_token(token), user_id, expires, ip)
    )
    return token


def build_snippet(origin: str, token: str, account: str) -> dict:
    """生成「AI 客户端怎么连」的片段：配置 JSON + 一段可直接粘给 AI 的提示。"""
    url = f"{(origin or '').rstrip('/')}/mcp"
    config = {
        "servers": {
            "hb-mcp": {
                "type": "http",
                "url": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }
    prompt = (
        f"请通过 MCP 服务器 hb-mcp 访问华博 ERP。\n"
        f"传输方式：Streamable HTTP；URL：{url}；"
        f"请求头：Authorization: Bearer {token}\n"
        f"这是平台账号 {account} 的专属 token：只在本地客户端配置里使用，"
        f"不要写进代码仓库、日志或分享给他人。"
        f"Workbuddy或其他软件使用可能会出现CA问题，需要你把服务端证书加进本地 CA bundle"
    )
    return {
        "url": url,
        "config": config,
        "config_json": json.dumps(config, ensure_ascii=False, indent=2),
        "prompt": prompt,
    }


def build_routes(state: ServerState) -> list[Route]:
    # ══════════════════ 注册 ══════════════════
    async def register(request: Request) -> JSONResponse:
        ip = client_ip(request, state)
        allowed, retry_after = state.platform_auth.allow(f"ip:{ip}")
        if not allowed:
            raise RateLimited(retry_after)
        if not state.server.allow_signup:
            raise Forbidden(
                "本实例未开放自助注册", code="signup_disabled",
                hint="联系管理员开通，或用已有的平台账号登录",
            )

        body = await _json_body(request)
        try:
            username = normalize_username(body.get("username"))
            password = validate_password(body.get("password"))
        except ValueError as exc:
            raise BadRequest(str(exc), code="invalid_account") from exc

        password_hash = await anyio.to_thread.run_sync(lambda: hash_password(password))
        role = ROLE_ADMIN if state.server.is_admin_username(username) else ROLE_USER
        user = await anyio.to_thread.run_sync(
            lambda: state.store.create_user(username, password_hash, role=role)
        )
        if user is None:
            raise Conflict("用户名已被占用", code="username_taken")

        await anyio.to_thread.run_sync(state.store.purge_expired_sessions)
        token = await _start_session(state, user.id, ip)
        await anyio.to_thread.run_sync(lambda: state.store.mark_login(user.id))
        user = await anyio.to_thread.run_sync(lambda: state.store.get_user(user.id)) or user
        _log.info("平台账号已注册：%s（来自 %s）", user.username, ip)
        await record_audit(state, request, "platform.register", actor=user,
                           target_type="user", target_id=user.id, status_code=201,
                           detail={"role": user.role})

        response = JSONResponse({"user": user.to_dict(), "tokens": []}, status_code=201)
        _set_session_cookie(response, token, request)
        return response

    # ══════════════════ 登录 ══════════════════
    async def login(request: Request) -> JSONResponse:
        ip = client_ip(request, state)
        body = await _json_body(request)
        username = str(body.get("username") or "").strip().lower()
        password = str(body.get("password") or "")

        for key in (f"ip:{ip}", f"user:{username}"):
            allowed, retry_after = state.platform_auth.allow(key)
            if not allowed:
                raise RateLimited(retry_after)

        guard_key = f"user:{username}"
        locked_for = state.guard.check(guard_key)
        if locked_for is not None:
            raise Locked(locked_for)

        creds = await anyio.to_thread.run_sync(
            lambda: state.store.get_credentials(username)
        )
        stored_hash = creds[1] if creds else _dummy_hash()
        ok = await anyio.to_thread.run_sync(lambda: verify_password(password, stored_hash))
        if creds is None or not ok:
            state.guard.fail(guard_key)
            await record_audit(state, request, "platform.login", result="failure",
                               target_type="user", target_id=username,
                               status_code=401, detail={"reason": "invalid_credentials"})
            raise Unauthorized(
                "用户名或密码错误",
                code="invalid_credentials",
                hint="连续失败 10 次会临时锁定 15 分钟",
            )

        user = creds[0]
        if user.disabled:
            await record_audit(state, request, "platform.login", actor=user,
                               result="failure", target_type="user",
                               target_id=user.id, status_code=403,
                               detail={"reason": "account_disabled"})
            raise Forbidden("账号已被禁用，请联系管理员", code="account_disabled")

        state.guard.success(guard_key)
        await anyio.to_thread.run_sync(state.store.purge_expired_sessions)
        token = await _start_session(state, user.id, ip)
        await anyio.to_thread.run_sync(lambda: state.store.mark_login(user.id))
        # 白名单里的账号登录时自动升级为 admin（便于用配置加管理员）
        if state.server.is_admin_username(user.username) and not user.is_admin:
            user = await anyio.to_thread.run_sync(
                lambda: state.store.update_user(user.id, role=ROLE_ADMIN)
            ) or user
        user = await anyio.to_thread.run_sync(lambda: state.store.get_user(user.id)) or user
        tokens = await anyio.to_thread.run_sync(lambda: state.store.list_for_owner(user.id))
        await record_audit(state, request, "platform.login", actor=user,
                           target_type="user", target_id=user.id, status_code=200)

        response = JSONResponse({
            "user": user.to_dict(),
            "tokens": [public_token(item) for item in tokens],
        })
        _set_session_cookie(response, token, request)
        return response

    # ══════════════════ 登出 ══════════════════
    async def logout(request: Request) -> Response:
        token = _session_token(request)
        user: PlatformUser | None = None
        if token:
            session = await anyio.to_thread.run_sync(
                lambda: state.store.get_session(hash_session_token(token))
            )
            if session is not None:
                user = await anyio.to_thread.run_sync(
                    lambda: state.store.get_user(session.user_id)
                )
            await anyio.to_thread.run_sync(
                lambda: state.store.delete_session(hash_session_token(token))
            )
        await record_audit(state, request, "platform.logout", actor=user,
                           status_code=204)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ══════════════════ 当前账号 ══════════════════
    async def me(request: Request) -> JSONResponse:
        session, user = await _require_user(state, request)
        tokens = await anyio.to_thread.run_sync(lambda: state.store.list_for_owner(user.id))
        stored = False
        if user.erp_bound:
            try:
                stored = bool(await anyio.to_thread.run_sync(
                    lambda: state.store.get_erp_secret(user.erp_username or "")
                ))
            except Exception:  # noqa: BLE001 - 查不到就当没存
                stored = False
        return JSONResponse({
            "user": user.to_dict(),
            "tokens": [public_token(item) for item in tokens],
            "session": {"expires_at": session.expires_at},
            # ERP 密码是否已加密保存（过期自动重登）；不代表明文可读
            "erp_credential": {
                "store_enabled": state.server.store_erp_password,
                "stored": stored,
            },
        })

    # ══════════════════ 绑定 ERP 账号 ══════════════════
    async def bind_erp(request: Request) -> JSONResponse:
        _, user = await _require_user(state, request)
        ip = client_ip(request, state)
        body = await _json_body(request)

        erp_username = str(body.get("username") or "").strip()
        erp_password = str(body.get("password") or "")
        if not erp_username or not erp_password:
            raise BadRequest("ERP 工号与密码必填")

        for key in (f"ip:{ip}", f"user:{erp_username.lower()}"):
            allowed, retry_after = state.buckets.allow(key)
            if not allowed:
                raise RateLimited(retry_after)
        guard_key = f"bind:{erp_username.lower()}"
        locked_for = state.guard.check(guard_key)
        if locked_for is not None:
            raise Locked(locked_for)

        try:
            erp_session = await anyio.to_thread.run_sync(
                lambda: state.provider.login(erp_username, erp_password)
            )
        except (AuthError, ConfigError) as exc:
            state.guard.fail(guard_key)
            await record_audit(state, request, "platform.erp.bind", actor=user,
                               result="failure", target_type="erp_account",
                               target_id=erp_username, status_code=400,
                               detail={"reason": "invalid_erp_credentials"})
            raise BadRequest(
                "ERP 工号或密码错误", code="invalid_erp_credentials",
                hint="这是登录华博 ERP 的工号密码，不是平台账号",
            ) from exc
        except (NetworkError, UpstreamError) as exc:
            raise InternalFailure(
                "无法连接 ERP，请稍后重试", code="erp_unreachable", status=502
            ) from exc
        except ErpError as exc:
            raise InternalFailure(
                "ERP 登录失败", code="erp_login_failed", status=502
            ) from exc

        state.guard.success(guard_key)
        previous = (user.erp_username or "").lower()
        updated = await anyio.to_thread.run_sync(
            lambda: state.store.bind_erp(user.id, erp_username, erp_session.user_id)
        )
        # 加密保存密码：会话过期时服务自动重登，用户不必再操作
        stored = await anyio.to_thread.run_sync(
            lambda: store_erp_secret(state, erp_username, erp_password)
        )
        revoked = 0
        if previous and previous != erp_username.lower():
            # 换绑到别的 ERP 账号：旧 token 还指向老身份，必须作废
            revoked = await anyio.to_thread.run_sync(
                lambda: state.store.revoke_owner(user.id)
            )
            await anyio.to_thread.run_sync(
                lambda: state.store.delete_erp_secret(user.erp_username or "")
            )
        _log.info("平台账号 %s 绑定 ERP %s（撤销旧 token %d 把，存密码=%s）",
                  user.username, erp_username, revoked, stored)
        await record_audit(state, request, "platform.erp.bind",
                           actor=updated or user, target_type="erp_account",
                           target_id=erp_username, status_code=200,
                           detail={"revoked_tokens": revoked,
                                   "credential_stored": stored})
        tokens = await anyio.to_thread.run_sync(lambda: state.store.list_for_owner(user.id))
        return JSONResponse({
            "user": (updated or user).to_dict(),
            "revoked_tokens": revoked,
            "credential_stored": stored,
            "tokens": [public_token(item) for item in tokens],
        })

    # ══════════════════ 删除已保存的 ERP 密码 ══════════════════
    async def delete_erp_credential(request: Request) -> JSONResponse:
        _, user = await _require_user(state, request)
        if not user.erp_bound:
            raise Conflict("尚未绑定 ERP 账号", code="erp_not_bound")
        removed = await anyio.to_thread.run_sync(
            lambda: state.store.delete_erp_secret(user.erp_username or "")
        )
        await record_audit(state, request, "platform.erp.credential_delete",
                           actor=user, target_type="erp_account",
                           target_id=user.erp_username, status_code=200,
                           detail={"removed": removed})
        return JSONResponse({"removed": removed})

    # ══════════════════ token 列表 ══════════════════
    async def list_tokens(request: Request) -> JSONResponse:
        _, user = await _require_user(state, request)
        tokens = await anyio.to_thread.run_sync(
            lambda: state.store.list_for_owner(user.id)
        )
        return JSONResponse({
            "erp": user.to_dict()["erp"],
            "tokens": [public_token(item) for item in tokens],
        })

    # ══════════════════ 申请 token ══════════════════
    async def create_token(request: Request) -> JSONResponse:
        _, user = await _require_user(state, request)
        if not user.erp_bound:
            raise Conflict(
                "请先绑定 ERP 账号再申请 token",
                code="erp_not_bound",
                hint="POST /v1/platform/erp/bind 带上 ERP 工号与密码",
            )
        body = await _json_body(request)
        label = str(body.get("label") or "").strip()[:80]

        raw = await anyio.to_thread.run_sync(
            lambda: state.store.issue(
                user.erp_username or "",
                user_id=user.erp_user_id,
                ip=client_ip(request, state),
                note=label or None,
                owner_id=user.id,
            )
        )
        origin = base_url() or str(request.base_url).rstrip("/")
        snippet = build_snippet(origin, raw, user.username)
        token_id = hash_api_key(raw)[:16]
        _log.info("平台账号 %s 申请了 token %s（%s）", user.username, token_id, label or "-")
        await record_audit(state, request, "platform.token.create", actor=user,
                           target_type="api_key", target_id=token_id, status_code=201,
                           detail={"label": label or None,
                                   "erp_username": user.erp_username})
        return JSONResponse({
            "token": raw,
            "id": token_id,
            "label": label,
            "erp_username": user.erp_username,
            "mcp": snippet,
            "note": "token 只显示这一次，请立刻复制保存；之后只能看到 id 与状态",
        }, status_code=201)

    # ══════════════════ 撤销 token ══════════════════
    async def revoke_token(request: Request) -> JSONResponse:
        _, user = await _require_user(state, request)
        token_id = str(request.path_params.get("token_id") or "").strip()
        ok = await anyio.to_thread.run_sync(
            lambda: state.store.revoke_owner_prefix(user.id, token_id)
        )
        if not ok:
            raise NotFound("token 不存在或已撤销", code="token_not_found")
        await record_audit(state, request, "platform.token.revoke", actor=user,
                           target_type="api_key", target_id=token_id, status_code=200)
        return JSONResponse({"revoked": True, "id": token_id})

    return [
        Route("/v1/platform/register", register, methods=["POST"]),
        Route("/v1/platform/login", login, methods=["POST"]),
        Route("/v1/platform/logout", logout, methods=["POST"]),
        Route("/v1/platform/me", me, methods=["GET"]),
        Route("/v1/platform/erp/bind", bind_erp, methods=["POST"]),
        Route("/v1/platform/erp/credentials", delete_erp_credential, methods=["DELETE"]),
        Route("/v1/platform/tokens", list_tokens, methods=["GET"]),
        Route("/v1/platform/tokens", create_token, methods=["POST"]),
        Route("/v1/platform/tokens/{token_id}/revoke", revoke_token, methods=["POST"]),
    ]


__all__ = ["SESSION_COOKIE", "build_routes", "build_snippet"]
