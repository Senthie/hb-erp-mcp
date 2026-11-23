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
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ... import __version__
from ...core.auth import normalize_page_path
from ...core.errors import ErpError
from ...runtime.context import require_record
from ...runtime.sessions import SessionExpired, UserSession
from ...tools.attendance import AttendanceTool
from ...tools.grid_module import (
    CostingListTool,
    ProductionDetailTool,
    ProductionListTool,
    ProductionNodeTool,
    PurchaseDetailTool,
    PurchaseListTool,
    PurchaseNodeTool,
    SalesOrderAttachmentTool,
    SalesOrderDetailTool,
    SalesOrderListTool,
    SalesOrderNodeTool,
)
from ...tools.quote_create import QuoteCreateService
from ...tools.quote_index import QuoteListTool
from ...tools.quote_index.parser import ACTION_COLUMNS
from ...tools.quote_price import PriceDetailTool, PriceListTool, PriceNodeTool
from ...tools.quote_request import QuoteRequestTool
from ..server.state import ServerState
from .schemas import QuoteProductInput

#: 单次返回的默认条数（LLM 友好）
DEFAULT_RECORDS = 50
#: 硬上限：超过就静默截断并置 truncated
MAX_RECORDS = 500

#: 登录响应 / capabilities 里使用的页面键
PAGE_KEYS = {
    "quote_index": "BaoJia/BJ_KHBJXQ/Index.aspx",
    "quote": "BaoJia/BJ_KHBJXQ/Add.aspx",
    "price_index": "BaoJia/BJ_KHBJ/Index.aspx",
    "price": "BaoJia/BJ_KHBJ/Add.aspx",
    "attendance": "RenShi/RS_KQDA_M/Add.aspx",
}


def _proxy_url(url: str, name: str = "") -> str:
    """内网直链 → 网关代理链接（跨网段可下载）；不可代理时返回空串。"""
    if not url:
        return ""
    try:
        from ...runtime import file_proxy

        return file_proxy.build_url(url, name=name)
    except (RuntimeError, OSError, ValueError):
        return ""


def _module_capabilities(pages: dict[str, str]) -> dict[str, dict[str, Any]]:
    """四个模块（核价/施工单/采购订单/客户订单）的页面权限。

    ⚠ **不能直接用菜单探测值**：同一路径挂在多个菜单下（`BJ_KHBJ/Index.aspx` 既是
    客户报价 860 又是产品核价 1281；`SC_SCSGD/Index.aspx` 既是 740 又是 983），
    `parse_menu_urls` 用 setdefault 只留第一个 → 会报出**别的菜单**的 QXID。
    所以这里只判断「菜单里有没有这个路径」，QXID 用 `ModuleSpec` 里的实测值。

    采购订单的详情页没有独立菜单（从列表行双击进 `Add.aspx`），
    因此它的可用性跟列表页一致。
    """
    from erp_tools.tools.grid_module import MODULES

    out: dict[str, dict[str, Any]] = {}
    for spec in MODULES.values():
        list_ok = normalize_page_path(spec.list_path) in pages
        out[spec.key] = {
            "label": spec.label,
            "list_path": spec.list_path,
            "list_qxid": spec.fallback_qxid,
            "list_available": list_ok,
            "detail_path": spec.detail_path or None,
            "detail_reached_from": "list_row" if spec.detail_path else None,
            "note": (
                "QXID 为实测值（菜单里同路径可能对应多个菜单，探测值不可用）"
                if list_ok else "菜单里没有这个页面，当前账号可能无权限"
            ),
        }
    return out


