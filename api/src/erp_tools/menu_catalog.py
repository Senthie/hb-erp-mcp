"""ERP 菜单 → 本工具能力 的对照表（**唯一数据源**）。

分工：

* 菜单树（模块/子模块/菜单页/QXID）**自动抓**，落在 `docs/menu.tree.json`；
* 本文件只声明「某个页面上已经实现/探明了哪些操作」，以及每个操作对应的
  **接口名 + 文档**。追加新能力 = 在 `PAGE_CAPABILITIES` 里加一行，然后
  `erp menu --write` 重新生成 `docs/erp-menu.md`。

约定：

* key 是 `core.auth.normalize_page_path()` 归一后的页面路径（小写、无前导 `/Admin/`、无 query）；
* 一个 `Capability` = 一个**可调用接口**（MCP 工具名），CLI 子命令作为补充写在同一个条目里，
  所以文档天然 1:1 对应 `docs/api/<mcp 工具名>.md`；
* `kind` 用 增 / 删 / 改 / 查 / 读 / 其他，方便按动作筛选。
"""

from __future__ import annotations

from dataclasses import dataclass

from .core.menu import MenuItem, duplicate_paths, menu_pages, walk

#: docs/ 下的接口文档目录
API_DIR = "api"
#: 生成的对照表（相对仓库根）
MENU_DOC = "docs/erp-menu.md"
#: 菜单树快照（相对仓库根）
MENU_TREE_JSON = "docs/menu.tree.json"

NOT_IMPLEMENTED = "⬜ 未接入"


@dataclass(frozen=True)
class Capability:
    """一个已实现的接口。"""

    kind: str                   # 增 / 删 / 改 / 查 / 读 / 其他
    action: str                 # 一句话说明
    mcp: str = ""               # MCP 工具名（= 文档名）
    cli: str = ""               # CLI 子命令（不带 `erp ` 前缀）
    doc: str = ""               # 相对 docs/ 的文档路径，如 api/erp_quote_create.md
    note: str = ""              # 额外提示，渲染成括号里的补充

    @property
    def title(self) -> str:
        return self.mcp or self.cli


