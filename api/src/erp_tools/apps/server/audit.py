"""路由侧写审计日志的统一入口（异步、失败不影响业务）。"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from starlette.requests import Request

from ...runtime.audit import (
    ACTOR_ANONYMOUS,
    ACTOR_API_KEY,
    ACTOR_USER,
    RESULT_SUCCESS,
    build_entry,
)
from ...runtime.auth_store import ApiKeyRecord
from ...runtime.platform_users import PlatformUser
from .state import ServerState

_log = logging.getLogger("erp_tools.server.audit")


def actor_of(actor: PlatformUser | ApiKeyRecord | None) -> tuple[str | None, str | None, str]:
    """把「谁」归一化成 (id, name, type)。"""
    if isinstance(actor, PlatformUser):
        return actor.id, actor.username, ACTOR_USER
    if isinstance(actor, ApiKeyRecord):
        return (actor.owner_id or actor.key_hash[:16]), actor.username, ACTOR_API_KEY
    return None, None, ACTOR_ANONYMOUS


async def record_audit(
    state: ServerState,
    request: Request,
    action: str,
    *,
    actor: PlatformUser | ApiKeyRecord | None = None,
    actor_name: str | None = None,
    result: str = RESULT_SUCCESS,
    target_type: str | None = None,
    target_id: str | None = None,
    status_code: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """写一行审计日志。**写失败只告警，绝不影响业务请求。**"""
    from .auth_routes import client_ip  # 延迟导入：避免 auth_routes <-> audit 循环依赖

    actor_id, name, actor_type = actor_of(actor)
    if actor is None and actor_name:
        name = actor_name
    entry = build_entry(
        action,
        result=result,
        actor_id=actor_id,
        actor_name=name,
        actor_type=actor_type,
        target_type=target_type,
        target_id=target_id,
        status_code=status_code,
        ip=client_ip(request, state),
        user_agent=request.headers.get("user-agent"),
        detail=detail,
    )
    try:
        await anyio.to_thread.run_sync(lambda: state.store.add_audit(entry))
    except Exception:  # noqa: BLE001 - 日志不能把请求搞挂
        _log.warning("写审计日志失败：%s", action, exc_info=True)


__all__ = ["actor_of", "record_audit"]
