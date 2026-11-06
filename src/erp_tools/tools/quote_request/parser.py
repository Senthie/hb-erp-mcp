"""报价需求页（BJ_KHBJXQ/Add.aspx）解析。

页面有两种布局（历史事故，见 README「坑位清单」）：
  A) 产品描述/备注/配件描述/修改记录/检测项目 这些「标签 -> textarea」行嵌在
     td#tdCPZLLIst 内；
  B) td#tdCPZLLIst 仍存在且有内容（产品名称/规格/数量…），但不含这些行，
     它们在 div#Pan_gridView7 里。
所以查找必须按优先级多作用域回退，不能写成 `td or soup`。

数量分两种，不能混：
  * 单值「数量：」= 汇总数量，onclick 第一个参数固定 '-1'；
  * 「产品数量：」行里的 CPSL 格子 = 多语言明细，第一个参数是 GUID。
"""

from __future__ import annotations

import re

from ...core.aspnet import soup_of
from ...core.htmlutil import (
    TEXTAREA_LABELS,
    extract_labeled_textareas,
    get_input_value,
    get_selected_text,
    get_textarea_value,
    label_value_map,
    norm_label,
    parse_qty,
)

#: tdCPZLLIst 里「标签 -> 字段名」
PRODUCT_FIELD_LABELS = {
    "产品名称：": "CPMC",
    "是否有配件：": "SFYPJ",
    "规格：": "GG",
    "装订方式：": "ZDFS",
    "送货方式：": "SHFS",
    "客户单号：": "KHCPMC",
    "香港单号：": "KHDDH",
    "数量：": "SL",
    "包装方式：": "BZFS",
    "台板类型：": "TBLX",
    "产品类别：": "CPLB",
}

#: CPSL 明细输入框的识别：onclick 形如 OpenInputCPSLZLData('<GUID>',...)
CPSL_ONCLICK_RE = re.compile(r"OpenInputCPSLZLData\(\s*'(?!-1')")

_TZS_RE = re.compile(r"共【(\d+)】款")
_YYS_RE = re.compile(r"每款【(\d+)】语言")


def find_cpsl_cells(soup, td=None) -> list:
    """找多语言产品数量明细（CPSL1..10）的输入框。

    两个要点：
      1. 单值「数量：」的 onclick 第一个参数固定是 '-1'，必须排除，
         否则会把汇总数量误当成 CPSL1；
      2. 不能只靠行文字判断——「费用项明细」表头里也有「产品数量」四个字，
         包裹整页的外层 tr 文本里同样含它。
    """

    def cells_in(scope) -> list:
        return [
            inp
            for inp in scope.find_all("input")
            if CPSL_ONCLICK_RE.search(inp.get("onclick") or "")
        ]

    for scope in (td, soup.find("div", {"id": "Pan_gridView7"}), soup):
        if scope is None:
            continue
        for label in scope.find_all("td"):
            if norm_label(label.get_text()) != "产品数量":
                continue
            tr = label.find_parent("tr")
            cells = cells_in(tr) if tr is not None else []
            if cells:
                return cells
        cells = cells_in(scope)
        if cells:
            return cells
    return []


def extract_product_qty(td, soup=None) -> dict[str, int | float | str | None]:
    """提取 CPSL1..CPSL10；页面没有这条明细时返回空 dict（保持 None）。"""
    result: dict[str, int | float | str | None] = {}
    cells = find_cpsl_cells(soup if soup is not None else td, td)
    for i, inp in enumerate(cells[:10], start=1):
        result[f"CPSL{i}"] = parse_qty(inp.get("value"))
    return result


