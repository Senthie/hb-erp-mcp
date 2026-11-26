"""审计日志：谁、在什么时间、通过什么入口、做了什么、结果如何。

落在数据库里（PostgreSQL；本地/测试用 SQLite 同构），管理员后台可以按
动作 / 操作者 / 结果 / 时间范围检索。

原则：

* **只记录可审计的元信息**，绝不写密码、api-key 明文、cookie；
  `detail` 里只放工号、备注、token id 前缀这类定位信息。
* 写日志失败**不能**影响业务请求（调用方统一 try/except 兜底）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 结果
RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"
RESULTS = (RESULT_SUCCESS, RESULT_FAILURE)

#: 操作者类型
ACTOR_USER = "platform_user"   # 平台账号（cookie 会话）
ACTOR_API_KEY = "api_key"      # 用 MCP token 调接口
ACTOR_ANONYMOUS = "anonymous"  # 未登录（登录尝试、服务注册等）
ACTOR_SYSTEM = "system"        # 进程内部动作

#: 已知动作（前端筛选下拉用它；新增动作也允许，日志表不做枚举约束）
ACTIONS = (
    "platform.register",
    "platform.login",
    "platform.logout",
    "platform.erp.bind",
    "platform.erp.credential_delete",
    "platform.token.create",
    "platform.token.revoke",
    "auth.login",
    "erp.relogin",
    "mcp.register",
    "admin.user.update",
    "admin.user.delete",
    "admin.user.unbind_erp",
    "admin.user.revoke_tokens",
)

ACTION_LABELS = {
    "platform.register": "平台注册",
    "platform.login": "平台登录",
    "platform.logout": "平台登出",
    "platform.erp.bind": "绑定 ERP",
    "platform.erp.credential_delete": "删除已存 ERP 密码",
    "platform.token.create": "申请 Token",
    "platform.token.revoke": "撤销 Token",
    "auth.login": "ERP 直连换 Key",
    "erp.relogin": "ERP 会话自动重登",
    "mcp.register": "登记 MCP 服务",
    "admin.user.update": "管理员改用户",
    "admin.user.delete": "管理员删用户",
    "admin.user.unbind_erp": "管理员解绑 ERP",
    "admin.user.revoke_tokens": "管理员撤销 Token",
}

MAX_TEXT = 256


def _clip(value: Any, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def build_entry(
    action: str,
    *,
    result: str = RESULT_SUCCESS,
    actor_id: str | None = None,
    actor_name: str | None = None,
    actor_type: str = ACTOR_ANONYMOUS,
    target_type: str | None = None,
    target_id: str | None = None,
    status_code: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装一行待写入的审计日志（不含 id / created_at，由库生成）。"""
    return {
        "action": _clip(action) or "unknown",
        "result": result if result in RESULTS else RESULT_FAILURE,
        "actor_id": _clip(actor_id),
        "actor_name": _clip(actor_name),
        "actor_type": _clip(actor_type) or ACTOR_ANONYMOUS,
        "target_type": _clip(target_type),
        "target_id": _clip(target_id),
        "status_code": int(status_code) if status_code is not None else None,
        "ip": _clip(ip),
        "user_agent": _clip(user_agent),
        "detail": detail or {},
    }


@dataclass(frozen=True)
class AuditEntry:
    id: int | None = None
    created_at: str = ""
    action: str = ""
    result: str = RESULT_SUCCESS
    actor_id: str | None = None
    actor_name: str | None = None
    actor_type: str = ACTOR_ANONYMOUS
    target_type: str | None = None
    target_id: str | None = None
    status_code: int | None = None
    ip: str | None = None
    user_agent: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> "AuditEntry":
        raw_detail = row.get("detail")
        if isinstance(raw_detail, str):
            import json

            try:
                raw_detail = json.loads(raw_detail)
            except ValueError:
                raw_detail = {}
        if not isinstance(raw_detail, dict):
            raw_detail = {}
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            created_at=str(row.get("created_at") or ""),
            action=str(row.get("action") or ""),
            result=str(row.get("result") or RESULT_SUCCESS),
            actor_id=row.get("actor_id"),
            actor_name=row.get("actor_name"),
            actor_type=str(row.get("actor_type") or ACTOR_ANONYMOUS),
            target_type=row.get("target_type"),
            target_id=row.get("target_id"),
            status_code=int(row["status_code"]) if row.get("status_code") is not None else None,
            ip=row.get("ip"),
            user_agent=row.get("user_agent"),
            detail=dict(raw_detail),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "action": self.action,
            "action_label": ACTION_LABELS.get(self.action, self.action),
            "result": self.result,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "actor_type": self.actor_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "status_code": self.status_code,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "detail": self.detail,
        }


__all__ = [
    "ACTIONS",
    "ACTION_LABELS",
    "ACTOR_ANONYMOUS",
    "ACTOR_API_KEY",
    "ACTOR_SYSTEM",
    "ACTOR_USER",
    "RESULTS",
    "RESULT_FAILURE",
    "RESULT_SUCCESS",
    "AuditEntry",
    "build_entry",
]
