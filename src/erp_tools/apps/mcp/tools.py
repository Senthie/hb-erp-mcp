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
            "missing_pages 为空说明这些页面都可访问；字段级缺失记录在每条数据的 _diagnostics 里。"
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
                for node in tree.values():
                    for group in (node or {}).get("groups", []):
                        for rows in group.get("fees", {}).values():
                            for row in rows:
                                row.pop("公式", None)
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
            "【会写入 ERP】修改报价需求单的表头（备注/联系人/电话/手机/传真/折扣率/"
            "产品描述/业务员/跟单员）。内部会先发 btnModify 解锁 [保存]，再发 btnSave。"
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
            "【会写入 ERP】修改某条产品明细（cpid 来自 erp_quote_details）。"
            "可改产品名称/规格/数量。confirm=false（默认）只返回待修改字段。"
            "保存后会回读名称与产品数量复核，不一致会返回 status=failed。"
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
        confirm: Annotated[
            bool,
            Field(description="false（默认）只返回待修改字段；true 才写库"),
        ] = False,
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