def parse_quote_page(html: str, fallback_ywy: str | None = None,
                     record_id: str | None = None) -> dict:
    """把报价需求页解析成 ERP 客户订单需求 JSON（字段名与 ERP 一致）。"""
    soup = soup_of(html)

    # ---- 明细区 textarea（按标签定位，兼容两种布局）----
    ta = extract_labeled_textareas(soup, TEXTAREA_LABELS)
    cpms = ta.get("产品描述") or ""
    remark1 = ta.get("配件描述")
    remark3 = ta.get("修改记录")
    remark4 = ta.get("检测项目")

    # ---- 顶层字段 ----
    bdbh = get_input_value(soup, "txtBDBH")
    ywy = get_selected_text(soup, "txtYGXM")
    khzl = get_input_value(soup, "txtKHMC")
    gdy = get_selected_text(soup, "txtGDYXM")
    # 明细区「备注」行才是 ERP 字段 Remark（ModifyFieldValue(...,'Remark','备注：')）
    remark = ta.get("备注") or get_textarea_value(soup, "txtRemark")
    cpmx = get_textarea_value(soup, "txtCPMX")

    # YWY 为空时用 CSV 制单人兜底（用户已确认可接受）
    if (ywy is None or str(ywy).strip() == "") and fallback_ywy:
        ywy = str(fallback_ywy).strip()

    # ---- 产品明细块 ----
    td = soup.find("td", {"id": "tdCPZLLIst"})
    pf = label_value_map(td, PRODUCT_FIELD_LABELS)
    pqty = extract_product_qty(td, soup)

    # ---- 规格 -> CD / KD / GD ----
    spec = pf.get("GG") or ""
    cd = kd = gd = 0
    if "*" in spec:
        parts = spec.split("*")
        try:
            if len(parts) >= 1:
                cd = int(float(parts[0]))
            if len(parts) >= 2:
                kd = int(float(parts[1]))
            if len(parts) >= 3:
                gd = int(float(parts[2]))
        except ValueError:
            pass

    # ---- 从产品描述里尝试提取 TZS / YYS ----
    tzs = yys = None
    m = _TZS_RE.search(cpms)
    if m:
        tzs = int(m.group(1))
    m = _YYS_RE.search(cpms)
    if m:
        yys = int(m.group(1))

    return {
        # 记录主键（CSV 里的 id），用于断点续跑/去重
        "id": record_id,
        "BDBH": bdbh,
        "YWY": ywy,
        "KHZL": khzl,
        "GDY": gdy,
        "Remark": remark,
        "CPMX": cpmx,
        "CPDL": None,
        "CPLB": pf.get("CPLB"),
        "Drop_SJFX": None,
        "ZDFS": pf.get("ZDFS"),
        "CPMC": pf.get("CPMC"),
        "TZS": tzs,
        "YYS": yys,
        "DWMC": None,
        "CPSL1": pqty.get("CPSL1"),
        "CPSL2": pqty.get("CPSL2"),
        "CPSL3": pqty.get("CPSL3"),
        "CPSL4": pqty.get("CPSL4"),
        "CPSL5": pqty.get("CPSL5"),
        "CPSL6": pqty.get("CPSL6"),
        "CPSL7": pqty.get("CPSL7"),
        "CPSL8": pqty.get("CPSL8"),
        "CPSL9": pqty.get("CPSL9"),
        "CPSL10": pqty.get("CPSL10"),
        "BZ": None,
        "KHMBDJ": None,
        "CD": cd,
        "KD": kd,
        "GD": gd,
        "BZFS": pf.get("BZFS"),
        "TBLX": pf.get("TBLX"),
        "SHFS": pf.get("SHFS"),
        "KHCPMC": pf.get("KHCPMC"),
        "JHRQ": None,
        "FSCMC": None,
        "OLD_KHDDH": None,
        "SFYPJ": pf.get("SFYPJ"),
        "Remark1": remark1,
        "PXH": 1,
        "CPSX": None,
        "CPLX": None,
        "WCRQ": None,
        "KHDDH": pf.get("KHDDH"),
        "chkXBJF": None,
        "ECPMC": None,
        "chkZDBJF": False,
        "ZXLB": None,
        "ZBLX": None,
        "ZXFS": None,
        "chkBSYTB": False,
        "chkBSYJZX": False,
        "DKMC": None,
        "YSLX": None,
        "SHDQ": None,
        "KHDQ": None,
        "CPMS": cpms,
        "BGMS": None,
        "Remark4": remark4,
        "Remark3": remark3,
    }
