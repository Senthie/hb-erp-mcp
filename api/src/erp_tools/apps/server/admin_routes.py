"""管理员后台接口：平台账号管理 + 审计日志检索。

全部挂在 `/v1/admin/*`，用平台账号的 cookie 会话鉴权，并且要求 `role == admin`。
所有写操作都会落到审计日志（`audit_logs`），包括「谁改了谁、改成什么」。
"""

from __future__ import annotations

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...runtime.audit import ACTION_LABELS, ACTIONS, RESULTS
from ...runtime.platform_users import ROLES, PlatformUser, public_token
from .audit import record_audit
from .errors import BadRequest, Conflict, Forbidden, NotFound
from .platform_routes import _require_user
from .state import ServerState

MAX_PAGE = 200


async def _require_admin(state: ServerState, request: Request) -> PlatformUser:
    _, user = await _require_user(state, request)
    if not user.is_admin:
        raise Forbidden(
            "需要管理员权限", code="admin_required",
            hint="用 config.yaml 的 server.admin_users（或 erp init 时的「第一个管理员」）指定的账号登录",
        )
    return user


def _page(request: Request) -> tuple[int, int]:
    def _int(name: str, default: int) -> int:
        raw = request.query_params.get(name)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            raise BadRequest(f"{name} 必须是整数") from None

    limit = max(1, min(_int("limit", 50), MAX_PAGE))
    offset = max(0, _int("offset", 0))
    return limit, offset


