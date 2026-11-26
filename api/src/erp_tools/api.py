"""库入口：给其他程序调用（不涉及文件落盘、不 print）。

示例：
    from erp_tools.api import connect, fetch_quote, fetch_attendance

    auth = connect(username="21146", password="654321")
    data = fetch_quote(auth, "33e3099b-...")
    rows = fetch_attendance(auth, "2026-11")
"""

from __future__ import annotations

from typing import Iterator

from .core.auth import AuthManager, ensure_authenticated
from .core.config import ErpConfig
from .core.session import ErpClient
from .tools.registry import build_tool

__all__ = [
    "connect",
    "qxid_map",
    "query_quotes",
    "fetch_quote",
    "fetch_quote_by_bdbh",
    "iter_quotes",
    "fetch_attendance",
]


def connect(*, username: str | None = None, password: str | None = None,
            host: str | None = None, env_file: str | None = None,
            login: bool = True, interactive: bool = False,
            captcha: str | None = None) -> AuthManager:
    """建立并（默认）验证登录态，返回可复用的 AuthManager。

    env_file=None 时自动查找 `./.env` 与 `%APPDATA%\\erp-tools\\.env`。
    """
    cfg = ErpConfig.from_env(env_file)
    kwargs = {}
    if username:
        kwargs["username"] = username
    if password:
        kwargs["password"] = password
    if host:
        kwargs["host"] = host
        kwargs["captcha_host"] = ""
    if kwargs:
        cfg = ErpConfig(**{**cfg.__dict__, **kwargs})

    if not login:
        return AuthManager(ErpClient(cfg), interactive=interactive)

    auth = ensure_authenticated(cfg, interactive=interactive)
    if captcha:
        auth.login(captcha=captcha)
    return auth


def qxid_map(auth: AuthManager, *, refresh: bool = False) -> dict[str, str]:
    return auth.qxid_map(refresh=refresh)


def fetch_quote(auth: AuthManager, record_id: str, *,
                fallback_ywy: str | None = None) -> dict:
    """取一条报价需求单，返回 ERP 订单需求字段字典。"""
    return build_tool("quote", auth).fetch_one(auth.client, record_id, fallback_ywy)


def fetch_quote_by_bdbh(auth: AuthManager, bdbh: str) -> dict:
    """按【表单编号】取详情（如 BJXQ261105013）：先查列表拿记录ID，再取详情页。"""
    finder = build_tool("query", auth)
    row = finder.find_by_bdbh(bdbh)
    if not row or not row.get("记录ID"):
        from .core.errors import ErpError

        raise ErpError(f"表单编号 {bdbh} 在 ERP 里查不到（可能已归档或权限不足）")
    return fetch_quote(auth, str(row["记录ID"]))


def query_quotes(auth: AuthManager, *, from_date: str | None = None,
                 to_date: str | None = None, where: str | None = None,
                 key: str = "", field: str = "", limit: int | None = None) -> list[dict]:
    """查询客户报价需求【列表】（不落盘）。

    where 语法与 CLI 一致：`客户名称~ADG,报价状态=需求审批`。
    """
    tool = build_tool("query", auth)
    rows, _headers, _page = tool.collect(from_date=from_date, to_date=to_date,
                                         where=where, key=key, field=field, limit=limit)
    return rows


def iter_quotes(auth: AuthManager, *, csv: str | None = None, ids: list[str] | None = None,
                limit: int | None = None, workers: int | None = None) -> Iterator[dict]:
    """批量采集（不落盘，直接产出记录）。"""
    tool = build_tool("quote", auth)
    yield from tool.run(csv=csv, ids=ids, out=None, limit=limit, workers=workers)


def fetch_attendance(auth: AuthManager, month: str, *, start_day: str = "01",
                     end_day: str = "31", username: str | None = None,
                     page_size: int | None = None) -> list[dict]:
    """查询某员工某月的考勤明细（行字典列表）。"""
    tool = build_tool("attendance", auth)
    return list(tool.run(month=month, start_day=start_day, end_day=end_day,
                         users=[username] if username else None, page_size=page_size))
