"""客户报价【详情】页（BaoJia/BJ_KHBJ/Add.aspx）解析。

页面事实（实测 2026-11-12，QXID=857，`?id=<记录ID>&Option=3`）：

  * `id` 是**报价产品行**的 ID（列表页的「记录ID」），所以一页 = 一个产品行；
  * 打开时 `Option=3` → 全页只读（表头 input/select 都是 `disabled`），
    真正的值在**表格单元格里的 `<INPUT readonly VALUE='...'>`**（属性名是大写 `VALUE`，
    且**没有 name/id**），所以取值不能靠 `name=`，要按「标签格 → 右邻格」读；
  * 区块（按出现顺序）：
      `table#Main_Top`        表头（表单编号/业务人员/客户资料/有效天数/联系人/单价体系/付款方式/币种/汇率/发票类别/付款期限/跟单员）
      产品信息表 (3×6)        产品名称/规格/装订方式、香港单号/数量/单价/金额、客户产品名称/款数/语言数/修改记录
      数量明细表 (3×12)       产品数量/单价/金额 ×4 组
      部件明细表 (33×17)      每个部件一行 + 4~5 行「印前工序/印刷机台/印后工序/部件辅料/部件备注」
      费用表 (3×8)            纸箱费用/台板费用/其他金额/海运费用/国内运费/FOB盐田/FOBHK/FOB蛇口/CIF/快递费/部分卡板费用
      `table#Main_Foot`       制单人/制单日期/审核人/审核日期
      产品描述 (textarea rows=58, 无 name)   多段历史，段头形如 `【许月霞   112610】`
  * `产品名称` 的 `<span onclick="OpenInputCPZLData('<CPID>','<PID>','0')">`：**CPID=产品明细 ID**，
    PID=本页 `lblID`（= 列表的记录ID）。`OpenInputCPSLZLData('-1','<PID>','<CPID>')` 是数量汇总弹窗
    （第一个参数固定 `-1` 才代表汇总）。
  * 其余大量 `txtBJ*`/`txtKJ*`/`txtDT*` 是弹窗编辑器的暂存控件，只读页里都是空的，不要拿它们当数据源。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...core.aspnet import soup_of

_WS_RE = re.compile(r"[\s\u3000\u00a0\u200b\ufeff]+")
_CPZL_RE = re.compile(r"OpenInputCPZLData\(\s*'([^']*)'\s*,\s*'([^']*)'")
#: 产品名称后面跟的「【 删除】」按钮文字
_DEL_SPAN_RE = re.compile(r"【\s*删除\s*】")

#: 部件明细表里跟在部件行后面的子行标签
PART_SUB_LABELS = ("印前工序", "印刷机台", "印后工序", "部件辅料", "部件备注", "部件贴数")

#: 详情里必有的表头标签（缺了要在 diagnostics 里说明）。
#: 只列「一定存在且一定渲染」的；付款方式/币种/跟单员这类未选中时就是空串，
#: 而 `汇率`/`发票税率`/`折扣率` 没有标签格（与 币种/发票类别 挤在同一格），见 `HEADER_SCALARS`。
HEADER_LABELS = ("表单编号", "业务人员", "客户资料")
#: 没有标签格、只能按 input name 取的表头数值
HEADER_SCALARS = {
    "txtHL": "汇率",
    "txtFPSL": "发票税率",
    "txtZKL": "折扣率",
    "txtJYLL": "建议利润(%)",
    "txtYXTS": "有效天数",
    "txtSJ": "手机",
    "txtCZ": "传真",
}
PRODUCT_LABELS = ("产品名称", "规格", "装订方式", "香港单号", "数量", "单价",
                  "金额", "客户产品名称", "款数", "语言数", "修改记录")
FEE_LABELS = ("纸箱费用", "台板费用", "其他金额", "海运费用", "国内运费",
              "FOB盐田", "FOBHK", "FOB蛇口", "CIF", "快递费", "部分卡板费用")


def norm(text: str) -> str:
    return _WS_RE.sub(" ", (text or "")).strip()


def blank_if_placeholder(value: str) -> str:
    """ERP 用 `.` / `-` 表示空单元格。"""
    return "" if norm(value) in (".", "-", "—") else norm(value)


def _label(text: str) -> str:
    """`单价体系：` / `单价体系:` → `单价体系`。"""
    return norm(text).rstrip("：:").strip()


def _is_label(text: str) -> bool:
    raw = norm(text)
    return raw.endswith(("：", ":")) and len(raw) <= 20


def cell_value(td) -> str:
    """取单元格的值：select 选中项 > 只读 input > 纯文本。

    一个单元格可能同时有 `select`+辅助 `input`（如币种格里有 汇率）或一堆按钮，
    所以先看 select；占位项（`-0-`/`<--请选择-->`）一律当空，**不要**把全部 option 文字当成值。

    注意：有些只读页的 `<select>` 没有任何 `selected` 属性（值本来就是空），
    这时不能退化成「整格文字」——那会把所有 option 连成一串当值。有 select 时只认
    select/input，都没有就返回空串。
    """
    has_select = False
    for sel in td.find_all("select"):
        has_select = True
        opt = sel.find("option", selected=True)
        if opt is None:
            continue
        value = (opt.get("value") or "").strip()
        label = norm(opt.get_text(strip=True))
        if value in ("", "-0-") or label.startswith("<--"):
            continue
        return label
    for inp in td.find_all("input"):
        if (inp.get("type") or "").lower() in ("button", "submit", "image", "checkbox",
                                               "radio", "hidden"):
            continue
        value = norm(inp.get("value") or "")
        if value:
            return value
    if has_select:
        return ""
    return norm(td.get_text(" ", strip=True))


def row_pairs(tr) -> dict[str, str]:
    """把「标签格 / 值格」交替的一行解析成 {标签: 值}。"""
    cells = tr.find_all("td", recursive=False)
    out: dict[str, str] = {}
    i = 0
    while i < len(cells):
        text = norm(cells[i].get_text(" ", strip=True))
        if _is_label(text) and i + 1 < len(cells):
            key = _label(text)
            if key not in out:
                out[key] = cell_value(cells[i + 1])
            i += 2
            continue
        i += 1
    return out


def _tables(soup) -> list:
    return soup.find_all("table")


def _own_rows(table) -> list:
    """只取**属于这张表**的行（排除内嵌子表里的 tr）。"""
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _find_table_by_first_label(soup, label: str):
    """找「某一行第一个格子就是 `<label>：`」的表；多个命中时取最小的（最内层）。"""
    hits = []
    for table in _tables(soup):
        for tr in _own_rows(table):
            cells = tr.find_all("td", recursive=False)
            if not cells:
                continue
            if _label(cells[0].get_text(" ", strip=True)) == label:
                hits.append(table)
                break
    if not hits:
        return None
    return min(hits, key=lambda t: len(t.find_all("td")))


def _find_table_by_header(soup, headers: tuple[str, ...]):
    """找表头（第一行）同时包含这些列名的表。"""
    for table in _tables(soup):
        rows = _own_rows(table)
        if not rows:
            continue
        cells = {norm(c.get_text(" ", strip=True)) for c in
                 rows[0].find_all(["td", "th"], recursive=False)}
        if set(headers) <= cells:
            return table
    return None


def parse_pairs_table(table) -> dict[str, str]:
    """一张全是「标签/值」行的表 → 一个 dict。"""
    out: dict[str, str] = {}
    if table is None:
        return out
    for tr in _own_rows(table):
        for key, value in row_pairs(tr).items():
            out.setdefault(key, value)
    return out


# ══════════════════ 各区块 ══════════════════
def parse_product_info(soup) -> tuple[dict[str, str], dict[str, str]]:
    """产品信息表 → (字段, {'cpid':..., 'pid':...})。"""
    table = _find_table_by_first_label(soup, "产品名称")
    info = parse_pairs_table(table)
    ids: dict[str, str] = {}
    if table is not None:
        for span in table.find_all("span"):
            raw = span.get("onclick") or ""
            m = _CPZL_RE.search(raw)
            if m:
                ids["cpid"], ids["pid"] = m.group(1), m.group(2)
                break
    if "产品名称" in info:
        info["产品名称"] = norm(_DEL_SPAN_RE.sub("", info["产品名称"]))
    return info, ids


def _find_quantity_table(soup):
    """数量明细表：表头首格是「产品数量」（后面几组写「数量」）。

    不能用「页面里有没有『产品数量』几个字」判断：汇总数量那个 span 的 title 里永远有
    `单击即可以调出【产品数量】明细信息`，会让所有页面都“看起来有表”。
    """
    for candidate in _tables(soup):
        rows = _own_rows(candidate)
        if not rows:
            continue
        cells = [norm(c.get_text(" ", strip=True))
                 for c in rows[0].find_all("td", recursive=False)]
        if cells[:1] == ["产品数量"] and "单价" in cells and "金额" in cells:
            return candidate
    return None


def _find_part_table(soup):
    return _find_table_by_header(soup, ("部件名称", "开料尺寸", "切纸数量"))


def _call_first_arg(el, func: str) -> str:
    """从元素（或它的子元素）的 onclick 里取 `func('X', ...)` 的第一个参数。"""
    targets = [el, *el.find_all(True)]
    for item in targets:
        raw = item.get("onclick") or ""
        m = re.search(rf"{func}\(\s*'([^']*)'", raw)
        if m:
            return m.group(1)
    return ""


def parse_quantities(soup) -> list[dict[str, str]]:
    """数量明细表 → [{数量,单价,金额}, ...]。

    表头只有**第一组**写「产品数量」，后面几组写成「数量」（实测 2026-11），
    所以判定条件是：首格=产品数量 且同行有 单价/金额；数据按每 3 格一组切。
    表头不一定在第一行（前面可能夹空行），逰行找。
    """
    table = _find_quantity_table(soup)
    if table is None:
        return []

    rows = _own_rows(table)
    headers = [norm(c.get_text(" ", strip=True))
               for c in rows[0].find_all("td", recursive=False)]
    out: list[dict[str, str]] = []
    for tr in rows[1:]:
        tds = tr.find_all("td", recursive=False)
        cells = [cell_value(td) for td in tds]
        if len(cells) != len(headers):
            break  # 数量明细到此结束（后面是部件表等）
        for start in range(0, len(cells) - 2, 3):
            quantity, price, amount = (blank_if_placeholder(cells[start]),
                                       blank_if_placeholder(cells[start + 1]),
                                       blank_if_placeholder(cells[start + 2]))
            if quantity or price or amount:
                tier = {"数量": quantity, "单价": price, "金额": amount}
                # CPSLID 挂在「数量」那一格的 span 上，一行有 3 档，不能按行取
                cpslid = _call_first_arg(tds[start], "OpenInputCPSLZLData")
                if cpslid and cpslid != "-1":
                    tier["cpslid"] = cpslid
                out.append(tier)
    return out


def parse_parts(soup) -> list[dict[str, str]]:
    """部件明细表 → [{部件名称, P数, ..., 印前工序, 印刷机台, 印后工序, 部件辅料, 部件备注}]。"""
    table = _find_part_table(soup)
    if table is None:
        return []

    rows = _own_rows(table)
    if len(rows) < 2:
        return []
    headers = [norm(c.get_text(" ", strip=True))
               for c in rows[0].find_all(["td", "th"], recursive=False)]
    parts: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for tr in rows[1:]:
        cells = [cell_value(td) for td in tr.find_all("td", recursive=False)]
        texts = [norm(td.get_text(" ", strip=True)) for td in tr.find_all("td", recursive=False)]
        if len(cells) >= len(headers) and not _is_label(texts[0]):
            current = dict(zip(headers, cells))
            bjid = _call_first_arg(tr, "OpenInputBJZLData")
            if bjid:
                current["bjid"] = bjid
            bjgx = _call_first_arg(tr, "OpenInputBJGXZLData")
            if bjgx:
                current.setdefault("bjid", bjgx)
            parts.append(current)
            continue
        # 子行：`印前工序： | 分纸->切纸`
        label = _label(texts[0]) if texts else ""
        if current is None or label not in PART_SUB_LABELS or len(cells) < 2:
            continue
        value = cells[1] if cells[1] else " ".join(texts[1:])
        current.setdefault(label, blank_if_placeholder(value))
    return parts


def _tier_sort_key(tier: dict[str, str]) -> tuple:
    def num(text: str) -> float:
        try:
            return float((text or "").replace(",", ""))
        except ValueError:
            return float("inf")
    return (num(tier["数量"]), num(tier["单价"]))


def parse_optionals(soup) -> list[dict]:
    """「选配项目列表」→ [{选配项目, tiers:[{数量,单价,金额}, ...]}, ...]。

    这一段**不是每个单据都有**（实测 123 行里 28 行有），夹在「数量明细」和「部件明细」之间；
    表头是 `选配项目/数量/单价/金额` 横向重复 3 组，所以一行里有 3 个「项目 + 一档数量」。

    两种实测数据形态：

    * **整段完全重复**（如【封面】烫金）：同样的 (数量,单价,金额) 出现两次 →
      去掉重复档，并在该项记 `dropped_duplicates`；
    * **同名但两条价格曲线交错**（如【产品】0.02mm厚PP空白收缩袋）：
      同一个数量出现两次、单价不同 → 这是两条报价曲线，**不能丢也不能猜归属**，
      所以留在同一条里（按数量排序后相邻），并在该项记 `同名重复档`。
    """
    table = _find_table_by_header(soup, ("选配项目", "数量", "单价", "金额"))
    if table is None:
        return []

    groups: dict[str, dict] = {}
    order: list[str] = []
    for tr in _own_rows(table)[1:]:
        cells = [cell_value(td) for td in tr.find_all("td", recursive=False)]
        for start in range(0, len(cells) - 3, 4):
            name, quantity, price, amount = cells[start:start + 4]
            name = blank_if_placeholder(name)
            if not name:
                continue
            tier = {
                "数量": blank_if_placeholder(quantity),
                "单价": blank_if_placeholder(price),
                "金额": blank_if_placeholder(amount),
            }
            entry = groups.get(name)
            if entry is None:
                entry = {"选配项目": name, "tiers": []}
                groups[name] = entry
                order.append(name)
            if tier in entry["tiers"]:
                entry["dropped_duplicates"] = entry.get("dropped_duplicates", 0) + 1
                continue
            entry["tiers"].append(tier)

    result: list[dict] = []
    for name in order:
        entry = groups[name]
        entry["tiers"].sort(key=_tier_sort_key)
        quantities = [tier["数量"] for tier in entry["tiers"]]
        repeated = len(quantities) - len(set(quantities))
        if repeated:
            entry["同名重复档"] = repeated
        result.append(entry)
    return result


def parse_description(soup) -> str:
    """产品描述：详情页里最长的那段只读 textarea。"""
    best = ""
    for area in soup.find_all("textarea"):
        text = (area.string or area.get_text() or "")
        if len(text) > len(best):
            best = text
    return best.strip()


def parse_fees(soup) -> dict[str, str]:
    return parse_pairs_table(_find_table_by_first_label(soup, "纸箱费用"))


def parse_header(soup) -> dict[str, str]:
    """表头：`Main_Top` + 按控件名取的数值（汇率/发票税率/折扣率…）。"""
    out: dict[str, str] = {}
    top = soup.find("table", id="Main_Top")
    if top is not None:
        out.update(parse_pairs_table(top))
    for control, label in HEADER_SCALARS.items():
        el = soup.find("input", {"name": control}) or soup.find("input", {"id": control})
        if el is None:
            continue
        value = norm(el.get("value") or "")
        if value and not out.get(label):
            out[label] = value
    return out


def parse_audit(soup) -> dict[str, str]:
    foot = soup.find("table", id="Main_Foot")
    return parse_pairs_table(foot)


def extract_ids(soup) -> dict[str, str]:
    """`lblID` / `txtBDBH` / `txtKHID` / `txtKHBM` / `txtQXID` → 短名。"""
    mapping = {"txtBDBH": "bdbh", "txtKHID": "khid", "txtKHBM": "khbm",
               "txtQXID": "qxid"}
    out: dict[str, str] = {}
    lbl = soup.find("input", {"id": "lblID"}) or soup.find("input", {"name": "lblID"})
    if lbl is not None:
        out["pid"] = (lbl.get("value") or "").strip()
    for control, key in mapping.items():
        el = soup.find("input", {"name": control}) or soup.find("input", {"id": control})
        if el is not None and (el.get("value") or "").strip():
            out[key] = el.get("value").strip()
    return out


# ══════════════════ 总入口 ══════════════════
@dataclass
class PriceDetail:
    record_id: str = ""
    bdbh: str = ""
    cpid: str = ""
    pid: str = ""
    header: dict[str, str] = field(default_factory=dict)
    product: dict[str, str] = field(default_factory=dict)
    quantities: list[dict[str, str]] = field(default_factory=list)
    optionals: list[dict] = field(default_factory=list)
    parts: list[dict[str, str]] = field(default_factory=list)
    fees: dict[str, str] = field(default_factory=dict)
    audit: dict[str, str] = field(default_factory=dict)
    description: str = ""
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "bdbh": self.bdbh,
            "cpid": self.cpid,
            "pid": self.pid,
            "header": self.header,
            "product": self.product,
            "quantities": self.quantities,
            "optionals": self.optionals,
            "parts": self.parts,
            "fees": self.fees,
            "audit": self.audit,
            "description": self.description,
            "diagnostics": self.diagnostics,
        }


def parse_price_detail(html: str, *, record_id: str = "") -> PriceDetail:
    soup = soup_of(html)
    ids = extract_ids(soup)
    product, product_ids = parse_product_info(soup)

    detail = PriceDetail(
        record_id=record_id or ids.get("pid", ""),
        bdbh=ids.get("bdbh", ""),
        cpid=product_ids.get("cpid", ""),
        pid=product_ids.get("pid", "") or ids.get("pid", ""),
        header=parse_header(soup),
        product=product,
        quantities=parse_quantities(soup),
        optionals=parse_optionals(soup),
        parts=parse_parts(soup),
        fees=parse_fees(soup),
        audit=parse_audit(soup),
        description=parse_description(soup),
    )

    page_text = soup.get_text(" ", strip=True)
    missing = [k for k in HEADER_LABELS if k not in detail.header]
    if missing:
        detail.diagnostics.append(f"表头缺少字段：{', '.join(missing)}")
    missing = [k for k in PRODUCT_LABELS if k not in detail.product]
    if missing:
        detail.diagnostics.append(f"产品信息缺少字段：{', '.join(missing)}")
    # 下面这几段"没有"分两种：页面本来就没这段（正常，如套装单没有部件明细），
    # 和页面上有这段却没解析出行（结构变了，要报警）。存在性按**表格结构**判断，
    # 不能用页面文字（汇总数量的 title 里永远有「产品数量」）。
    if not detail.parts:
        detail.diagnostics.append(
            "页面上有部件明细表但没解析到行（结构可能变了）"
            if _find_part_table(soup) is not None else "页面没有部件明细表"
        )
    if not detail.quantities:
        detail.diagnostics.append(
            "页面上有数量明细表但没解析到行（结构可能变了）"
            if _find_quantity_table(soup) is not None else "页面没有数量明细表（只有汇总数量）"
        )
    if not detail.optionals and "选配项目" in page_text:
        detail.diagnostics.append("页面上有「选配项目」但没有解析到行（表格结构可能变了）")
    missing_fees = [k for k in ("纸箱费用", "台板费用") if k not in detail.fees]
    if missing_fees:
        detail.diagnostics.append(f"费用区缺少：{', '.join(missing_fees)}")
    return detail