# ══════════════════ 菜单页 → 能力 ══════════════════
#: 建议按菜单顺序书写，方便和 ERP 左侧菜单对照
PAGE_CAPABILITIES: dict[str, tuple[Capability, ...]] = {
    # ── 业务报价管理 / 业务需求 / 客户报价需求 ──
    "baojia/bj_khbjxq/add.aspx": (
        Capability("增", "新建报价需求（主表 + 产品明细 + 产品数量）",
                   mcp="erp_quote_create", doc="api/erp_quote_create.md"),
        Capability("查", "新增页字段结构（能填什么、业务员怎么传）",
                   mcp="erp_quote_new_form", doc="api/erp_quote_new_form.md"),
        Capability("查", "产品明细子页字段清单 + 上传用 JSON Schema",
                   mcp="erp_quote_product_form", doc="api/erp_quote_product_form.md"),
        Capability("查", "业务员 / 跟单员 / 单价体系 下拉选项",
                   mcp="erp_quote_options", doc="api/erp_quote_options.md"),
        Capability("查", "客户资料检索（拿 customer_id）",
                   mcp="erp_quote_customer_search", doc="api/erp_quote_customer_search.md"),
        Capability("改", "改表头（备注 / 联系人 / 折扣率 / 业务员 / 跟单员…）",
                   mcp="erp_quote_update", doc="api/erp_quote_update.md"),
    ),
    "baojia/bj_khbjxq/index.aspx": (
        Capability("查", "列表筛选 + 导出 CSV", cli="query",
                   mcp="erp_quote_list", doc="api/erp_quote_list.md"),
        Capability("查", "单张单据完整详情（订单需求 JSON）", cli="quote",
                   mcp="erp_quote_detail", doc="api/erp_quote_detail.md"),
        Capability("查", "列出产品明细行（拿 cpid，供改/删单行）",
                   mcp="erp_quote_details", doc="api/erp_quote_details.md"),
        Capability("改", "改单条产品明细（名称 / 规格 / 数量）",
                   mcp="erp_quote_update_product", doc="api/erp_quote_update_product.md",
                   note="入口是 Add.aspx，流程从列表的记录ID 进"),
        Capability("删", "删除整张报价需求单",
                   mcp="erp_quote_delete", doc="api/erp_quote_delete.md"),
        Capability("读/写", "附件：列出 / 上传 stream / 下载 / 删除（五模块通用）",
                   mcp="erp_attachment", doc="api/erp_attachment.md",
                   note="CLI：erp attachment（旧名 erp quote-attachment）"),
    ),
    # ── 业务报价管理 / 客户报价 ──
    "baojia/bj_khbj/index.aspx": (
        Capability("查", "列表筛选 + 导出（一行 = 一个报价产品行）", cli="price",
                   mcp="erp_price_list", doc="api/erp_price_list.md"),
        Capability("查", "产品核价列表（同一页面 QXID=1281，一行 = 一个核价产品行）",
                   cli="costing-list", mcp="erp_costing_list",
                   doc="api/erp_costing_list.md",
                   note="菜单：业务报价管理 › 产品核价 › 列表-产品核价"),
    ),
    "baojia/bj_khbj/add.aspx": (
        Capability("读", "单个报价产品行完整详情", cli="price-detail",
                   mcp="erp_price_detail", doc="api/erp_price_detail.md"),
        Capability("读", "22 个子数据节点（数量档/部件/工序/辅料/费项/折叠面板…）",
                   cli="price-node", mcp="erp_price_node", doc="api/erp_price_node.md"),
    ),
    # ── 生产管理 / 生产施工单 ──
    "shengchan/sc_scsgd/index.aspx": (
        Capability("查", "列表筛选 + 导出（一行 = 一张施工单）", cli="production-list",
                   mcp="erp_production_list", doc="api/erp_production_list.md"),
        Capability("读", "行内点击事件（电子稿袋/文件跟进/跟进信息/做货指引/专色油墨/"
                         "临时材料清单/打印）", cli="production-row",
                   mcp="erp_production_row", doc="api/erp_production_row.md",
                   note="行上的图标/链接；info·部件备注·核价否 在 ERP 侧不可读"),
    ),
    "shengchan/sc_scsgd/add.aspx": (
        Capability("读", "施工单详情（字段 + 部件表 + 送货表 + 子页清单）",
                   cli="production-detail", mcp="erp_production_detail",
                   doc="api/erp_production_detail.md"),
        Capability("读", "20 个弹窗子页（产品/部件/工序/辅料/机台/送货/D10/开版…）",
                   cli="production-node", mcp="erp_production_node",
                   doc="api/erp_production_node.md"),
    ),
    # ── 采购管理 / 采购订单 ──
    # 采购订单只有「列表-采购订单」一个菜单（没有新增页），详情页 Add.aspx 由列表行双击进入，
    # 因此详情/子页能力也登记在这个页面上（菜单树里没有 add.aspx 这条路径）。
    "caigou/cg_cgdd/index.aspx": (
        Capability("查", "列表筛选 + 导出（一行 = 一条材料明细）", cli="purchase-list",
                   mcp="erp_purchase_list", doc="api/erp_purchase_list.md"),
        Capability("读", "采购订单详情（表头 + 材料明细网格）", cli="purchase-detail",
                   mcp="erp_purchase_detail", doc="api/erp_purchase_detail.md",
                   note="入口是列表的 记录ID → Add.aspx?id=…&Option=3"),
        Capability("读", "子页面（供应商档案选择等弹窗）", cli="purchase-node",
                   mcp="erp_purchase_node", doc="api/erp_purchase_node.md"),
    ),
    # ── 客户订单管理 / 客户订单 ──
    # 与采购订单相同：「列表-客户订单」是菜单项，详情页 Add.aspx 由列表记录ID 进入，
    # 菜单树里没有这条路径，因此详情/子页能力也登记在列表页上。
    "dingdan/dd_khdd/index.aspx": (
        Capability("查", "列表筛选 + 导出（一行 = 一个订单产品行，94 列）",
                   cli="sales-order-list", mcp="erp_sales_order_list",
                   doc="api/erp_sales_order_list.md"),
        Capability("读", "订单详情（字段 + 选配/部件/送货网格 + 文本域）",
                   cli="sales-order-detail", mcp="erp_sales_order_detail",
                   doc="api/erp_sales_order_detail.md",
                   note="入口是列表的 记录ID → DD_KHDD/Add.aspx"),
        Capability("读", "20 个弹窗子页（产品/部件/工序/辅料/机台/数量档/费项/反馈/开版…）",
                   cli="sales-order-node", mcp="erp_sales_order_node",
                   doc="api/erp_sales_order_node.md"),
        Capability("读", "附件（列表页「表单编号」左击调出的文件，直链可下载）",
                   cli="sales-order-attachment", mcp="erp_sales_order_attachment",
                   doc="api/erp_sales_order_attachment.md"),
    ),
    # ── 人力资源管理 / 考勤管理 ──
    "renshi/rs_kqda_m/add.aspx": (
        Capability("查", "月度考勤明细", cli="attendance",
                   mcp="erp_attendance", doc="api/erp_attendance.md"),
    ),
}

