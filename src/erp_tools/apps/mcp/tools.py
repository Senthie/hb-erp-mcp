"""MCP 工具注册（Streamable HTTP）。

三条硬约束：

1. **工具都是同步 `def`**。`requests` 是阻塞库，交给 MCP SDK 的线程池执行，
   绝不能直接跑在事件循环里。
2. **身份来自 contextvar**（`BearerAuthMiddleware` 写入），不通过工具参数传递，
   避免调用方伪造用户名。
3. **返回体积有闸门**。这是给 LLM 用的接口，一次吐几千行既费钱也没用。
"""

from __future__ import annotations

import functools
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ... import __version__
from ...core.auth import normalize_page_path
from ...core.errors import ErpError
from ...runtime.context import require_record
from ...runtime.sessions import SessionExpired, UserSession
from ...tools.attendance import AttendanceTool
from ...tools.quote_create import QuoteCreateService
from ...tools.quote_index import QuoteListTool
from ...tools.quote_index.parser import ACTION_COLUMNS
from ...tools.quote_request import QuoteRequestTool
from ..server.state import ServerState

#: 单次返回的默认条数（LLM 友好）
DEFAULT_RECORDS = 50
#: 硬上限：超过就静默截断并置 truncated
MAX_RECORDS = 500

#: 登录响应 / capabilities 里使用的页面键
PAGE_KEYS = {
    "quote_index": "BaoJia/BJ_KHBJXQ/Index.aspx",
    "quote": "BaoJia/BJ_KHBJXQ/Add.aspx",
    "attendance": "RenShi/RS_KQDA_M/Add.aspx",
}


