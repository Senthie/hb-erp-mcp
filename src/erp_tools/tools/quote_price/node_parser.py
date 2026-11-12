"""客户报价详情页里「点开才有的子数据」解析。

这些子页都是老 WebForms 弹窗，共同特点：
  * 数据在**嵌套很深的 table** 里，`tr.find_parent("table") is table` 这种“只要本表行”
    的过滤在这里会把真正的数据行滤掉 —— 所以本项目统一按「行宽」抓：
    先看目标行应该有几个 `td`，再在全文档里按宽度取行；
  * 表头可能是 `<th>` 也可能是 `<td>`，`_cells()` 两种都认；
  * 单元格值可能是 `select` 的选中项、无 name 的只读 input，或者纯文本。

子页清单（实测 2026-11-12，账号 2069，QXID=860）：

| node | 子页 | 内容 |
| --- | --- | --- |
| quantities | `Add_CPSL.aspx` | 数量档：序号/数量/单价/金额/折扣率/折后单价/折后金额/建议利润/客户目标单价/成交否/单款数量 |
| part | `Add_BJZL.aspx` | 单个部件：部件名称/P数/尺寸/比例关系/数量/正反面颜色与专色数/书脊方向/拼版方式/排版留位 |
| part_materials | `Add_BJFL.aspx` | 该部件已选辅料：部件名称/材料编码/材料名称/品牌/单价/材料长宽/指定数量/系数 |
| fees | `Brow_FXMX.aspx` | 每个数量档的费项明细：纸张/辅料/印前/印刷/版费/印后/其他 |
| cp | `Add_CPZL.aspx` | 产品资料：整张产品表单（产品类别/装订方式/尺寸/金额拆分/纸箱/报关…） |
| cp_process | `Add_CPGXZL.aspx` | 产品级工序（29 列：工序名称/工价级别/工序单价/起版费/版材…） |
| cp_materials | `Add_CPFL.aspx` | 产品级辅料（8 列：材料编码/名称/单价/材料长宽/指定数量/系数） |
| part_process | `Add_BJGXZL.aspx` | 部件工序（27 列，按部件） |
| part_machines | `Add_BJJTZL.aspx` | 部件机台（30 列：机台名称/正反面颜色/版材/加放…）+ 机台字段 |
| other_fees | `Add_QTFX.aspx` | 其他费用项目（排序号/费用项目/选配/计价公式/税点/金额） |
| feedback | `Add_KHBJXQFK.aspx` | 客户报价需求反馈正文（textarea） |
| sgkb | `Add_SGKB.aspx` | 手工开版资料附件（资料名称/类别） |
| split_price | `KHBJ_SetJEXSMS.aspx` | 拆分价格设置（两棵 TreeView：待分配 / 已分配） |
| product_check | `Add_CPSHZL.aspx` | 送货/审核资料（28 列送货明细 + 客户/送货字段） |

另外 8 个「折叠面板」（`__doPostBack('Lbtn_Xxx')`，返回整页）见 `PANEL_TARGETS`。
"""

from __future__ import annotations

import re
from typing import Iterable

from ...core.aspnet import soup_of
from .detail_parser import blank_if_placeholder, cell_value, norm, row_pairs

#: 材料编码形如 03.03.05.03.0347
CODE_RE = re.compile(r"^\d{2}(?:\.\d{2}){3,4}$")
#: 费项明细里的类别标题
FEE_CATEGORIES = ("纸张费用", "辅料费用", "印前费用", "印刷费用", "版费",
                  "印后费用", "其他费用")


def _norm(text: str) -> str:
    """归一空白；这里直接复用 detail_parser.norm（会清掉 BOM / 全角空格 / 零宽字符，
    ERP 表头里确实夹着这些，不清会在列名里留下 `\ufeff`）。"""
    return norm(text)


def _all_rows(soup) -> list:
    return soup.find_all("tr")


def _cells(tr) -> list:
    cells = tr.find_all("td", recursive=False)
    if not cells:
        cells = tr.find_all("th", recursive=False)
    return cells


def _texts(cells: Iterable) -> list[str]:
    return [_norm(c.get_text(" ", strip=True)) for c in cells]