#: 不挂在菜单上的接口（身份 / 能力探测）——菜单里没有对应页面
INFRA_CAPABILITIES: tuple[Capability, ...] = (
    Capability("查", "当前 api-key 对应的 ERP 身份与会话状态",
               mcp="erp_whoami", doc="api/erp_whoami.md"),
    Capability("查", "当前账号能访问哪些页面（QXID 能否探测到）",
               mcp="erp_capabilities", doc="api/erp_capabilities.md"),
    Capability("查", "菜单 → 接口对照（哪些菜单页已接入）",
               mcp="erp_menu", doc="api/erp_menu.md",
               note="CLI：erp menu"),
    Capability("查", "四个模块的**数据页面清单**（列表 / 详情 / 每个弹窗子页）",
               mcp="erp_page_catalog", doc="api/erp_page_catalog.md",
               note="CLI：erp pages"),
    Capability("查", "有附件功能的模块与各自附件页 / 入口 / 删除方式",
               mcp="erp_attachment_modules", doc="api/erp_attachment_modules.md"),
)


# ══════════════════ 路径 ══════════════════
def find_repo_root(start=None):
    """从 `start`（默认当前目录）向上找**仓库根**。

    monorepo 布局：仓库根下有 `api/pyproject.toml`（后端）与 `web/`（前端），
    而 `api/` 自己也有 `pyproject.toml`。所以判定顺序是
    「先认 `api/pyproject.toml`（monorepo 根），再退化成老的『带 pyproject.toml 的目录』」，
    这样无论从仓库根还是 `api/` 里执行，`docs/` 都指向仓库根。
    """
    import sys
    from pathlib import Path

    here = Path(start).resolve() if start else Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / "api" / "pyproject.toml").is_file():
            return candidate

    # 打包 / 旧布局：目录里既有 pyproject.toml 又有 docs/
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "docs").is_dir():
            return candidate

    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate

    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else here


def menu_tree_path(start=None):
    """`docs/menu.tree.json` 的位置；找不到仓库根时按当前目录算。"""
    return find_repo_root(start) / MENU_TREE_JSON


# ══════════════════ 统计 ══════════════════
def connected_pages() -> set[str]:
    return {path for path, caps in PAGE_CAPABILITIES.items() if caps}


def coverage(tree: list[MenuItem]) -> dict:
    """整体覆盖统计。

    同时给「菜单页」和「唯一路径」两套数：同一路径可能挂在多个菜单下，
    所以 34 个菜单项只对应 32 个页面。
    """
    pages = menu_pages(tree)
    unique = {item.page_path for _p, item in pages}
    connected = connected_pages()
    hit = [item for _p, item in pages if item.page_path in connected]
    hit_paths = {item.page_path for item in hit}
    return {
        "modules": len(tree),
        "pages": len(pages),
        "unique_paths": len(unique),
        "connected_pages": len(hit_paths),
        "capabilities": sum(len(c) for c in PAGE_CAPABILITIES.values())
                        + len(INFRA_CAPABILITIES),
        "unconnected": len(pages) - len(hit),
        "unconnected_paths": len(unique - hit_paths),
    }


def module_stats(tree: list[MenuItem]) -> list[dict]:
    """每个顶级模块的「菜单页 / 已接入」统计（给文档顶部的表用）。"""
    connected = connected_pages()
    rows: list[dict] = []
    for module in tree:
        pages = [item for _p, item in menu_pages([module])]
        hit = {item.page_path for item in pages if item.page_path in connected}
        rows.append({
            "module": module.label,
            "pages": len(pages),
            "connected": len(hit),
            "missing": len(pages) - len(hit) if pages else 0,
        })
    return rows


# ══════════════════ 渲染 ══════════════════
def _cap_label(cap: Capability) -> str:
    if cap.mcp and cap.cli:
        api = f"`erp {cap.cli}` · `{cap.mcp}`"
    elif cap.mcp:
        api = f"`{cap.mcp}`"
    else:
        api = f"`erp {cap.cli}`"
    doc = f"[{cap.doc}]({cap.doc})" if cap.doc else ""
    note = f"（{cap.note}）" if cap.note else ""
    tail = " · ".join(x for x in (api, doc) if x)
    return f"- ✅ {cap.kind} · {cap.action}{note} → {tail}"


