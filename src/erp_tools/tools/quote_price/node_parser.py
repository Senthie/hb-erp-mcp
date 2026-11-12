"""客户报价详情页里「点开才有的子数据」解析（C 方案：数量/部件/部件辅料/费项）。

这些子页都是老 WebForms 弹窗，共同特点：
  * 数据在**嵌套很深的 table** 里，`tr.find_parent("table") is table` 这种“只要本表行”的
    过滤在这里会把真正的数据行滤掉 —— 所以本项目统一按「行宽」抓：
    先看目标行应该有几个 `td`，再在全文档里按宽度取行；
  * 单元格值可能是 `select` 的选中项、无 name 的只读 input，或者纯文本。

四类子页（实测 2026-11-12，账号 2069）：

| node | 子页 | 内容 |
| --- | --- | --- |
| quantities | `Add_CPSL.aspx` | 数量档：序号/数量/单价/金额/折扣率/折后单价/折后金额/建议利润/客户目标单价/成交否/单款数量 |
| part | `Add_BJZL.aspx` | 单个部件：部件名称/P数/尺寸/比例关系/数量/正反面颜色与专色数/书脊方向/拼版方式/排版留位 |
| part_materials | `Add_BJFL.aspx` | 该部件已选辅料：部件名称/材料编码/材料名称/品牌/单价/材料长宽/指定数量/系数 |
| fees | `Brow_FXMX.aspx` | 每个数量档的费项明细：纸张/辅料/印前/印刷/版费/印后/其他 |
"""

from __future__ import annotations

import re
from typing import Iterable

from ...core.aspnet import soup_of
from .detail_parser import _label, _is_label, blank_if_placeholder, cell_value, row_pairs

#: 材料编码形如 03.03.05.03.0347
CODE_RE = re.compile(r"^\d{2}(?:\.\d{2}){3,4}$")
#: 费项明细里的类别标题
FEE_CATEGORIES = ("纸张费用", "辅料费用", "印前费用", "印刷费用", "版费",
                  "印后费用", "其他费用")


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _all_rows(soup) -> list:
    return soup.find_all("tr")


def _cells(tr) -> list:
    cells = tr.find_all("td", recursive=False)
    if not cells:
        cells = tr.find_all("th", recursive=False)
    return cells


def _texts(cells: Iterable) -> list[str]:
    return [_norm(c.get_text(" ", strip=True)) for c in cells]


def grid_by_width(soup, width: int, must_have: tuple[str, ...] = ()) -> list[dict]:
    """按「行宽 + 表头关键字」在全文档里找一张网格，返回数据行字典列表。"""
    header: list[str] | None = None
    records: list[dict] = []
    for tr in _all_rows(soup):
        cells = _cells(tr)
        if len(cells) != width:
            continue
        texts = _texts(cells)
        if header is None:
            if must_have and not all(any(label in t for t in texts) for label in must_have):
                continue
            header = [t or f"col{i + 1}" for i, t in enumerate(texts)]
            continue
        if not any(texts):
            continue
        if all(any(label in t for t in texts) for label in must_have):
            continue        # 表头重复出现，跳过
        values = [blank_if_placeholder(cell_value(c)) for c in cells]
        if not any(values):
            continue
        records.append(dict(zip(header, values)))
    return records


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


NODE_PARSERS = {
    "quantities": parse_quantities_node,
    "part": parse_part_node,
    "part_materials": parse_materials_node,
    "fees": parse_fees_node,
}