def _normalize_time(raw: str | None, *, end: bool) -> str | None:
    """把 `2026-11-26` 这类日期补成当天起止时间；空值返回 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    if len(text) == 10:  # YYYY-MM-DD
        return f"{text}T{('23:59:59' if end else '00:00:00')}"
    return text


async def _user_payload(state: ServerState, user: PlatformUser) -> dict:
    tokens = await anyio.to_thread.run_sync(
        lambda: state.store.list_for_owner(user.id, include_revoked=True)
    )
    active = [item for item in tokens if not item.revoked]
    sessions = await anyio.to_thread.run_sync(lambda: state.store.count_sessions(user.id))
    return {
        **user.to_dict(),
        "token_count": len(active),
        "token_total": len(tokens),
        "session_count": sessions,
        "tokens": [public_token(item) for item in tokens],
    }


async def _call_payloads(state: ServerState, logs) -> list[dict]:
    """调用日志 + 平台账号名（token 只存 owner_id，这里补一次用户名）。"""
    owner_ids = {item.actor_id for item in logs if item.actor_id}
    names: dict[str, str] = {}
    for owner_id in owner_ids:
        user = await anyio.to_thread.run_sync(lambda oid=owner_id: state.store.get_user(oid))
        if user is not None:
            names[owner_id] = user.username
    out: list[dict] = []
    for item in logs:
        data = item.to_dict()
        data["owner_name"] = names.get(item.actor_id or "")
        out.append(data)
    return out


def build_routes(state: ServerState) -> list[Route]:
    # ══════════════════ 概览 ══════════════════
    async def overview(request: Request) -> JSONResponse:
        admin = await _require_admin(state, request)
        users = await anyio.to_thread.run_sync(lambda: state.store.list_users(limit=MAX_PAGE))
        total = await anyio.to_thread.run_sync(state.store.count_users)
        tokens_active = await anyio.to_thread.run_sync(lambda: state.store.count())
        tokens_total = await anyio.to_thread.run_sync(
            lambda: state.store.count(include_revoked=True)
        )
        calls_total = await anyio.to_thread.run_sync(state.store.count_call_logs)
        return JSONResponse({
            "admin": admin.to_dict(),
            "users": {
                "total": total,
                "sampled": len(users),
                "admins": sum(1 for u in users if u.is_admin),
                "disabled": sum(1 for u in users if u.disabled),
                "erp_bound": sum(1 for u in users if u.erp_bound),
            },
            "tokens": {"active": tokens_active, "total": tokens_total},
            "calls": {"total": calls_total},
            "actions": [{"value": item, "label": ACTION_LABELS.get(item, item)}
                        for item in ACTIONS],
            "results": list(RESULTS),
        })

    # ══════════════════ 用户列表 ══════════════════
    async def list_users(request: Request) -> JSONResponse:
        await _require_admin(state, request)
        limit, offset = _page(request)
        query = (request.query_params.get("query") or "").strip()
        users = await anyio.to_thread.run_sync(
            lambda: state.store.list_users(query=query, limit=limit, offset=offset)
        )
        total = await anyio.to_thread.run_sync(
            lambda: state.store.count_users(query=query)
        )
        items = [await _user_payload(state, user) for user in users]
        return JSONResponse({
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
        })

    # ══════════════════ 用户详情 ══════════════════
    async def user_detail(request: Request) -> JSONResponse:
        await _require_admin(state, request)
        user_id = request.path_params["user_id"]
        user = await anyio.to_thread.run_sync(lambda: state.store.get_user(user_id))
        if user is None:
            raise NotFound("用户不存在", code="user_not_found")
        payload = await _user_payload(state, user)
        recent = await anyio.to_thread.run_sync(
            lambda: state.store.list_audit(actor=user.username, limit=20)
        )
        payload["recent_audit"] = [item.to_dict() for item in recent]
        calls = await anyio.to_thread.run_sync(
            lambda: state.store.list_call_logs(
                actor=user.erp_username or user.username, limit=20
            )
        )
        payload["recent_calls"] = await _call_payloads(state, calls)
        payload["call_count"] = await anyio.to_thread.run_sync(
            lambda: state.store.count_call_logs(actor_id=user.id)
        )
        return JSONResponse(payload)

    # ══════════════════ 改角色 / 禁用 ══════════════════
    async def update_user(request: Request) -> JSONResponse:
        admin = await _require_admin(state, request)
        user_id = request.path_params["user_id"]
        target = await anyio.to_thread.run_sync(lambda: state.store.get_user(user_id))
        if target is None:
            raise NotFound("用户不存在", code="user_not_found")

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise BadRequest("请求体必须是 JSON 对象") from None
        if not isinstance(body, dict):
            raise BadRequest("请求体必须是 JSON 对象")

        role = body.get("role")
        disabled = body.get("disabled")
        if role is None and disabled is None:
            raise BadRequest("至少给出 role 或 disabled")
        if role is not None and role not in ROLES:
            raise BadRequest(f"role 只能是：{' / '.join(ROLES)}")
        if disabled is not None and not isinstance(disabled, bool):
            raise BadRequest("disabled 必须是布尔值")

        if target.id == admin.id:
            raise Conflict("不能修改自己的角色或禁用状态", code="cannot_modify_self")

        updated = await anyio.to_thread.run_sync(
            lambda: state.store.update_user(user_id, role=role, disabled=disabled)
        )
        killed = 0
        revoked = 0
        if disabled:
            # 禁用＝立即失效：踢掉全部会话，并撤销该账号的 MCP token
            killed = await anyio.to_thread.run_sync(
                lambda: state.store.delete_user_sessions(user_id)
            )
            revoked = await anyio.to_thread.run_sync(
                lambda: state.store.revoke_owner(user_id)
            )
        await record_audit(
            state, request, "admin.user.update", actor=admin,
            target_type="user", target_id=user_id, status_code=200,
            detail={"username": target.username, "role": role,
                    "disabled": disabled, "killed_sessions": killed,
                    "revoked_tokens": revoked},
        )
        return JSONResponse({
            "user": await _user_payload(state, updated or target),
            "killed_sessions": killed,
            "revoked_tokens": revoked,
        })

    # ══════════════════ 解绑 ERP ══════════════════
    async def unbind_erp(request: Request) -> JSONResponse:
        admin = await _require_admin(state, request)
        user_id = request.path_params["user_id"]
        target = await anyio.to_thread.run_sync(lambda: state.store.get_user(user_id))
        if target is None:
            raise NotFound("用户不存在", code="user_not_found")
        if not target.erp_bound:
            raise BadRequest("该用户没有绑定 ERP 账号", code="erp_not_bound")

        updated = await anyio.to_thread.run_sync(
            lambda: state.store.unbind_erp(user_id)
        )
        revoked = await anyio.to_thread.run_sync(
            lambda: state.store.revoke_owner(user_id)
        )
        # 解绑后没必要再留着密码密文
        await anyio.to_thread.run_sync(
            lambda: state.store.delete_erp_secret(target.erp_username or "")
        )
        await record_audit(
            state, request, "admin.user.unbind_erp", actor=admin,
            target_type="user", target_id=user_id, status_code=200,
            detail={"username": target.username, "erp_username": target.erp_username,
                    "revoked_tokens": revoked},
        )
        return JSONResponse({
            "user": await _user_payload(state, updated or target),
            "revoked_tokens": revoked,
        })

    # ══════════════════ 撤销某用户全部 token ══════════════════
    async def revoke_tokens(request: Request) -> JSONResponse:
        admin = await _require_admin(state, request)
        user_id = request.path_params["user_id"]
        target = await anyio.to_thread.run_sync(lambda: state.store.get_user(user_id))
        if target is None:
            raise NotFound("用户不存在", code="user_not_found")

        revoked = await anyio.to_thread.run_sync(
            lambda: state.store.revoke_owner(user_id)
        )
        await record_audit(
            state, request, "admin.user.revoke_tokens", actor=admin,
            target_type="user", target_id=user_id, status_code=200,
            detail={"username": target.username, "revoked_tokens": revoked},
        )
        return JSONResponse({
            "user": await _user_payload(state, target),
            "revoked_tokens": revoked,
        })

    # ══════════════════ 删除用户 ══════════════════
    async def delete_user(request: Request) -> JSONResponse:
        admin = await _require_admin(state, request)
        user_id = request.path_params["user_id"]
        target = await anyio.to_thread.run_sync(lambda: state.store.get_user(user_id))
        if target is None:
            raise NotFound("用户不存在", code="user_not_found")
        if target.id == admin.id:
            raise Conflict("不能删除自己的账号", code="cannot_delete_self")

        await anyio.to_thread.run_sync(lambda: state.store.delete_user(user_id))
        # 删账号时一并抹掉加密保存的 ERP 密码
        await anyio.to_thread.run_sync(
            lambda: state.store.delete_erp_secret(target.erp_username or "")
        )
        await record_audit(
            state, request, "admin.user.delete", actor=admin,
            target_type="user", target_id=user_id, status_code=200,
            detail={"username": target.username, "erp_username": target.erp_username},
        )
        return JSONResponse({"deleted": True, "id": user_id})

    # ══════════════════ 审计日志 ══════════════════
    async def list_audit(request: Request) -> JSONResponse:
        await _require_admin(state, request)
        limit, offset = _page(request)
        params = request.query_params
        action = (params.get("action") or "").strip() or None
        actor = (params.get("actor") or "").strip() or None
        result = (params.get("result") or "").strip() or None
        since = _normalize_time(params.get("since"), end=False)
        until = _normalize_time(params.get("until"), end=True)
        rows = await anyio.to_thread.run_sync(
            lambda: state.store.list_audit(
                action=action, actor=actor, result=result,
                since=since, until=until, limit=limit + 1, offset=offset,
            )
        )
        has_more = len(rows) > limit
        items = [item.to_dict() for item in rows[:limit]]
        return JSONResponse({
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "items": items,
        })

    # ══════════════════ MCP 工具调用日志 ══════════════════
    async def list_calls(request: Request) -> JSONResponse:
        await _require_admin(state, request)
        limit, offset = _page(request)
        params = request.query_params
        tool = (params.get("tool") or "").strip() or None
        actor = (params.get("actor") or "").strip() or None
        status = (params.get("status") or "").strip() or None
        since = _normalize_time(params.get("since"), end=False)
        until = _normalize_time(params.get("until"), end=True)

        rows = await anyio.to_thread.run_sync(
            lambda: state.store.list_call_logs(
                tool=tool, actor=actor, status=status,
                since=since, until=until, limit=limit + 1, offset=offset,
            )
        )
        has_more = len(rows) > limit
        items = await _call_payloads(state, rows[:limit])
        return JSONResponse({
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "items": items,
        })

    return [
        Route("/v1/admin/overview", overview, methods=["GET"]),
        Route("/v1/admin/users", list_users, methods=["GET"]),
        Route("/v1/admin/users/{user_id}", user_detail, methods=["GET"]),
        Route("/v1/admin/users/{user_id}/update", update_user, methods=["POST"]),
        Route("/v1/admin/users/{user_id}/unbind-erp", unbind_erp, methods=["POST"]),
        Route("/v1/admin/users/{user_id}/revoke-tokens", revoke_tokens, methods=["POST"]),
        Route("/v1/admin/users/{user_id}", delete_user, methods=["DELETE"]),
        Route("/v1/admin/audit", list_audit, methods=["GET"]),
        Route("/v1/admin/calls", list_calls, methods=["GET"]),
    ]


__all__ = ["build_routes"]