# ══════════════════ 内部工具 ══════════════════
def _tool_errors(fn):
    """把可预期的 ERP 异常翻译成 `ToolError`。

    MCP SDK 对**未预期**异常只会给客户端一句 `Error executing tool <name>`，
    真正原因只进服务端日志。所以凡是业务上说得清的错误，都必须显式包成
    `ToolError`，让模型和用户能看到到底是什么问题。
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except ErpError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _session(state: ServerState) -> UserSession:
    """取当前 api-key 绑定的 ERP 会话；失效则顺手把 key 作废。"""
    record = require_record()
    try:
        return state.provider.require(record.username)
    except SessionExpired as exc:
        state.store.revoke_hash(record.key_hash)
        raise ToolError(
            f"{exc}。api-key 已一并作废，请重新调用 POST /v1/auth/login 获取新的 api-key。"
        ) from exc


def _clamp(limit: int | None) -> int:
    try:
        value = int(limit if limit is not None else DEFAULT_RECORDS)
    except (TypeError, ValueError):
        value = DEFAULT_RECORDS
    return max(1, min(value, MAX_RECORDS))


def _pick_columns(spec: str | None, available: list[str]) -> tuple[list[str], list[str]]:
    """返回 (保留的列, 请求了但不存在的列)。"""
    if not spec:
        return list(available), []
    wanted = [c.strip() for c in str(spec).replace("，", ",").split(",") if c.strip()]
    keep = [c for c in wanted if c in available]
    unknown = [c for c in wanted if c not in available]
    return (keep or list(available)), unknown


def _flatten(record: dict) -> dict:
    """把考勤里的 `_user/_month/_page` 转成不带下划线的普通字段。"""
    out: dict[str, Any] = {}
    for key, value in record.items():
        out[key.lstrip("_") if key.startswith("_") else key] = value
    return out


# ══════════════════ 注册 ══════════════════
def register_tools(mcp: MCPServer, state: ServerState) -> None:
    """把 ERP 能力登记成 MCP 工具。"""

    # ---------- 身份 ----------
    @mcp.tool(
        name="erp_whoami",
        description=(
            "返回当前 api-key 对应的 ERP 身份与会话状态。"
            "erp_alive=false 表示 ERP 会话已过期，需要重新调用 /v1/auth/login。"
        ),
    )
    @_tool_errors
    def erp_whoami() -> dict[str, Any]:
        record = require_record()
        session = _session(state)
        return {
            "username": session.username,
            "user_id": session.user_id,
            "erp_alive": state.provider.is_alive(session.username),
            "key_created_at": record.created_at,
        }

    # ---------- 能力 ----------
    @mcp.tool(
        name="erp_capabilities",
        description=(
            "返回当前账号在 ERP 里能访问哪些页面（QXID 能否从菜单探测到）。"
            "missing_pages 为空说明三个页面都可访问；字段级缺失记录在每条数据的 _diagnostics 里。"
        ),
    )
    @_tool_errors
    def erp_capabilities() -> dict[str, Any]:
        session = _session(state)
        pages = {normalize_page_path(k): str(v) for k, v in (session.pages or {}).items()}
        available = {name: pages.get(normalize_page_path(path)) for name, path in PAGE_KEYS.items()}
        missing = [name for name, qxid in available.items() if not qxid]
        return {
            "username": session.username,
            "pages": available,
            "missing_pages": missing,
            "note": (
                "missing_pages 非空通常意味着当前账号没有该页面权限；"
                "此时调用对应工具会失败，请改用能访问的页面。"
            ),
        }

    # ---------- 报价需求：列表 ----------
    @mcp.tool(
        name="erp_quote_list",
        description=(
            "查询客户报价需求【列表】（BJ_KHBJXQ/Index.aspx）。"
            "服务端只支持日期区间 + 一个关键字，其余条件用 where 在本地过滤，"
            "语法如 '客户名称~ADG,报价状态=需求审批'。"
            f"默认返回 {DEFAULT_RECORDS} 条，硬上限 {MAX_RECORDS} 条。"
        ),
    )
    @_tool_errors
    def erp_quote_list(
        from_date: str | None = None,
        to_date: str | None = None,
        where: str | None = None,
        customer: str | None = None,
        sales: str | None = None,
        status: str | None = None,
        key: str = "",
        field: str = "",
        columns: str | None = None,
        limit: int = DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        session = _session(state)
        size = _clamp(limit)
        tool = QuoteListTool(session.auth)
        with state.provider.slot():
            rows, headers, page = tool.collect(
                from_date=from_date, to_date=to_date, key=key or "", field=field or "",
                where=where, customer=customer, sales=sales, status=status, limit=size,
            )
        available = [h for h in headers if h not in ACTION_COLUMNS]
        picked, unknown = _pick_columns(columns, available)
        return {
            "columns": picked,
            "unknown_columns": unknown,
            "records": [{c: row.get(c) for c in picked} for row in rows],
            "count": len(rows),
            "total": page.total,
            "pages": page.pages,
            "truncated": bool(page.total and len(rows) < page.total),
        }

    # ---------- 报价需求：详情 ----------
    @mcp.tool(
        name="erp_quote_detail",
        description=(
            "按记录 ID 或表单编号取一条报价需求单的完整字段（BJ_KHBJXQ/Add.aspx）。"
            "record_id 与 bdbh 至少给一个；只给 bdbh 时会先用单号里的日期反查记录 ID。"
        ),
    )
    @_tool_errors
    def erp_quote_detail(
        record_id: str | None = None,
        bdbh: str | None = None,
    ) -> dict[str, Any]:
        session = _session(state)
        if not record_id and not bdbh:
            raise ToolError("record_id 与 bdbh 至少提供一个")

        with state.provider.slot():
            resolved = record_id
            if not resolved:
                finder = QuoteListTool(session.auth)
                row = finder.find_by_bdbh(str(bdbh).strip())
                resolved = str((row or {}).get("记录ID") or "")
                if not resolved:
                    raise ToolError(
                        f"表单编号 {bdbh} 查不到（可能已归档，或当前账号无该单据权限）"
                    )
            record = QuoteRequestTool(session.auth).fetch_one(session.auth.client, resolved)
        return {"record_id": resolved, "record": record}

    # ---------- 考勤 ----------
    @mcp.tool(
        name="erp_attendance",
        description=(
            "查询员工月度考勤（RS_KQDA_M）。month 形如 2026-11；"
            "users 为空则用当前登录账号。注意 ERP 页面的起止日下拉只有 01-30，"
            "超出会被收敛到 30（传 31 会让服务端只回 1 号一行）。默认返回 "
            f"{DEFAULT_RECORDS} 行，硬上限 {MAX_RECORDS} 行。"
        ),
    )
    @_tool_errors
    def erp_attendance(
        month: str,
        start_day: str = "01",
        end_day: str = "30",
        users: list[str] | None = None,
        limit: int = DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        session = _session(state)
        size = _clamp(limit)
        tool = AttendanceTool(session.auth)

        collected: list[dict] = []
        truncated = False
        with state.provider.slot():
            for rec in tool.run(month=month, start_day=start_day, end_day=end_day,
                                users=users, out=None, jsonl=None):
                if len(collected) >= size:
                    truncated = True
                    break
                collected.append(_flatten(rec))

        columns = list(collected[0].keys()) if collected else []
        return {
            "columns": columns,
            "records": collected,
            "count": len(collected),
            "truncated": truncated,
        }

    # ---------- 新增报价需求：页面结构 ----------
    @mcp.tool(
        name="erp_quote_new_form",
        description=(
            "【只读】返回「新增客户报价需求」页(BJ_KHBJXQ/Add.aspx)的字段结构："
            "表头字段及其只读标记、三个下拉(业务员/跟单员/单价体系)的「名称 -> GUID」映射、"
            "textarea 列表。新建单据前先用它确定能填什么、业务员怎么传。"
        ),
    )
    @_tool_errors
    def erp_quote_new_form() -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).describe_form()

    # ---------- 新增报价需求：产品明细子页的完整字段清单 ----------
    @mcp.tool(
        name="erp_quote_product_form",
        description=(
            "【只读】返回产品明细子页(Add_CPZL.aspx)的字段清单与**上传用 JSON Schema**："
            "control(ERP 提交名) / alias(JSON 字段名) / 中文标签 / 类型 / "
            "visible(DOM 首屏可见) / writable / uploadable / 已知下拉选项。"
            "只有 uploadable=true（可见且可写，51 个）的字段能通过 erp_quote_create 写入；"
            "折叠面板里的 142 个隐藏字段会被拒绝。"
            "不传 record_id 时返回静态注册表；传 record_id 会打开真实页面，"
            "带上当前值、真实下拉选项与只读状态。返回里的 upload 含 json_schema + example，"
            "categories 是新产品的产品类别树（code / name / lbid）。"
        ),
    )
    @_tool_errors
    def erp_quote_product_form(
        record_id: str | None = None,
        cpid: str | None = None,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).describe_product_form(
                record_id, cpid=cpid,
            )

    # ---------- 新增报价需求：下拉选项 ----------
    @mcp.tool(
        name="erp_quote_options",
        description=(
            "【只读】列出「新增客户报价需求」页里当前账号可选的下拉项："
            "业务员(sales)、跟单员(merchandiser)、单价体系(price_system)。"
            "每项返回 name(显示名) 与 id(提交用 GUID)。另给 employees 合并 名单，"
            "标明每个人能否当业务员/跟单员。选项按账号权限实时读取，缓存 5 分钟。"
        ),
    )
    @_tool_errors
    def erp_quote_options(
        field: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """field 可选 sales / merchandiser / price_system（也接受中文名）。"""
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).list_options(field, refresh=refresh)

    # ---------- 新增报价需求：客户检索 ----------
    @mcp.tool(
        name="erp_quote_customer_search",
        description=(
            "【只读】在客户资料选择页(Base_KHZL_Select.aspx)按名称片段检索客户，"
            "返回 customer_id(GUID)、编码、简称、以及该客户默认的业务员/跟单员/折扣率。"
            "keyword 留空则返回前若干条。拿到的 customer_id 可直接交给 erp_quote_prepare。"
        ),
    )
    @_tool_errors
    def erp_quote_customer_search(
        keyword: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).search_customers(keyword, limit=limit)

    # ---------- 新增报价需求：提交体预览 ----------
    @mcp.tool(
        name="erp_quote_prepare",
        description=(
            "【只读，不会落库】校验并组装一条「新增客户报价需求」将要提交的字段："
            "客户、业务员、至少一条产品明细(name/spec/qty)。"
            "返回 ok/errors/warnings 与 header 预览。真正落库请接着调 erp_quote_create"
            "（confirm=true），不要把这个预览当成已创建。"
        ),
    )
    @_tool_errors
    def erp_quote_prepare(
        customer: str | None = None,
        sales: str | None = None,
        merchandiser: str | None = None,
        price_system: str | None = None,
        products: list[dict[str, Any]] | None = None,
        contact: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        fax: str | None = None,
        discount: str | None = None,
        remark: str | None = None,
        product_memo: str | None = None,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).prepare(
                customer=customer, sales=sales, merchandiser=merchandiser,
                price_system=price_system, products=products, contact=contact,
                phone=phone, mobile=mobile, fax=fax, discount=discount,
                remark=remark, product_memo=product_memo,
            )


    # ---------- 新增报价需求：真正写入 ----------
    @mcp.tool(
        name="erp_quote_create",
        description=(
            "【会写入 ERP】新建一条客户报价需求（主表 + 1 条产品明细，含产品数量）。"
            "confirm=false（默认）时只做校验并返回 needs_confirmation，不会落库；"
            "传 confirm=true 才会真正保存，成功返回表单编号(BDBH)与记录ID。"
            "产品名称会先在【产品档案】里模糊检索，必须唯一匹配才继续。"
            "新产品（档案里搜不到）请同时传 products[0].product_category"
            "（如 \"27.01\" / \"27.01;普通BB书\" / \"普通BB书\"），明细行不要求产品档案存在。"
            "明细字段只接受 DOM 首屏可见的 51 个（见 erp_quote_product_form），"
            "折叠面板里的隐藏字段会被拒绝；键名拼错也会直接报错。"
            "写完后会回读校验（明细行 CPMC + 产品数量）；任一项不符就返回 status=partial，"
            "detail_error 说明卡在哪一步，不会假报成功。"
            "已知限制：多语言数量明细不落库（只写汇总值 txtCPSL）。"
        ),
    )
    @_tool_errors
    def erp_quote_create(
        customer: str | None = None,
        products: list[dict[str, Any]] | None = None,
        sales: str | None = None,
        merchandiser: str | None = None,
        price_system: str | None = None,
        contact: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        fax: str | None = None,
        discount: str | None = None,
        remark: str | None = None,
        product_memo: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).create(
                customer=customer, sales=sales, merchandiser=merchandiser,
                price_system=price_system, products=products, contact=contact,
                phone=phone, mobile=mobile, fax=fax, discount=discount,
                remark=remark, product_memo=product_memo, confirm=confirm,
            )


    # ---------- 报价需求：明细行 / 修改 ----------
    @mcp.tool(
        name="erp_quote_details",
        description=(
            "【只读】列出某条报价需求单的产品明细行，返回每行的 cpid（修改/删除该行时要用）。"
            "record_id 可先用 erp_quote_list 查到。"
        ),
    )
    @_tool_errors
    def erp_quote_details(record_id: str) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).list_details(record_id)

    @mcp.tool(
        name="erp_quote_update",
        description=(
            "【会写入 ERP】修改报价需求单的表头（备注/联系人/电话/手机/传真/折扣率/"
            "产品描述/业务员/跟单员）。内部会先发 btnModify 解锁 [保存]，再发 btnSave。"
            "confirm=false（默认）只返回将要修改的字段，不写库。"
        ),
    )
    @_tool_errors
    def erp_quote_update(
        record_id: str,
        remark: str | None = None,
        contact: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        fax: str | None = None,
        discount: str | None = None,
        product_memo: str | None = None,
        sales: str | None = None,
        merchandiser: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).update_quote(
                record_id, remark=remark, contact=contact, phone=phone,
                mobile=mobile, fax=fax, discount=discount,
                product_memo=product_memo, sales=sales,
                merchandiser=merchandiser, confirm=confirm,
            )

    @mcp.tool(
        name="erp_quote_update_product",
        description=(
            "【会写入 ERP】修改某条产品明细（cpid 来自 erp_quote_details）。"
            "可改产品名称/规格/数量。confirm=false（默认）只返回待修改字段。"
            "保存后会回读名称与产品数量复核，不一致会返回 status=failed。"
        ),
    )
    @_tool_errors
    def erp_quote_update_product(
        record_id: str,
        cpid: str,
        name: str | None = None,
        spec: str | None = None,
        qty: float | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).update_product(
                record_id, cpid, name=name, spec=spec, qty=qty, confirm=confirm,
            )

    # ---------- 新增报价需求：删除 ----------
    @mcp.tool(
        name="erp_quote_delete",
        description=(
            "【会删除 ERP 数据】按记录ID删除一条客户报价需求单。"
            "confirm=false（默认）只检查单据是否还存在并返回单号，不会删除；"
            "传 confirm=true 才真正删除，删除不可恢复。"
            "记录ID 可先用 erp_quote_list 查到。"
        ),
    )
    @_tool_errors
    def erp_quote_delete(record_id: str, confirm: bool = False) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).delete_quote(record_id, confirm=confirm)


def build_mcp_server(state: ServerState) -> MCPServer:
    """构造 MCPServer 并注册全部工具。"""
    mcp = MCPServer(
        name="erp-tools",
        title="华博 ERP 工具",
        version=__version__,
        instructions=(
            "所有工具都要求 HTTP 头 Authorization: Bearer <api-key>。"
            "先用 erp_capabilities 了解当前账号能访问哪些页面，再调用具体工具。"
            "结果体积有限（默认 50 条，上限 500 条），需要更多请缩小 where 或分页。"
            "新增报价需求的写入链已开通：erp_quote_prepare 预览 → erp_quote_create 落库"
            "→ erp_quote_details 拿 cpid → erp_quote_update / erp_quote_update_product 修改"
            "→ erp_quote_delete 删除；所有写入工具都要 confirm=true 才生效。"
        ),
    )
    register_tools(mcp, state)
    return mcp


__all__ = [
    "DEFAULT_RECORDS", "MAX_RECORDS", "PAGE_KEYS",
    "build_mcp_server", "register_tools",
]