def _render_node(item: MenuItem, depth: int, dup: dict[str, list[str]]) -> list[str]:
    pad = "  " * depth
    if item.is_page:
        qxid = f" · QXID {item.qxid}" if item.qxid else ""
        warn = ""
        qxids = dup.get(item.page_path)
        if qxids:
            others = "/".join(q for q in qxids if q and q != item.qxid)
            if others:
                warn = f" · ⚠ 同路径另有 QXID {others}"
        lines = [f"{pad}- `{item.label}` · `{item.url.split('?')[0]}`{qxid}{warn}"]
        caps = PAGE_CAPABILITIES.get(item.page_path, ())
        if caps:
            cap_pad = "  " * (depth + 1)
            lines.extend(cap_pad + _cap_label(cap) for cap in caps)
        else:
            lines.append(f"{'  ' * (depth + 1)}- {NOT_IMPLEMENTED}")
        return lines

    # 分组节点（无 url 的纯目录）
    suffix = "" if item.children else " · 无页面"
    head = f"{pad}- **{item.label}**{suffix}" if depth == 0 else f"{pad}- {item.label}{suffix}"
    lines = [head]
    for child in item.children:
        lines.extend(_render_node(child, depth + 1, dup))
    return lines


def render_markdown(tree: list[MenuItem], meta: dict) -> str:
    """生成 `docs/erp-menu.md`。`meta` 来自 `docs/menu.tree.json` 的头部。"""
    stats = coverage(tree)
    dup = duplicate_paths(tree)
    out: list[str] = []
    add = out.append

    add("# ERP 菜单 → 接口 对照表")
    add("")
    add("> **自动生成，请勿手改**：`erp menu --write`")
    add(f"> 数据源：`{MENU_TREE_JSON}`（菜单树）+ "
        "`api/src/erp_tools/menu_catalog.py`（能力清单）")
    add(">")
    add(f"> | 项 | 值 |")
    add("> | --- | --- |")
    add(f"> | 抓取账号 | {meta.get('account') or '未记录'}（ERP UserID {meta.get('login_user') or '?'}）|")
    add(f"> | 抓取时间 | {meta.get('captured_at') or '未记录'} |")
    add(f"> | 菜单来源 | `{meta.get('source') or 'GetMenuTree'}` |")
    add(f"> | 规模 | {stats['modules']} 个模块 · {stats['pages']} 个菜单页"
        f"（{stats['unique_paths']} 个唯一路径） |")
    add(f"> | 已接入 | {stats['connected_pages']} 个页面 · "
        f"{stats['capabilities']} 个接口 |")
    add("")

    add("## 覆盖率")
    add("")
    add("| 模块 | 菜单页 | 已接入 | 未接入 |")
    add("| --- | ---: | ---: | ---: |")
    for row in module_stats(tree):
        add(f"| {row['module']} | {row['pages']} | {row['connected']} | {row['missing']} |")
    add(f"| **合计** | **{stats['pages']}** | **{stats['connected_pages']}** | "
        f"**{stats['unconnected']}** |")
    add("")
    add("标记说明：✅ 已接入（后面跟接口名与文档）、⬜ 未接入、⚠ 同一页面挂在多个菜单下。")
    add("")

    add("## 菜单树与接口")
    add("")
    for module in tree:
        out.extend(_render_node(module, 0, dup))
    add("")

    add("## 基础设施（不挂在菜单上）")
    add("")
    for cap in INFRA_CAPABILITIES:
        add(_cap_label(cap))
    add("- ✅ 查 · 登录 / 缓 cookie / 探测 QXID → `erp login` `erp doctor` `erp qxid` "
        "· [docs/cli.md](cli.md)")
    add("- ✅ 其他 · 起 MCP 网关 / 换 api-key → `erp serve` `erp key` "
        "· [docs/mcp.md](mcp.md)")
    add("")
    add("---")
    add("")
    add("追加能力的步骤：在 `api/src/erp_tools/menu_catalog.py` 的 `PAGE_CAPABILITIES` 里"
        "给对应页面加一行（动作 + 接口名 + `docs/api/<接口名>.md`），然后跑 "
        "`erp menu --write`。忘了重新生成时 `tests/test_menu_docs.py` 会失败。")
    add("")
    return "\n".join(out)


__all__ = [
    "API_DIR",
    "Capability",
    "INFRA_CAPABILITIES",
    "MENU_DOC",
    "MENU_TREE_JSON",
    "NOT_IMPLEMENTED",
    "PAGE_CAPABILITIES",
    "connected_pages",
    "coverage",
    "find_repo_root",
    "menu_tree_path",
    "module_stats",
    "render_markdown",
]