def _grid_response(rows: list[dict], headers: list[str], page, note: str,
                   columns: str | None = None) -> dict[str, Any]:
    """列表类工具的统一返回形状（与 erp_price_list 保持一致）。"""
    available = [h for h in headers if h not in ACTION_COLUMNS]
    if "记录ID" not in available:
        available.append("记录ID")
    picked, unknown = _pick_columns(columns, available)
    return {
        "columns": picked,
        "unknown_columns": unknown,
        "records": [{c: row.get(c) for c in picked} for row in rows],
        "count": len(rows),
        "total": getattr(page, "total", 0),
        "pages": getattr(page, "pages", 1),
        "truncated": bool(getattr(page, "total", 0) and len(rows) < getattr(page, "total", 0)),
        "note": note,
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
            "返回当前账号能访问哪些页面：pages=基础模块（报价需求/客户报价/考勤）的菜单 QXID；"
            "modules=产品核价/生产施工单/采购订单三个模块的**实测** QXID 与可用性。"
            "missing_pages 为空说明基础页面齐全。"
        ),
    )
    @_tool_errors
    def erp_capabilities() -> dict[str, Any]:
        session = _session(state)
        pages = {normalize_page_path(k): str(v) for k, v in (session.pages or {}).items()}
        available = {name: pages.get(normalize_page_path(path)) for name, path in PAGE_KEYS.items()}
        missing = [name for name, qxid in available.items() if not qxid]
        modules = _module_capabilities(pages)
        return {
            "username": session.username,
            "pages": available,
            "missing_pages": missing,
            "modules": modules,
            "module_missing": [k for k, v in modules.items() if not v["list_available"]],
            "note": (
                "modules 里的 QXID 是实测值：同一 aspx 常挂在多个菜单下"
                "（BJ_KHBJ/Index.aspx = 客户报价 860 + 产品核价 1281），菜单探测只留第一个，不可用。"
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
        from_date: Annotated[
            str | None,
            Field(description="制单起始日期，YYYY-MM-DD；默认本月 1 号",
                  examples=["2026-11-01"]),
        ] = None,
        to_date: Annotated[
            str | None,
            Field(description="制单结束日期，YYYY-MM-DD；默认今天",
                  examples=["2026-11-30"]),
        ] = None,
        where: Annotated[
            str | None,
            Field(
                description=(
                    "本地过滤表达式，多个条件用英文逗号分隔；语法 `字段 操作符 值`，"
                    "操作符支持 = != ~ > < >= <=（~ 表示包含）"
                ),
                examples=["客户名称~ADG,报价状态=需求审批"],
            ),
        ] = None,
        customer: Annotated[
            str | None,
            Field(description="客户名称片段（等价于 where 里的 客户名称~X）", examples=["ADG"]),
        ] = None,
        sales: Annotated[
            str | None,
            Field(description="业务员姓名片段（等价于 业务员~X）", examples=["陈"]),
        ] = None,
        status: Annotated[
            str | None,
            Field(description="报价状态片段（等价于 报价状态~X）", examples=["需求审批"]),
        ] = None,
        key: Annotated[
            str,
            Field(description="服务端关键字（配合 field 使用，减少传输量）",
                  examples=["BJXQ2611"]),
        ] = "",
        field: Annotated[
            str,
            Field(
                description=(
                    "关键字匹配的列名（中文），如 表单编号 / 客户名称 / 产品名称 / 香港单号 / 业务员"
                ),
                examples=["表单编号"],
            ),
        ] = "",
        columns: Annotated[
            str | None,
            Field(
                description="只返回指定列，逗号分隔；列名用返回里的 columns",
                examples=["记录ID,表单编号,客户名称,报价状态"],
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(description=f"返回条数，1~{MAX_RECORDS}；超出会被静默钳到上限并置 truncated"),
        ] = DEFAULT_RECORDS,
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
        if "记录ID" not in available:
            available.append("记录ID")
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
        record_id: Annotated[
            str | None,
            Field(description="记录 ID（GUID），来自 erp_quote_list 的 记录ID 列"),
        ] = None,
        bdbh: Annotated[
            str | None,
            Field(description="表单编号，形如 BJXQ261112001；会给单号里的日期反查记录 ID",
                  examples=["BJXQ261112001"]),
        ] = None,
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

    # ---------- 客户报价：列表 ----------
    @mcp.tool(
        name="erp_price_list",
        description=(
            "查询客户报价列表（BJ_KHBJ）。⚠ 一行=一个报价产品行，同一表单编号可能多行，"
            "用 记录ID 定位单行。date_type 选日期字段；filters 传服务端下拉；"
            "其余用 where 本地过滤。"
            f"默认 {DEFAULT_RECORDS} 行，上限 {MAX_RECORDS}。"
        ),
    )
    @_tool_errors
    def erp_price_list(
        from_date: Annotated[str | None, Field(description="起 YYYY-MM-DD")] = None,
        to_date: Annotated[str | None, Field(description="止 YYYY-MM-DD")] = None,
        date_type: Annotated[str, Field(description="制单/交货/离厂日期")] = "制单日期",
        where: Annotated[
            str | None,
            Field(description="本地过滤，逗号分隔；操作符 = != ~ > < >= <=（~ 为包含）"),
        ] = None,
        customer: Annotated[str | None, Field(description="客户名称片段")] = None,
        sales: Annotated[str | None, Field(description="业务员片段")] = None,
        status: Annotated[str | None, Field(description="报价状态片段")] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(
                description=(
                    "服务端下拉过滤，值可写中文。键：审批状态/报废状态/确认情况/"
                    "签订单/查询方式/客户属性/模板/预计报价月份/报价分类"
                )
            ),
        ] = None,
        key: Annotated[str, Field(description="服务端关键字（配合 field）")] = "",
        field: Annotated[
            str, Field(description="关键字字段：表单编号/客户名称/产品名称/香港单号…")
        ] = "",
        columns: Annotated[str | None, Field(description="只返回这些列，逗号分隔")] = None,
        limit: Annotated[int, Field(description=f"1~{MAX_RECORDS}，超出截断")] = DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        session = _session(state)
        size = _clamp(limit)
        tool = PriceListTool(session.auth)
        with state.provider.slot():
            rows, headers, page = tool.collect(
                from_date=from_date, to_date=to_date, date_type=date_type,
                key=key or "", field=field or "",
                where=where, customer=customer, sales=sales, status=status,
                filters=filters, limit=size,
            )
        available = [h for h in headers if h not in ACTION_COLUMNS]
        if "记录ID" not in available:
            available.append("记录ID")
        picked, unknown = _pick_columns(columns, available)
        return {
            "columns": picked,
            "unknown_columns": unknown,
            "records": [{c: row.get(c) for c in picked} for row in rows],
            "count": len(rows),
            "total": page.total,
            "pages": page.pages,
            "truncated": bool(page.total and len(rows) < page.total),
            "note": "一行 = 一个报价产品行；同一表单编号可能多行，用 记录ID 定位单行",
        }

    # ---------- 客户报价：详情 ----------
    @mcp.tool(
        name="erp_price_detail",
        description=(
            "客户报价【详情】（BJ_KHBJ/Add.aspx，只读）：一个报价产品行的表头、产品字段、"
            "数量明细、部件明细（材料/尺寸/工序/辅料）、费用、产品描述、制单审核。"
            "命中多行时返回 candidates，再传 row 或 record_id。"
        ),
    )
    @_tool_errors
    def erp_price_detail(
        record_id: Annotated[str | None, Field(description="记录 ID")] = None,
        bdbh: Annotated[str | None, Field(description="表单编号或前缀")] = None,
        row: Annotated[int | None, Field(description="取第几行，1 起")] = None,
        include_description: Annotated[bool, Field(description="返回产品描述全文")] = True,
    ) -> dict[str, Any]:
        session = _session(state)
        tool = PriceDetailTool(session.auth)
        with state.provider.slot():
            rows = tool.resolve_records(record_id=record_id, bdbh=bdbh, row=row)
            if len(rows) > 1:
                return {
                    "status": "need_row",
                    "count": len(rows),
                    "candidates": [
                        {k: r.get(k) for k in ("记录ID", "表单编号", "产品名称", "报价状态")}
                        for r in rows
                    ],
                    "hint": "该表单编号有多行产品，请带 row(1 起) 或直接用 record_id 再调一次",
                }
            return tool.fetch_one(rows[0]["记录ID"],
                                  include_description=include_description)

    # ---------- 客户报价：子数据（点开才有） ----------
    @mcp.tool(
        name="erp_price_node",
        description=(
            "客户报价详情里「点开才有的子数据」（每个节点一次 POST+GET，1-2 秒）。"
            "node 取值：list=列出全部节点；all=整棵树(22 个节点一次取完，约 0.4 MB)；"
            "quantities=数量档；part=部件资料；part_materials=部件辅料；"
            "part_process=部件工序；part_machines=部件机台；product_check=送货资料；"
            "fees=费项明细；other_fees=其他费项；cp=产品资料；cp_process=产品工序；"
            "cp_materials=产品辅料；feedback=需求反馈；sgkb=手工开版；split_price=拆分价格；"
            "panel:description/material_list/manual_process/change_log/process_list/"
            "part_list/freight/price_mode=8 个折叠面板。"
            "部件级节点不传 bjid/index 时默认第一个部件；fees 默认不返回计算公式。"
        ),
    )
    @_tool_errors
    def erp_price_node(
        record_id: Annotated[str, Field(description="记录 ID（列表的 记录ID）")],
        node: Annotated[
            str,
            Field(description="节点名；list 查看全部，all 取整棵树"),
        ],
        cpid: Annotated[str | None, Field(description="产品明细 ID，不传自动取")] = None,
        bjid: Annotated[str | None, Field(description="部件 ID，不传取第一个部件")] = None,
        index: Annotated[int | None, Field(description="取第几个部件（1 起）")] = None,
        cpslid: Annotated[str | None,
                         Field(description="数量档 ID（默认 fees 汇总、other_fees 第一档）")] = None,
        include_formula: Annotated[
            bool, Field(description="fees 是否带计算公式（默认 false）")
        ] = False,
    ) -> dict[str, Any]:
        session = _session(state)
        tool = PriceNodeTool(session.auth)
        with state.provider.slot():
            if index is not None and not bjid:
                detail = PriceDetailTool(session.auth).fetch_one(
                    record_id, include_description=False)
                parts = [p for p in detail.get("parts", []) if p.get("bjid")]
                pos = int(index) - 1
                if pos < 0 or pos >= len(parts):
                    raise ToolError(f"index={index} 超出范围：该单共 {len(parts)} 个部件")
                bjid = parts[pos]["bjid"]
            result = tool.fetch(record_id, node, cpid=cpid or "", bjid=bjid or "",
                                cpslid=cpslid or "")
        if not include_formula:
            for group in result.get("groups", []):
                for rows in group.get("fees", {}).values():
                    for row in rows:
                        row.pop("公式", None)
            tree = result.get("nodes")
            if isinstance(tree, dict):
                for branch in tree.values():
                    for group in (branch or {}).get("groups", []):
                        for rows in group.get("fees", {}).values():
                            for row in rows:
                                row.pop("公式", None)
        return result

    # ---------- 数据页面清单（三个新模块） ----------
    @mcp.tool(
        name="erp_page_catalog",
        description=(
            "【只读】列出「列表-产品核价 / 列表-生产施工单 / 列表-采购订单 / 列表-客户订单」四个模块"
            "以及它们全部数据页面的实测清单：列表页、详情页、每个弹窗子页"
            "（子页文件名 + 打开它的按钮 + 中文说明），并标注哪些页已接入哪个 MCP 工具。"
            "module 留空返回全部四个模块。"
        ),
    )
    @_tool_errors
    def erp_page_catalog(
        module: Annotated[
            str,
            Field(description="模块：costing / production / purchase / sales_order；留空=全部"),
        ] = "",
    ) -> dict[str, Any]:
        from erp_tools.tools.grid_module import MODULES, get_module

        specs = [get_module(module)] if module.strip() else list(MODULES.values())
        tools = {
            "costing": {"list": "erp_costing_list", "detail": "erp_price_detail",
                        "node": "erp_price_node"},
            "production": {"list": "erp_production_list", "detail": "erp_production_detail",
                           "node": "erp_production_node"},
            "purchase": {"list": "erp_purchase_list", "detail": "erp_purchase_detail",
                         "node": "erp_purchase_node"},
            "sales_order": {"list": "erp_sales_order_list",
                            "detail": "erp_sales_order_detail",
                            "node": "erp_sales_order_node",
                            "attachment": "erp_sales_order_attachment"},
        }
        out = []
        for spec in specs:
            out.append({
                "module": spec.key,
                "label": spec.label,
                "row_note": spec.row_note,
                "pages": {
                    "list": {"path": spec.list_path, "qxid": spec.fallback_qxid,
                             "mcp": tools[spec.key]["list"]},
                    "detail": {"path": spec.detail_path or None,
                               "mcp": tools[spec.key]["detail"]},
                },
                "filters": spec.filter_controls,
                "date_fields": spec.date_fields,
                "nodes": [
                    {"node": node, "page": page, "button": button,
                     "label": label, "mcp": tools[spec.key]["node"]}
                    for node, (page, button, label) in spec.nodes.items()
                ] or "该模块的详情页没有弹窗数据子页（数据在页面内联网格里）",
            })
        return {"modules": out, "count": len(out)}

    # ---------- 产品核价：列表 ----------
    @mcp.tool(
        name="erp_costing_list",
        description=(
            "查询产品核价【列表】（BaoJia/BJ_KHBJ/Index.aspx，QXID=1281）。"
            "⚠ 一行=一个核价产品行（表单编号 CPHJ 开头），同一单号可能多行，用 记录ID 定位单行；"
            "详情与子数据用 erp_price_detail / erp_price_node（同一套页面）。"
            f"默认 {DEFAULT_RECORDS} 行，上限 {MAX_RECORDS}。"
        ),
    )
    @_tool_errors
    def erp_costing_list(
        from_date: Annotated[str | None, Field(description="起 YYYY-MM-DD")] = None,
        to_date: Annotated[str | None, Field(description="止 YYYY-MM-DD")] = None,
        date_type: Annotated[str, Field(description="制单日期 / 交货日期 / 离厂日期")] = "制单日期",
        where: Annotated[
            str | None,
            Field(description="本地过滤，逗号分隔；操作符 = != ~ > < >= <=（~ 为包含）"),
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(description="服务端下拉过滤，值可写中文：审批情况/报废状态/确认情况/"
                              "签订单/客户属性/模板/报价分类/查询方式"),
        ] = None,
        key: Annotated[str, Field(description="服务端关键字（配合 field）")] = "",
        field: Annotated[str, Field(description="关键字字段：表单编号/客户名称/产品名称…")] = "",
        key1: Annotated[str, Field(description="第二个服务端关键字")] = "",
        field1: Annotated[str, Field(description="第二个关键字字段")] = "",
        columns: Annotated[str | None, Field(description="只返回这些列，逗号分隔")] = None,
        limit: Annotated[int, Field(description=f"1~{MAX_RECORDS}，超出截断")] = DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        session = _session(state)
        size = _clamp(limit)
        with state.provider.slot():
            rows, headers, page = CostingListTool(session.auth).collect(
                from_date=from_date, to_date=to_date, date_type=date_type,
                key=key or "", field=field or "", key1=key1 or "", field1=field1 or "",
                where=where, filters=filters, limit=size,
            )
        return _grid_response(rows, headers, page,
                              "一行 = 一个核价产品行；同一表单编号可能多行，用 记录ID 定位单行；"
                              "详情/子数据用 erp_price_detail / erp_price_node",
                              columns=columns)

    # ---------- 生产施工单 ----------
    @mcp.tool(
        name="erp_production_list",
        description=(
            "查询生产施工单【列表】（ShengChan/SC_SCSGD/Index.aspx，QXID=740）。"
            "一行=一张施工单（生产单号 SGD…）。服务端只支持日期区间+关键字，"
            "其余用 filters（服务端下拉）或 where（本地过滤）。"
            f"默认 {DEFAULT_RECORDS} 行，上限 {MAX_RECORDS}。"
        ),
    )
    @_tool_errors
    def erp_production_list(
        from_date: Annotated[str | None, Field(description="起 YYYY-MM-DD")] = None,
        to_date: Annotated[str | None, Field(description="止 YYYY-MM-DD")] = None,
        date_type: Annotated[str, Field(description="制单日期 / 交货日期 / 审批日期")] = "制单日期",
        where: Annotated[
            str | None,
            Field(description="本地过滤，逗号分隔：生产单号~SGD2611,工单状态=已审批"),
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(description="服务端下拉过滤，值可写中文：工单状态/审批情况/报废状态/"
                              "工单类型/文件状态/推送状态/客户属性/模板…"),
        ] = None,
        key: Annotated[str, Field(description="服务端关键字（配合 field）")] = "",
        field: Annotated[str, Field(description="关键字字段：生产单号/订单编号/产品名称/客户名称…")] = "",
        key1: Annotated[str, Field(description="第二个服务端关键字")] = "",
        field1: Annotated[str, Field(description="第二个关键字字段")] = "",
        columns: Annotated[str | None, Field(description="只返回这些列，逗号分隔")] = None,
        limit: Annotated[int, Field(description=f"1~{MAX_RECORDS}，超出截断")] = DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        session = _session(state)
        size = _clamp(limit)
        with state.provider.slot():
            rows, headers, page = ProductionListTool(session.auth).collect(
                from_date=from_date, to_date=to_date, date_type=date_type,
                key=key or "", field=field or "", key1=key1 or "", field1=field1 or "",
                where=where, filters=filters, limit=size,
            )
        return _grid_response(rows, headers, page,
                              "一行 = 一张生产施工单；详情用 erp_production_detail",
                              columns=columns)

    @mcp.tool(
        name="erp_production_detail",
        description=(
            "生产施工单【详情】（SC_SCSGD/Add.aspx，Option=3 只读）："
            "表头字段（表单编号/产品名称/数量/交货日期…）+ 内联网格（部件表 17 列、"
            "送货表 21 列）+ 备注文本 + 这一单能点开的子页清单（nodes）。"
            "record_id 与 number 至少给一个。"
        ),
    )
    @_tool_errors
    def erp_production_detail(
        record_id: Annotated[str | None,
                              Field(description="记录 ID（erp_production_list 的 记录ID）")] = None,
        number: Annotated[str | None,
                          Field(description="生产单号，如 SGD2611180011")] = None,
        include_texts: Annotated[bool, Field(description="是否返回备注类文本域")] = True,
    ) -> dict[str, Any]:
        session = _session(state)
        tool = ProductionDetailTool(session.auth)
        with state.provider.slot():
            rows = tool.resolve_records(record_id=record_id or "", number=number or "")
            return tool.fetch_one(rows[0]["记录ID"], include_texts=include_texts)

    @mcp.tool(
        name="erp_production_node",
        description=(
            "生产施工单里「点开才有的子数据」（每个节点一次 POST+GET，1-2 秒）。"
            "node=list 列出全部节点（会标出这一单哪些能点开）；"
            "可用：cp=产品资料、cp_process=产品工序、cp_materials=产品辅料、"
            "part=部件资料、part_materials=部件辅料、part_process=部件工序、"
            "part_machines=部件机台、product_check=送货/审核资料、part_pick=部件明细、"
            "product_bg=产品资料变更、feedback=生产需求反馈、sgkb=手工开版、"
            "template_product=产品模板、by_template=按模板新建、binding=装订D10、"
            "package_content=包装内容D9、stock=生产库存明细、other_fees=其他费用、"
            "freight=费项明细。"
        ),
    )
    @_tool_errors
    def erp_production_node(
        record_id: Annotated[str, Field(description="记录 ID（列表的 记录ID）")],
        node: Annotated[str, Field(description="节点名；list 查看全部")],
    ) -> dict[str, Any]:
        session = _session(state)
        tool = ProductionNodeTool(session.auth)
        with state.provider.slot():
            if node.strip().lower() in ("list", "ls", "nodes"):
                return {"nodes": tool.catalog(record_id)}
            return tool.fetch(record_id, tool.resolve_node(node))

    # ---------- 采购订单 ----------
    @mcp.tool(
        name="erp_purchase_list",
        description=(
            "查询采购订单【列表】（CaiGou/CG_CGDD/Index.aspx，QXID=266）。"
            "⚠ 一行=一条采购订单**材料明细**：同一表单编号会有多行（一单多料），"
            "记录ID 是订单主记录，同一 ID 可能重复出现。"
            f"默认 {DEFAULT_RECORDS} 行，上限 {MAX_RECORDS}。"
        ),
    )
    @_tool_errors
    def erp_purchase_list(
        from_date: Annotated[str | None, Field(description="制单起 YYYY-MM-DD")] = None,
        to_date: Annotated[str | None, Field(description="制单止 YYYY-MM-DD")] = None,
        where: Annotated[
            str | None,
            Field(description="本地过滤，逗号分隔：表单编号=CGDD261118031,核实状态=…"),
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(description="服务端下拉过滤，值可写中文：审批情况/表单状态/数据粒度/"
                              "材料类别/供货方式/入库情况/是否不入库…"),
        ] = None,
        key: Annotated[str, Field(description="服务端关键字（配合 field）")] = "",
        field: Annotated[str, Field(description="关键字字段：表单编号/订单编号/材料名称/供应商名称…")] = "",
        key1: Annotated[str, Field(description="第二个服务端关键字")] = "",
        field1: Annotated[str, Field(description="第二个关键字字段")] = "",
        columns: Annotated[str | None, Field(description="只返回这些列，逗号分隔")] = None,
        limit: Annotated[int, Field(description=f"1~{MAX_RECORDS}，超出截断")] = DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        session = _session(state)
        size = _clamp(limit)
        with state.provider.slot():
            rows, headers, page = PurchaseListTool(session.auth).collect(
                from_date=from_date, to_date=to_date,
                key=key or "", field=field or "", key1=key1 or "", field1=field1 or "",
                where=where, filters=filters, limit=size,
            )
        return _grid_response(rows, headers, page,
                              "一行 = 一条采购材料明细；同一 表单编号 可能多行",
                              columns=columns)

    @mcp.tool(
        name="erp_purchase_detail",
        description=(
            "采购订单【详情】（CG_CGDD/Add.aspx，Option=3 只读）：表头字段"
            "（表单编号/供应商/联系人/付款方式/送货地址…）+ 材料明细内联网格（gridView2，33 列）。"
        ),
    )
    @_tool_errors
    def erp_purchase_detail(
        record_id: Annotated[str | None,
                              Field(description="记录 ID（erp_purchase_list 的 记录ID）")] = None,
        number: Annotated[str | None, Field(description="表单编号，如 CGDD261118031")] = None,
        include_texts: Annotated[bool, Field(description="是否返回备注类文本域")] = True,
    ) -> dict[str, Any]:
        session = _session(state)
        tool = PurchaseDetailTool(session.auth)
        with state.provider.slot():
            rows = tool.resolve_records(record_id=record_id or "", number=number or "")
            return tool.fetch_one(rows[0]["记录ID"], include_texts=include_texts)

    @mcp.tool(
        name="erp_purchase_node",
        description=(
            "采购订单的子页面（node=list 列出全部）。采购订单详情**没有**弹窗数据子页"
            "——材料明细已在 erp_purchase_detail 的 grids 里；这里能打开的是"
            "supplier=供应商档案选择页等「选择/编辑」弹窗。"
        ),
    )
    @_tool_errors
    def erp_purchase_node(
        record_id: Annotated[str, Field(description="记录 ID")],
        node: Annotated[str, Field(description="节点名；list 查看全部")],
    ) -> dict[str, Any]:
        session = _session(state)
        tool = PurchaseNodeTool(session.auth)
        with state.provider.slot():
            if node.strip().lower() in ("list", "ls", "nodes"):
                return {"nodes": tool.catalog(record_id)}
            return tool.fetch(record_id, tool.resolve_node(node))

    # ---------- 客户订单 ----------
    @mcp.tool(
        name="erp_sales_order_list",
        description=(
            "查询客户订单【列表】（DingDan/DD_KHDD/Index.aspx，QXID=687）。"
            "⚠ 一行=一个订单产品行（表单编号 KHDD 开头），同一单号可能多行，用 记录ID 定位单行。"
            f"默认 {DEFAULT_RECORDS} 行，上限 {MAX_RECORDS}。"
        ),
    )
    @_tool_errors
    def erp_sales_order_list(
        from_date: Annotated[str | None, Field(description="起 YYYY-MM-DD")] = None,
        to_date: Annotated[str | None, Field(description="止 YYYY-MM-DD")] = None,
        date_type: Annotated[
            str,
            Field(description="日期字段：制单（默认）/订单/离厂/交货"),
        ] = "制单日期",
        where: Annotated[
            str | None,
            Field(description="本地过滤：客户名称~CAR,订单状态=编辑中"),
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(description="服务端下拉（中文键）：订单状态/审批情况/客户属性/业务员…"),
        ] = None,
        key: Annotated[str, Field(description="服务端关键字（配合 field）")] = "",
        field: Annotated[str, Field(description="关键字字段：表单编号/客户名称…")] = "",
        key1: Annotated[str, Field(description="第二个服务端关键字")] = "",
        field1: Annotated[str, Field(description="第二个关键字字段")] = "",
        columns: Annotated[str | None, Field(description="只返回这些列，逗号分隔")] = None,
        limit: Annotated[int, Field(description=f"1~{MAX_RECORDS}，超出截断")] = DEFAULT_RECORDS,
    ) -> dict[str, Any]:
        session = _session(state)
        size = _clamp(limit)
        with state.provider.slot():
            rows, headers, page = SalesOrderListTool(session.auth).collect(
                from_date=from_date, to_date=to_date, date_type=date_type or "制单日期",
                key=key or "", field=field or "", key1=key1 or "", field1=field1 or "",
                where=where, filters=filters, limit=size,
            )
        return _grid_response(rows, headers, page,
                              "一行 = 一个订单产品行；同一 表单编号 可能多行",
                              columns=columns)

    @mcp.tool(
        name="erp_sales_order_detail",
        description=(
            "客户订单【详情】（DingDan/DD_KHDD/Add.aspx，只读）：表头与产品字段"
            "（订单状态/客户名称/客户单号/产品名称/数量/价格…）+ 页内网格"
            "（部件/选配/送货等）+ 备注类文本域 + 可点开的子页清单（nodes）。"
        ),
    )
    @_tool_errors
    def erp_sales_order_detail(
        record_id: Annotated[str | None, Field(description="记录 ID")] = None,
        number: Annotated[str | None, Field(description="表单编号")] = None,
        include_texts: Annotated[bool, Field(description="是否返回备注文本域")] = True,
    ) -> dict[str, Any]:
        session = _session(state)
        tool = SalesOrderDetailTool(session.auth)
        with state.provider.slot():
            rows = tool.resolve_records(record_id=record_id or "", number=number or "")
            return tool.fetch_one(rows[0]["记录ID"], include_texts=include_texts)

    @mcp.tool(
        name="erp_sales_order_node",
        description=(
            "客户订单里「点开才有的子数据」（每个节点一次 POST+GET，1-2 秒）。"
            "node=list 列出全部节点（会标出这一单哪些能点开）；可用："
            "cp=产品资料、cp_process=产品工序、cp_materials=产品辅料、"
            "part=部件资料、part_materials=部件辅料、part_process=部件工序、"
            "part_machines=部件机台、product_check=送货资料、quantities=数量档、"
            "fees=费项明细、other_fees=其他费项、feedback=需求反馈、sgkb=手工开版、"
            "product_bg=产品资料变更、stock=生产库存明细等（共 20 个）。"
            "⚠ fees / other_fees 在 ERP 侧是死链（弹窗指向 404），调用会明确报错。"
        ),
    )
    @_tool_errors
    def erp_sales_order_node(
        record_id: Annotated[str, Field(description="记录 ID")],
        node: Annotated[str, Field(description="节点名；list 查看全部")],
    ) -> dict[str, Any]:
        session = _session(state)
        tool = SalesOrderNodeTool(session.auth)
        with state.provider.slot():
            if node.strip().lower() in ("list", "ls", "nodes"):
                return {"nodes": tool.catalog(record_id)}
            return tool.fetch(record_id, tool.resolve_node(node))

    @mcp.tool(
        name="erp_sales_order_attachment",
        description=(
            "客户订单**附件**：列表页上「表单编号」左击调出的文件（title=左击即可以调出附件）。"
            "返回文件名 + 直链（ERP 静态目录 `/Admin/FuJian/<模块>/Picture/<guid>.<ext>`，"
            "可直接 HTTP GET 下载，**不需要** cookie）。"
            "单附件一单 1 个链接；多附件（附件列表页 `DD_KHDD_FJ.aspx`）会展开成多个。"
        ),
    )
    @_tool_errors
    def erp_sales_order_attachment(
        record_id: Annotated[str | None, Field(description="记录 ID")] = None,
        number: Annotated[str | None, Field(description="表单编号")] = None,
        from_date: Annotated[str | None, Field(description="起 YYYY-MM-DD")] = None,
        to_date: Annotated[str | None, Field(description="止 YYYY-MM-DD")] = None,
        where: Annotated[str | None, Field(description="本地过滤")] = None,
        filters: Annotated[dict[str, str] | None,
                           Field(description="服务端下拉（中文键）")] = None,
        limit: Annotated[int, Field(description="最多单据数")] = 50,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            rows = SalesOrderAttachmentTool(session.auth).attachments(
                record_id=record_id or "", number=number or "",
                from_date=from_date, to_date=to_date, where=where,
                filters=filters, limit=_clamp(limit),
            )
        files = []
        for row in rows:
            item = {"表单编号": row.get("表单编号"), "记录ID": row.get("记录ID"),
                    "名称": row.get("附件文件名") or None, "url": row.get("url")}
            proxy = _proxy_url(row.get("url") or "", name=(row.get("附件文件名") or ""))
            if proxy:
                item["proxy_url"] = proxy
            if row.get("note"):
                item["note"] = row["note"]
            files.append(item)
        return {"count": len(files), "files": files,
                "note": ("url 是 ERP 内网直链（同网段可 GET）；"
                         "proxy_url 走网关，跨网段也能下载")}

    # ---------- 菜单 → 接口 对照 ----------
    @mcp.tool(
        name="erp_menu",
        description=(
            "【只读】返回 ERP 左侧菜单与本工具接口的对照：哪些菜单页已接入、"
            "各页面上能做什么（增/删/改/查）、对应哪个 MCP 工具与文档。"
            "等价于 `erp menu --coverage`，明细见 docs/erp-menu.md。"
            "include_tree=true 时附带完整菜单树（较大）。"
        ),
    )
    @_tool_errors
    def erp_menu(
        include_tree: Annotated[
            bool, Field(description="是否附带完整菜单树（默认 false）")
        ] = False,
    ) -> dict[str, Any]:
        import json as _json

        from erp_tools import menu_catalog as mc
        from erp_tools.core.menu import duplicate_paths, menu_pages, tree_to_items

        tree_path = mc.menu_tree_path()
        result: dict[str, Any] = {
            "menu_doc": mc.MENU_DOC,
            "tree_file": str(tree_path),
            "pages": {
                path: [{"kind": c.kind, "action": c.action, "mcp": c.mcp,
                        "cli": c.cli, "doc": c.doc} for c in caps]
                for path, caps in mc.PAGE_CAPABILITIES.items()
            },
            "infra": [{"action": c.action, "mcp": c.mcp, "doc": c.doc}
                      for c in mc.INFRA_CAPABILITIES],
        }
        if not tree_path.is_file():
            result["tree_available"] = False
            result["note"] = "本机没有 docs/menu.tree.json；在仓库根跑 erp menu --capture 生成"
            return result

        meta = _json.loads(tree_path.read_text(encoding="utf-8"))
        tree = tree_to_items(meta.get("tree"))
        result.update({
            "tree_available": True,
            "captured_at": meta.get("captured_at"),
            "account": meta.get("account"),
            "login_user": meta.get("login_user"),
            "coverage": mc.coverage(tree),
            "duplicate_paths": duplicate_paths(tree),
        })
        if include_tree:
            result["tree"] = [item.to_dict() for item in tree]
        else:
            result["unconnected_paths"] = sorted(
                {item.page_path for _p, item in menu_pages(tree)}
                - mc.connected_pages()
            )
        return result

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
        month: Annotated[
            str,
            Field(description="考勤月份，YYYY-MM", examples=["2026-11"]),
        ],
        start_day: Annotated[
            str,
            Field(description="起始日，两位数字 01~30；超过 30 会被 ERP 自行收敛到 30",
                  examples=["01"]),
        ] = "01",
        end_day: Annotated[
            str,
            Field(description="结束日，两位数字 01~30；传 31 只会返回 1 号一行",
                  examples=["30"]),
        ] = "30",
        users: Annotated[
            list[str] | None,
            Field(description="工卡号或员工姓名列表；为空则用当前登录账号",
                  examples=[["2474"]]),
        ] = None,
        limit: Annotated[
            int,
            Field(description=f"返回行数，1~{MAX_RECORDS}；超出会被静默钳到上限并置 truncated"),
        ] = DEFAULT_RECORDS,
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
        record_id: Annotated[
            str | None,
            Field(
                description=(
                    "给了就打开该单据的明细编辑页，返回当前值、真实下拉选项与只读状态；"
                    "不给则返回静态注册表"
                )
            ),
        ] = None,
        cpid: Annotated[
            str | None,
            Field(description="明细行 ID（来自 erp_quote_details），与 record_id 搭配定位单行"),
        ] = None,
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
        field: Annotated[
            str | None,
            Field(
                description="只看某一组：sales / merchandiser / price_system（也接受业务员/跟单员/单价体系）",
                examples=["sales"],
            ),
        ] = None,
        refresh: Annotated[
            bool,
            Field(description="true 则跳过 5 分钟缓存，重新向 ERP 读取"),
        ] = False,
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
            "keyword 留空则返回前若干条。拿到的 customer_id 可直接交给 erp_quote_create。"
        ),
    )
    @_tool_errors
    def erp_quote_customer_search(
        keyword: Annotated[
            str,
            Field(description="客户名称/简称的片段，留空则返回前若干条", examples=["ADG"]),
        ] = "",
        limit: Annotated[
            int,
            Field(description="最多返回几个客户", examples=[20]),
        ] = 20,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).search_customers(keyword, limit=limit)

    # ---------- 新增报价需求：真正写入 ----------
    @mcp.tool(
        name="erp_quote_create",
        description=(
            "【会写入 ERP】新建一条客户报价需求（主表 + 1 条产品明细，含产品数量）。"
            "confirm=false（默认）只校验并返回预览（ok/errors/warnings/header），不会落库；"
            "传 confirm=true 才会真正保存，成功返回表单编号(BDBH)与记录ID。"
            "产品名称会先在【产品档案】里模糊检索，必须唯一匹配才继续。"
            "新产品（档案里搜不到）请同时传 products[0].product_category"
            "（如 \"27.01\" / \"27.01;普通BB书\" / \"普通BB书\"），明细行不要求产品档案存在。"
            "明细字段只接受 DOM 首屏可见的 51 个（见 erp_quote_product_form），"
            "折叠面板里的隐藏字段会被拒绝；键名拼错也会直接报错。"
            "写完后会回读校验（明细行 CPMC + 产品数量）；任一项不符就返回 status=partial，"
            "detail_error 说明卡在哪一步，不会假报成功。"
            "传了 attachments 时，单据落库成功后会自动把附件传到该单据上"
            "（附件页要记录 ID，所以只能建完再传）。"
            "已知限制：多语言数量明细不落库（只写汇总值 txtCPSL）。"
        ),
    )
    @_tool_errors
    def erp_quote_create(
        products: Annotated[
            list[QuoteProductInput],
            Field(
                min_length=1,
                description=(
                    "产品明细，当前只支持 1 条。每条要 name/spec/qty，"
                    "明细字段放在 fields 里（也可平铺在同级）"
                ),
            ),
        ],
        customer: Annotated[
            str | None,
            Field(description="客户名称片段或客户 ID；可先用 erp_quote_customer_search 查",
                  examples=["ADG"]),
        ] = None,
        sales: Annotated[
            str | None,
            Field(description="业务员姓名或 GUID；为空时取客户默认业务员",
                  examples=["陈小美"]),
        ] = None,
        merchandiser: Annotated[
            str | None,
            Field(description="跟单员姓名或 GUID；选项用 erp_quote_options 查"),
        ] = None,
        price_system: Annotated[
            str | None,
            Field(description="单价体系名称或 GUID；选项用 erp_quote_options 查"),
        ] = None,
        contact: Annotated[
            str | None,
            Field(description="联系人", examples=["陈先生"]),
        ] = None,
        phone: Annotated[
            str | None,
            Field(description="联系电话", examples=["0755-12345678"]),
        ] = None,
        mobile: Annotated[
            str | None,
            Field(description="手机；⚠ 实测该 ERP 不落库，提交后回读为空"),
        ] = None,
        fax: Annotated[
            str | None,
            Field(description="传真；⚠ 实测该 ERP 不落库，提交后回读为空"),
        ] = None,
        discount: Annotated[
            str | None,
            Field(description="折扣率，如 0.45；为空时取客户默认折扣率",
                  examples=["0.45"]),
        ] = None,
        remark: Annotated[
            str | None,
            Field(description="备注（表头 txtRemark）"),
        ] = None,
        product_memo: Annotated[
            str | None,
            Field(description="产品描述（表头 txtCPMX）"),
        ] = None,
        attachments: Annotated[
            list[dict[str, Any]] | None,
            Field(description=(
                "附件（建单后自动上传）：{filename, content_base64|content_text}，"
                "可选 name/desc/remark"
            )),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="false（默认）只校验并返回 needs_confirmation，不落库；true 才真正保存"),
        ] = False,
    ) -> dict[str, Any]:
        session = _session(state)
        items = (
            [item.model_dump(exclude_none=True) for item in products]
            if products else None
        )
        with state.provider.slot():
            return QuoteCreateService(session.auth).create(
                customer=customer, sales=sales, merchandiser=merchandiser,
                price_system=price_system, products=items, contact=contact,
                phone=phone, mobile=mobile, fax=fax, discount=discount,
                remark=remark, product_memo=product_memo, attachments=attachments,
                confirm=confirm,
            )


    # ---------- 通用附件（各模块复用同一套协议） ----------
    @mcp.tool(
        name="erp_attachment",
        description=(
            "**通用附件**（客户报价需求 / 客户报价·产品核价 / 生产施工单 / 采购订单 / 客户订单\n"
            "共用同一套 `*_FJ.aspx` 附件页）。一次调用按参数决定动作：\n"
            "· 都不传 = 列出附件；· 传 files = 上传；· 传 download = 取回 base64；\n"
            "· 传 delete（名称或 file_id）= 删除（需 confirm=true）。\n"
            "files 每项 {filename, content_base64} 或 {filename, content_text}，"
            "可选 name(附件名称)/desc/remark/expected_size/sha256；也支持 {path} 本机文件。\n"
            "上传后会下载回比字节数（size_ok / size_mismatch），ok=false 不要当成功。\n"
            "with_urls 每行给 url（ERP 内网直链）与 proxy_url（经网关转发，跨网段可下）。\n"
            "大文件用分片：每项带 {upload_key, chunk_index, total_chunks, chunk_base64}，\n"
            "未收齐返回 status=chunk_pending，继续传缺的片；收齐自动拼装上传。\n"
            "附件页路径自动发现；上传/删除后都会回读核对。单据必须先保存。"
        ),
    )
    @_tool_errors
    def erp_attachment(
        module: Annotated[
            str,
            Field(description="模块：quote_request / price / production / purchase / sales_order",
                  examples=["quote_request"]),
        ],
        record_id: Annotated[str, Field(description="单据记录 ID（GUID）")],
        files: Annotated[
            list[dict[str, Any]] | None,
            Field(description="要上传的附件（可带 chunk_index 分片）；不传=只列出"),
        ] = None,
        with_urls: Annotated[bool, Field(description="列表时一并取下载直链")] = False,
        download: Annotated[str, Field(description="按名称/文件ID 下载一个附件")] = "",
        delete: Annotated[str, Field(description="要删除的附件名称（或文件ID）")] = "",
        file_id: Annotated[str, Field(description="附件文件ID（比名称精确）")] = "",
        confirm: Annotated[bool, Field(description="删除时 false 只预览，true 才删")] = False,
    ) -> dict[str, Any]:
        from ...tools.attachments import AttachmentPageService

        session = _session(state)
        with state.provider.slot():
            svc = AttachmentPageService.for_module(session.auth, module)
            if files:
                result = svc.upload(record_id, files)
                result["page"] = f"/Admin/{svc.resolve_page_path(record_id)}"
                result["files"] = svc.list_files(record_id)["files"]
                return result
            if delete.strip() or file_id.strip():
                return svc.delete_file(record_id, name=delete.strip(),
                                       file_id=file_id.strip(), confirm=confirm)
            listing = svc.list_files(record_id, with_urls=with_urls)
            if download.strip():
                target = download.strip()
                picked = next((r for r in listing["files"]
                               if target in (r["名称"], r["文件ID"])), None)
                if picked is None:
                    raise ToolError(
                        "没找到附件 " + target + "；现有："
                        + "、".join(r["名称"] for r in listing["files"]))
                url = svc.file_url(record_id, picked["文件ID"])
                content, ctype, used = svc.fetch_file(url)
                import base64 as _b64

                from ...runtime import file_proxy

                return {"record_id": record_id, "名称": picked["名称"], "url": used,
                        "proxy_url": file_proxy.build_url(used, name=picked["名称"]),
                        "size": len(content), "content_type": ctype,
                        "content_base64": _b64.b64encode(content).decode()}
            listing["module"] = module
            listing["page"] = f"/Admin/{svc.resolve_page_path(record_id)}"
            return listing

    @mcp.tool(
        name="erp_attachment_modules",
        description=(
            "【只读】列出「有附件功能」的模块与它们实测的附件页、入口形态"
            "（详情页 `[附件]` 锚点 / 列表页单号单元格）、删除方式。"
            "用于确认 erp_attachment 的 module 取值。"
        ),
    )
    def erp_attachment_modules() -> dict[str, Any]:
        from ...tools.attachments import MODULES

        return {
            "modules": [m.as_dict() for m in MODULES.values()],
            "count": len(MODULES),
            "note": "上传页全部是同一套模板；差别只在入口与删除按钮",
        }

    # ---------- 报价需求：明细行 / 修改 ----------
    @mcp.tool(
        name="erp_quote_details",
        description=(
            "【只读】列出某条报价需求单的产品明细行，返回每行的 cpid（修改/删除该行时要用）。"
            "record_id 可先用 erp_quote_list 查到。"
        ),
    )
    @_tool_errors
    def erp_quote_details(record_id: Annotated[
        str,
        Field(description="报价需求记录 ID（GUID），来自 erp_quote_list 的 记录ID 列"),
    ]) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).list_details(record_id)

    @mcp.tool(
        name="erp_quote_update",
        description=(
            "【会写入 ERP】修改报价需求单的**表头**（备注/联系人/电话/手机/传真/折扣率/"
            "产品描述(表头 txtCPMX)/业务员/跟单员）。内部会先发 btnModify 解锁 [保存]，再发 btnSave。"
            "⚠ 明细行的产品描述（CPMS）不在这里，请用 erp_quote_update_product(product_desc=…)。"
            "confirm=false（默认）只返回将要修改的字段，不写库。"
        ),
    )
    @_tool_errors
    def erp_quote_update(
        record_id: Annotated[
            str,
            Field(description="报价需求记录 ID（GUID）"),
        ],
        remark: Annotated[str | None, Field(description="备注；不传则不修改")] = None,
        contact: Annotated[str | None, Field(description="联系人；不传则不修改")] = None,
        phone: Annotated[str | None, Field(description="联系电话；不传则不修改")] = None,
        mobile: Annotated[
            str | None,
            Field(description="手机；⚠ 实测该 ERP 不落库"),
        ] = None,
        fax: Annotated[
            str | None,
            Field(description="传真；⚠ 实测该 ERP 不落库"),
        ] = None,
        discount: Annotated[
            str | None,
            Field(description="折扣率，如 0.45", examples=["0.45"]),
        ] = None,
        product_memo: Annotated[
            str | None,
            Field(description="产品描述（表头 txtCPMX）"),
        ] = None,
        sales: Annotated[
            str | None,
            Field(description="业务员姓名或 GUID"),
        ] = None,
        merchandiser: Annotated[
            str | None,
            Field(description="跟单员姓名或 GUID"),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="false（默认）只返回将要修改的字段；true 才写库"),
        ] = False,
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
            "【会写入 ERP】修改某条**产品明细**（cpid 来自 erp_quote_details）。"
            "可改：产品名称 name / 规格 spec / 数量 qty / **产品描述 product_desc（txtCPMS）**，"
            "以及 fields 里的任意明细字段（与 erp_quote_create 同一套词汇，如 "
            "parts_desc 配件描述、modify_log 修改记录、inspection 检测项目、packing 包装方式）。"
            "⚠ 表头字段（备注/联系人/折扣率…）用 erp_quote_update；"
            "明细的 CPMS 只能用本工具改。"
            "confirm=false（默认）只返回待修改字段；保存后逐个回读改动过的控件复核，"
            "不一致返回 status=failed 并在 mismatched 里列出字段。"
        ),
    )
    @_tool_errors
    def erp_quote_update_product(
        record_id: Annotated[
            str,
            Field(description="报价需求记录 ID（GUID）"),
        ],
        cpid: Annotated[
            str,
            Field(description="明细行 ID，来自 erp_quote_details 返回的 cpid"),
        ],
        name: Annotated[
            str | None,
            Field(description="新产品名称（会写入明细 txtCPMC）；不传则不修改"),
        ] = None,
        spec: Annotated[
            str | None,
            Field(description="规格，形如 210*148；不传则不修改", examples=["210*148"]),
        ] = None,
        qty: Annotated[
            float | None,
            Field(description="产品数量，必须 > 0", examples=[3000]),
        ] = None,
        product_desc: Annotated[
            str | None,
            Field(
                description=(
                    "明细的**产品描述**（ERP 字段 txtCPMS，多行长文本，可含换行）；"
                    "不传则不修改。传空串表示清空。"
                ),
                examples=["皮壳：157g哑粉裱1200g灰板，270*210mm，4C+哑胶\n内文：128g双铜，4C+4C"],
            ),
        ] = None,
        fields: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "其他明细字段（别名或控件名）：如 parts_desc=配件描述、modify_log=修改记录、"
                    "inspection=检测项目、packing=包装方式、delivery=送货方式、pallet=台板类型。"
                    "数量请用 qty 传；只读/自动计算的字段会被拒绝。"
                ),
                examples=[{"inspection": "EN71 part1/2/3"}],
            ),
        ] = None,
        confirm: Annotated[
            bool,
            Field(description="false（默认）只返回待修改字段；true 才写库"),
        ] = False,
    ) -> dict[str, Any]:
        session = _session(state)
        with state.provider.slot():
            return QuoteCreateService(session.auth).update_product(
                record_id, cpid, name=name, spec=spec, qty=qty,
                product_desc=product_desc, fields=fields, confirm=confirm,
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
    def erp_quote_delete(
        record_id: Annotated[
            str,
            Field(description="报价需求记录 ID（GUID），删除不可恢复"),
        ],
        confirm: Annotated[
            bool,
            Field(description="false（默认）只检查单据是否存在并返回单号；true 才真正删除"),
        ] = False,
    ) -> dict[str, Any]:
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
            "新增报价需求的写入链：erp_quote_new_form / erp_quote_options 看清可填项"
            "→ erp_quote_create(confirm=false) 预览 → erp_quote_create(confirm=true) 落库"
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