def _unique(names: list[str]) -> list[str]:
    """给重名列加后缀（`单价`、`单价#2`），避免 dict(zip(...)) 丢列。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for raw in names:
        name = raw or f"col{len(out) + 1}"
        if name in seen:
            seen[name] += 1
            out.append(f"{name}#{seen[name]}")
        else:
            seen[name] = 1
            out.append(name)
    return out


def grid_by_width(soup, width: int, must_have: tuple[str, ...] = (),
                  drop: tuple[str, ...] = ("选择",)) -> list[dict]:
    """按「行宽 + 表头关键字」在全文档里找一张网格，返回数据行字典列表。

    `must_have` 里的关键字必须全部出现在表头行里，避免把别的同宽表格当表头；
    `drop` 里的列名（默认「选择」复选框列）在结果里去掉。

    找到表头后会锁定在**那一张表**里取行：这些子页把「列表」和「左侧编辑表单」放在
    同一个文档里，两者行宽往往一样，不锁定就会把表单的标签行当数据。
    """
    header: list[str] | None = None
    table = None
    records: list[dict] = []
    for tr in _all_rows(soup):
        cells = _cells(tr)
        if len(cells) != width:
            continue
        texts = _texts(cells)
        if header is None:
            if must_have and not all(any(label in t for t in texts) for label in must_have):
                continue
            header = _unique(texts)
            table = tr.find_parent("table")
            continue
        if table is not None and tr.find_parent("table") is not table:
            continue        # 表外同宽行（编辑表单）不是数据
        if not any(texts):
            continue
        if must_have and all(any(label in t for t in texts) for label in must_have):
            continue        # 表头重复出现，跳过
        values = [blank_if_placeholder(cell_value(c)) for c in cells]
        if not any(values):
            continue
        records.append(dict(zip(header, values)))
    if drop:
        return [{k: v for k, v in r.items() if k not in drop} for r in records]
    return records


# ══════════════════ 表单页（标签格 → 值格） ══════════════════
#: 这些“标签”只是页面内部的 ID/隐藏字段，不是业务数据
FORM_NOISE = frozenset({
    "ID", "PID", "CPID", "BJID", "LYDHID", "XKHDD", "GXID", "GXQDPXH",
    "客户ID", "需求来源ID", "产品模板ID", "机台编码", "TableName", "DivName",
    "FieldName", "DataSource", "DataSet",
})


def forms_of(soup) -> dict[str, str]:
    """全文档「标签格 → 值格」汇总成 {标签: 值}，只保留有值的。

    同名标签出现多次时保留第一个有值的（页面里偶尔有重复的展示块）。
    """
    pairs: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        for key, value in row_pairs(tr).items():
            key = key.strip()
            if not key or key in FORM_NOISE:
                continue
            if key not in pairs or (not pairs[key] and value):
                pairs[key] = value
    return {k: v for k, v in pairs.items() if v}


def parse_quantities_node(html: str) -> list[dict]:
    """产品数量档 → [{序号, 数量, 单价, 金额, 折扣率, 折后单价, 折后金额, ...}]。"""
    soup = soup_of(html)
    return grid_by_width(soup, 11, must_have=("序号", "数量", "单价", "金额"))


PART_FIELD_LABELS = ("部件名称", "P数", "部件尺寸", "比例关系", "部件数量",
                     "正面颜色", "反面颜色", "正面专色数", "反面专色数",
                     "书脊方向", "拼版方式", "排版方式", "留位默认值")


def parse_part_node(html: str) -> dict:
    """单个部件 → {fields: {...}, selects: {...}}。"""
    soup = soup_of(html)
    fields: dict[str, str] = {}
    selects: dict[str, str] = {}
    for tr in _all_rows(soup):
        pairs = row_pairs(tr)
        for key, value in pairs.items():
            if key not in PART_FIELD_LABELS:
                continue
            fields.setdefault(key, value)
    # 下拉（书脊方向/拼版方式/排版方式）单独给一份“显示文字 -> 提交值”
    for control in ("txtSJFX", "txtPBFX", "txtPFFS", "txtLWDJZ"):
        sel = soup.find("select", {"name": control})
        if sel is None:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        if opt is not None and _norm(opt.get_text(strip=True)) and (opt.get("value") or "") != "-0-":
            selects[control] = _norm(opt.get_text(strip=True))
    return {"fields": fields, "selects": selects}


def parse_materials_node(html: str) -> list[dict]:
    """部件辅料（已选材料）→ [{部件名称, 材料编码, 材料名称, 品牌, 单价, 材料长, 材料宽, 指定数量, 系数}]。

    页面上同一批材料会出现两遍（列表区 + 模板区），按整行去重。
    """
    soup = soup_of(html)

    def dedupe(rows: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        out: list[dict] = []
        for row in rows:
            key = tuple(sorted(row.items()))
            if key in seen or not any(row.values()):
                continue
            seen.add(key)
            out.append(row)
        return out

    for width in (11, 10, 12, 9):
        records = grid_by_width(soup, width, must_have=("材料编码", "单价", "系数"))
        if records:
            return dedupe(records)
    return []


def parse_fees_node(html: str) -> dict:
    """费项明细 → {groups: [{数量档信息, 选配项目, fees: {类别: [{部件, 项目, 公式, 金额, 单价}]}}]}。

    每个数量档由一张 12 列的表头块（产品名称/单款数量/款数/总数量/单价/金额 + 选配项目）
    加一大段 `类别标题 + 5 列明细表` 组成；实测 10 档 = 10 组。
    """
    soup = soup_of(html)
    groups: list[dict] = []
    current: dict | None = None
    for table in soup.find_all("table"):
        trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
        if not trs:
            continue
        first = _cells(trs[0])
        # ① 数量档表头：12 列、含「产品名称：」
        if len(first) == 12 and any("产品名称" in _norm(c.get_text(strip=True)) for c in first):
            info: dict[str, str] = {}
            for tr in trs:
                info.update(row_pairs(tr))
            current = {"数量档": info.get("单款数量", ""), "款数": info.get("款数", ""),
                       "总数量": info.get("总数量", ""), "单价": info.get("单价", ""),
                       "金额": info.get("金额", ""), "选配项目": info.get("选配项目", ""),
                       "fees": {}}
            groups.append(current)
            continue
        if current is None:
            continue
        # ② 类别容器：一行两格，首格是类别名，次格里嵌一张 5 列表
        for tr in trs:
            cells = _cells(tr)
            if len(cells) != 2:
                continue
            name = _norm(cells[0].get_text(" ", strip=True)).rstrip("：:")
            if name not in FEE_CATEGORIES:
                continue
            nested = cells[1].find("table")
            if nested is None:
                continue
            rows: list[dict] = []
            header: list[str] | None = None
            for sub in nested.find_all("tr"):
                sub_cells = _cells(sub)
                if len(sub_cells) < 4:
                    continue
                values = [blank_if_placeholder(cell_value(c)) for c in sub_cells]
                if header is None:
                    header = ["部件", "项目", "公式", "金额", "单价"][:len(values)]
                    if not any(v and not v.replace(".", "").isdigit() for v in values[:1]):
                        pass
                    rows.append(dict(zip(header, values)))
                    continue
                rows.append(dict(zip(header, values)))
            current["fees"][name] = rows
    return {"groups": groups}


# ══════════════════ 产品资料 ══════════════════
def parse_cp_node(html: str) -> dict:
    """产品资料（`Add_CPZL.aspx`）→ {fields: {标签: 值}}。

    这一页没有网格，整页就是一张巨型表单：产品类别/尺寸/金额拆分（印前/印刷/印后/
    纸料/辅料/其他）/纸箱/报关/物流… 全部按「标签格 → 右邻格」读。
    """
    return {"fields": forms_of(soup_of(html))}


# ══════════════════ 工序 / 辅料 / 机台 网格 ══════════════════
def parse_cp_process_node(html: str) -> list[dict]:
    """产品工序（`Add_CPGXZL.aspx`，29 列）→ [{工序名称, 工价级别, 工序单价, 起版费, ...}]。"""
    soup = soup_of(html)
    return grid_by_width(soup, 29, must_have=("工序名称", "工序单价"))


def parse_part_process_node(html: str) -> list[dict]:
    """部件工序（`Add_BJGXZL.aspx`，27 列）。

    列比产品工序少「指定起版费」「选配」：`选择/排序号/工序名称/工价级别/工序单价/
    起版费/系数/指定长/指定宽/指定数量/指定单价/指定金额/计价批量/版材…/工序正数/…`。
    """
    soup = soup_of(html)
    return grid_by_width(soup, 27, must_have=("工序名称", "工序单价"))


def parse_part_machines_node(html: str) -> dict:
    """部件机台（`Add_BJJTZL.aspx`）→ {records(30 列机台行), fields(机台参数)}。"""
    soup = soup_of(html)
    return {
        "records": grid_by_width(soup, 30, must_have=("机台名称", "工序单价")),
        "fields": forms_of(soup),
    }


def parse_cp_materials_node(html: str) -> list[dict]:
    """产品辅料（`Add_CPFL.aspx`，8 列）→ [{材料编码, 材料名称, 单价, 材料长, 材料宽, 指定数量, 系数}]。

    页面上还有一块同宽的**编辑表单**（材料名称/品牌/材料规格…），所以 `must_have` 要用
    列表独有的列名，并且 `grid_by_width` 会锁定在表头所在那张表里取行。
    """
    soup = soup_of(html)
    return grid_by_width(soup, 8, must_have=("材料编码", "指定数量", "系数"))


# ══════════════════ 其他费项 ══════════════════
def parse_other_fees_node(html: str) -> list[dict]:
    """其他费用项目（`Add_QTFX.aspx`）→ [{排序号, 费用项目, 选配, 计价公式, 税点, 金额}]。"""
    soup = soup_of(html)
    return grid_by_width(soup, 6, must_have=("费用项目", "计价公式", "金额"), drop=())


# ══════════════════ 需求反馈 ══════════════════
def parse_feedback_node(html: str) -> dict:
    """客户报价需求反馈（`Add_KHBJXQFK.aspx`）。

    反馈正文在 `textarea#txtBGNR` 的**元素内文本**里（不是 value 属性），
    反馈人/反馈日期是空输入框（历史反馈只存正文）。
    """
    soup = soup_of(html)
    content = ""
    box = soup.find("textarea", attrs={"name": "txtBGNR"}) or soup.find("textarea")
    if box is not None:
        content = norm(box.get_text(" ", strip=True) or box.get("value") or "")

    def _val(name: str) -> str:
        el = soup.find("input", attrs={"name": name})
        return norm(el.get("value") or "") if el is not None else ""

    return {
        "反馈内容": content,
        "反馈人": _val("txtBGR"),
        "反馈日期": _val("txtBGRQ"),
    }


# ══════════════════ 手工开版 ══════════════════
def parse_sgkb_node(html: str) -> list[dict]:
    """手工开版资料（`Add_SGKB.aspx`）→ [{资料名称, 类别}]（没有开版资料时为空）。"""
    soup = soup_of(html)
    return grid_by_width(soup, 3, must_have=("资料名称", "类别"), drop=("删除",))


# ══════════════════ 拆分价格设置 ══════════════════
def _tree_of(soup, tree_id: str) -> list[dict]:
    """把一棵 ASP.NET TreeView 摊平成 [{层级, 名称, 节点ID}]（层级由祖先 Nodes 容器算）。"""
    nodes: list[dict] = []
    for a in soup.find_all("a", id=re.compile(rf"^{tree_id}t\d+$")):
        text = _norm(a.get_text(" ", strip=True))
        if not text:
            continue
        depth = 0
        parent = a.parent
        while parent is not None:
            pid = parent.get("id") or ""
            if pid.startswith(tree_id) and pid.endswith("Nodes"):
                depth += 1
            parent = parent.parent
        arg = ""
        m = re.search(r"__doPostBack\([^,]+,\s*'s?([^']*)'\)", a.get("href") or "")
        if m:
            arg = re.sub(r"\\+2", "|", m.group(1))   # ASP.NET TreeView 的层级分隔符
            arg = arg.replace("\\", "")
        nodes.append({"层级": depth, "名称": text, "节点ID": arg})
    return nodes


def parse_split_price_node(html: str) -> dict:
    """拆分价格设置（`KHBJ_SetJEXSMS.aspx`）。

    页面是「两棵勾选树 + 分配按钮」：TreeView1 = 待分配资料（按产品/纸张费用…分组），
    TreeView2 = 已分配资料（拆分价格名称，如 `1.前封面100%烫金（选配）`）。
    """
    soup = soup_of(html)
    name_input = soup.find("input", attrs={"name": "txtXMMC_Input"})
    return {
        "拆分价格名称": _norm(name_input.get("value") or "") if name_input is not None else "",
        "待分配": _tree_of(soup, "TreeView1"),
        "已分配": _tree_of(soup, "TreeView2"),
    }


# ══════════════════ 送货/审核资料 ══════════════════
def parse_product_check_node(html: str) -> dict:
    """产品审核/送货资料（`Add_CPSHZL.aspx`）→ {records(28 列送货明细), fields}。"""
    soup = soup_of(html)
    return {
        "records": grid_by_width(soup, 28, must_have=("产品名称", "送货数量")),
        "fields": forms_of(soup),
    }


# ══════════════════ 折叠面板 ══════════════════
#: 面板 node 名 → `__doPostBack` 的 __EVENTTARGET
PANEL_TARGETS = {
    "description": "Lbtn_CPZL",        # 【产品描述】数量档/选配/部件/其他费用/送货明细
    "material_list": "Lbtn_CLQD",      # 【材料清单】需求数量/库存/报价单价/反馈单价
    "manual_process": "Lbtn_SGGX",     # 【手工工序】
    "change_log": "Lbtn_TLGL",         # 【修改日志】
    "process_list": "Lbtn_GXQD",       # 【工序清单】
    "part_list": "Lbtn_BJZLList",      # 【部件列表】
    "freight": "Lbtn_HYFList",         # 【运费清单】重量/装箱/运费
    "price_mode": "Lbtn_DJMSList",     # 【单价模式】
}


def _label_value_table(table) -> dict[str, str]:
    """「首行是标签、次行是对应值」的表 → {标签: 值}（按列序对齐）。

    运费清单那种一块 `单个重量(KG) | 0.413 | 长mm | 宽mm | …` 就是这种排版，
    列宽不一致（后面几行有 colspan），按行宽抓不到，只能按列序配。
    """
    rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    if len(rows) < 2:
        return {}
    labels = _texts(_cells(rows[0]))
    values = _texts(_cells(rows[1]))
    out: dict[str, str] = {}
    for index, label in enumerate(labels):
        if not label or label in out or index >= len(values):
            continue
        value = blank_if_placeholder(values[index])
        if value:
            out[label] = value
    return out


def parse_panel_node(html: str) -> dict:
    """折叠面板返回页 → {panel, grids, fields, note?}。

    8 个面板都是整页回发，内容渲染在 `div#Pan_gridView*` 里。这里不按面板名硬编码表格：

      * `grids`：容器里所有「有表头 + 有数据 + 表头不是标签」的网格（同宽行按列名对齐）；
      * `fields`：容器里的「标签 → 值」对（运费清单这类是 colspan 排版，只能按标签读：
        单个重量/长/宽/高/Pcs 每箱/Kg 每箱/用箱/平方价/总CBM/单价/金额…）。

    面板之间共用同一个容器，但只有当前选中面板的内容会被渲染，所以直接抓即可。
    """
    soup = soup_of(html)
    selected = ""
    bold: list[str] = []
    for a in soup.find_all("a"):
        aid = a.get("id") or ""
        if not aid.startswith("Lbtn_"):
            continue
        style = (a.get("style") or "").replace(" ", "").lower()
        if "font-weight:bold" in style:
            bold.append((aid, _norm(a.get_text())))
    for aid, label in bold:
        if aid in set(PANEL_TARGETS.values()):
            selected = label
            break
    if not selected and bold:
        selected = bold[0][1]

    grids: list[dict] = []
    fields: dict[str, str] = {}
    seen: set[tuple[str, ...]] = set()
    for div in soup.find_all("div", id=re.compile(r"^Pan_gridView")):
        for key, value in forms_of(div).items():
            if value and not fields.get(key):
                fields[key] = value
        for table in div.find_all("table"):
            rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
            if len(rows) < 2:
                continue
            header = [_norm(c.get_text(" ", strip=True))
                      for c in rows[0].find_all(["td", "th"], recursive=False)]
            if len(header) < 3:
                continue
            if any(h.endswith(("：", ":")) for h in header):
                continue        # 「标签/值」表单块，不是网格
            key = tuple(header)
            if key in seen:
                continue
            records = grid_by_width(table, len(header), drop=())
            if not records:
                for label, value in _label_value_table(table).items():
                    fields.setdefault(label, value)
                continue
            seen.add(key)
            grids.append({"header": header, "rows": records})

    result: dict = {"panel": selected, "grids": grids, "fields": fields}
    if not grids:
        result["note"] = ("该面板在这一行没有渲染出列表（空列表，或列表未设置列）；"
                          "fields 里是该面板的标签/值区")
    return result


NODE_PARSERS = {
    # 产品/部件级子数据
    "quantities": parse_quantities_node,
    "part": parse_part_node,
    "part_materials": parse_materials_node,
    "fees": parse_fees_node,
    "cp": parse_cp_node,
    "cp_process": parse_cp_process_node,
    "cp_materials": parse_cp_materials_node,
    "part_process": parse_part_process_node,
    "part_machines": parse_part_machines_node,
    "other_fees": parse_other_fees_node,
    "feedback": parse_feedback_node,
    "sgkb": parse_sgkb_node,
    "split_price": parse_split_price_node,
    "product_check": parse_product_check_node,
}
