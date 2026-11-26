"""列表页 / 详情页 / 弹窗子页的通用解析。

这三个页面（生产施工单、采购订单、产品核价）与报价需求页同源，所以：

  * 列表：`gridlist.parse_grid_page` 直接可用（`div#gViewDiv > table#gridView`）；
  * 详情：`Option=3` 只读页，值只能「标签格 → 右邻格」读；数据表格是内嵌 `table`
    （施工单的部件表 / 送货表，采购订单的 `gridView2` 材料明细）；
  * 子页：页面 JS 里每个 `Open*` 函数都是「写 `lblSelect*` 隐藏字段 → 点某个
    `btnSelect*`」，**响应里的 `showModalDialog('xxx.aspx?...')` 才是子页 URL**。
    这里不硬编码每个函数的入参，而是**从函数体反推**「参数 → 隐藏字段」映射，
    再用页面里真实出现的调用（带 GUID 实参）去填充，避免参数错位。
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field

from ...core.aspnet import soup_of
from ..quote_price.detail_parser import (
    _is_label,
    blank_if_placeholder,
    cell_value,
    norm,
    row_pairs,
)

#: 详情页里不是业务数据的标签（内部 ID / 系统字段）
FORM_NOISE = frozenset({
    "PID", "ID", "CPID", "BJID", "CPSLID", "QXID", "QXBH", "SetPageID",
    "TableName", "DivName", "FieldName", "DataSource", "DataSet", "LoginUserID",
})

#: 合计行标记
TOTAL_MARKERS = ("合计",)

_POPUP_RE = re.compile(r"""showModalDialog\(\s*['"]([^'"]+)['"]""", re.I)
_HREF_RE = re.compile(r"""window\.open\(\s*['"]([^'"]+)['"]""", re.I)
_FUNC_RE = re.compile(r"function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*\{")
_CLICK_RE = re.compile(r"""\$HB\(\s*["']([^"']+)["']\s*\)\.click\(\)""")
#: 页面里有的赋值**不带分号**（`= PID`），所以只能截到行尾，否则会把下一行吞进来
_SET_RE = re.compile(r"""\$HB\(\s*["']([^"']+)["']\s*\)\.value\s*=\s*([^;\r\n]+)""")
_INPUTDATA_RE = re.compile(r"""InputData\(\s*["']([^"']+)["']\s*,\s*([^)\r\n]+)""")
_CALL_RE = re.compile(r"\b(Open[A-Za-z_]\w*)\s*\(([^)]*)\)")


# ══════════════════ 详情页 ══════════════════
def _own_rows(table) -> list:
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _cells(tr) -> list:
    cells = tr.find_all("td", recursive=False)
    if not cells:
        cells = tr.find_all("th", recursive=False)
    return cells


def _unique(names: list[str]) -> list[str]:
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


def parse_fields(soup) -> dict[str, str]:
    """全文档「标签格 → 值格」汇总（同名标签保留第一个有值的）。"""
    pairs: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        for key, value in row_pairs(tr).items():
            key = key.strip()
            if not key or key in FORM_NOISE:
                continue
            if key not in pairs or (not pairs[key] and value):
                pairs[key] = value
    return {k: v for k, v in pairs.items() if v}


def parse_grids(soup, *, min_columns: int = 3, min_rows: int = 0) -> list[dict]:
    """内联网格：第一行当表头、其余当数据；跳过「标签/值」表单块与合计行。

    **表头存在但零行**的情况要保留（`rows: []`）——采购订单新建单只填了表头时
    `gridView2` 就是「33 列表头 + 0 行」，如果直接丢掉，调用方无法区分
    「这张单没有明细网格」和「明细网格是空的」。只有带 `id` 的真实数据网格才这样处理，
    避免把页面上大量装饰性空表格也吐出来。
    """
    grids: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for table in soup.find_all("table"):
        rows = _own_rows(table)
        if len(rows) < min_rows + 1:
            continue
        header = [norm(c.get_text(" ", strip=True)) for c in _cells(rows[0])]
        if len(header) < min_columns:
            continue
        if any(h.endswith(("：", ":")) for h in header):
            continue                       # 「标签 / 值」表单块
        if not any(header):
            continue
        names = _unique(header)
        key = tuple(names)
        if key in seen:
            continue
        records: list[dict] = []
        for tr in rows[1:]:
            cells = _cells(tr)
            if len(cells) != len(names):
                continue
            texts = [norm(c.get_text(" ", strip=True)) for c in cells]
            if not any(texts):
                continue
            if any(marker in "".join(texts) for marker in TOTAL_MARKERS):
                continue
            values = [blank_if_placeholder(cell_value(c)) for c in cells]
            if not any(values):
                continue
            records.append(dict(zip(names, values)))
        table_id = table.get("id") or ""
        if not records and not table_id:
            continue                       # 装饰性空表格，丢掉
        seen.add(key)
        grids.append({
            "table": table_id,
            "header": names,
            "rows": records,
        })
    return grids


def parse_textareas(soup) -> dict[str, str]:
    out: dict[str, str] = {}
    for el in soup.find_all("textarea"):
        name = el.get("name") or ""
        text = norm(el.string or "")
        if text:
            out[name or f"textarea{len(out) + 1}"] = text
    return out


def find_record_id(soup) -> str:
    """详情页自身记录的 GUID：优先 `lblID`，其次 URL 里的 id。"""
    for name in ("lblID", "txtID", "lblPID"):
        el = soup.find("input", {"name": name}) or soup.find("input", {"id": name})
        if el is not None and (el.get("value") or "").strip():
            return (el.get("value") or "").strip()
    return ""


def parse_detail(html: str, *, record_id: str = "") -> dict:
    """详情页 → {record_id, fields, grids, texts, self_check}。"""
    soup = soup_of(html)
    actual = find_record_id(soup)
    return {
        "record_id": record_id or actual,
        "page_record_id": actual,
        "self_check": bool(record_id and actual and record_id.lower() == actual.lower()),
        "fields": parse_fields(soup),
        "grids": parse_grids(soup),
        "texts": parse_textareas(soup),
    }


# ══════════════════ 子页入口发现 ══════════════════
@dataclass
class NodeEntry:
    """一个「点开才有」的子页入口。"""

    node: str                       # 稳定键（catalog 里定义的）
    label: str                      # 中文说明
    page: str                       # 子页文件名（不含目录）
    function: str                   # 打开它的 JS 函数
    button: str                     # 要回发的按钮名
    sets: dict[str, object] = field(default_factory=dict)   # 隐藏字段 -> 参数下标 / 常量
    params: list[str] = field(default_factory=list)
    sample_args: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.function and self.button)


def _functions(text: str) -> dict[str, tuple[list[str], str]]:
    out: dict[str, tuple[list[str], str]] = {}
    for m in _FUNC_RE.finditer(text):
        i = m.end()
        depth = 1
        while i < len(text) and depth:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        params = [p.strip() for p in m.group(2).split(",") if p.strip()]
        out[m.group(1)] = (params, text[m.end():i - 1])
    return out


def split_args(raw: str) -> list[str]:
    """把 JS 调用的实参串切成列表（保留引号内的逗号）。"""
    raw = html_mod.unescape(raw or "").strip()
    if not raw:
        return []
    out: list[str] = []
    buf = ""
    quote = ""
    for ch in raw:
        if quote:
            if ch == quote:
                out.append(buf)
                buf, quote = "", ""
            else:
                buf += ch
        elif ch in "'\"":
            quote = ch
        elif ch == ",":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def _real_samples(text: str) -> dict[str, list[str]]:
    """页面里**真实出现**的 Open* 调用（排除函数定义本身）。

    空串是合法实参（例如 `OpenInputBJGXZLData(...,'0','')` 的 ZSJID 就是空串），
    不能因为「有参数为空」就丢掉整次调用；只丢弃全空的。
    """
    samples: dict[str, list[str]] = {}
    for m in _CALL_RE.finditer(text):
        head = text[max(0, m.start() - 20):m.start()]
        if re.search(r"function\s+$", head):
            continue
        args = split_args(m.group(2))
        # 未加引号的实参是表达式（如 `OpenBrowFXMX(PID, CPID, …)`），会带换行/空格，
        # 拿它当值只会把 "PID" 写进隐藏字段 → 一律丢弃。
        if any(re.search(r"\s", arg) for arg in args):
            continue
        if not any(arg.strip() for arg in args):
            continue
        samples.setdefault(m.group(1), args)
    return samples


#: 详情页里「点开产品 / 部件 / 数量档」的调用，是子页参数的兜底来源
_CONTEXT_PATTERNS = {
    "cpid": re.compile(r"OpenInputCPZLData\(\s*'([^']*)'\s*,\s*'([^']*)'"),
    "bjid": re.compile(r"OpenInputBJZLData\(\s*'([^']*)'"),
    "cpslid": re.compile(r"OpenInputCPSLZLData\(\s*'([^']*)'"),
}


def detail_context(html: str, record_id: str = "") -> dict[str, str]:
    """从详情页里取 cpid / bjid / cpslid，供「页面没给实参」的节点兜底。"""
    ctx = {"pid": record_id, "cpid": "", "bjid": "", "cpslid": ""}
    m = _CONTEXT_PATTERNS["cpid"].search(html or "")
    if m:
        ctx["cpid"], ctx["pid"] = m.group(1), (m.group(2) or ctx["pid"])
    m = _CONTEXT_PATTERNS["bjid"].search(html or "")
    if m:
        ctx["bjid"] = m.group(1)
    m = _CONTEXT_PATTERNS["cpslid"].search(html or "")
    if m:
        ctx["cpslid"] = m.group(1)
    return ctx


def fill_missing(payload: dict[str, str], ctx: dict[str, str]) -> dict[str, str]:
    """把 payload 里的空值按字段名语义回填（只在页面没给实参时生效）。

    ERP 有些按钮在只读页里**没有**带实参的 `Open*` 调用（按钮在隐藏工具栏里），
    这时隐藏字段会是空串，打开的子页就是空页面。用详情页里已解析出的
    pid / cpid / bjid / cpslid 按字段名回填，能把这类节点救回来。

    ⚠ 只认**明确**的字段名，宁可留空也不要写错：
    例如 `lblSelectTZCPZLID`（套装产品 ID）和 `lblSelectBJZLID`（在费项页里其实是
    「数量」而不是部件 ID）都不回填 —— 填错会让服务端打开别的记录或直接不弹窗。
    """
    exact = {
        "lblSelectCPZLID": "cpid",
        "lblSelectBJZLID": "bjid",
        "lblSelectCPSLZL": "cpslid",
    }
    out = dict(payload)
    for key, value in payload.items():
        if value:
            continue
        upper = key.upper()
        if upper.endswith("CPZLPID") or upper.endswith("BJZLPID"):
            candidate = ctx.get("pid") or ""
        elif key in exact:
            candidate = ctx.get(exact[key]) or ""
        else:
            continue
        if candidate:
            out[key] = candidate
    return out


def discover_nodes(html: str, catalog: dict[str, tuple[str, str, str]]) -> list[NodeEntry]:
    """按 catalog（node -> (子页, 按钮, 说明)）扫描详情页，返回**这一单真能打开**的入口。

    catalog 只声明「哪个子页 / 用哪个按钮打开」，具体「是哪个 JS 函数、要写哪些
    隐藏字段」全部从页面反推（`$HB("lblX").value = 参数名` / `InputData("lblX", 参数名)`），
    实参取页面里**真实出现**的那次调用，所以参数不会错位。

    ⚠ 光有 `Open*` 函数不算可用：ERP 共用了整套模板，施工单页里仍留着
    `OpenInputQTFXData` / `OpenBrowFXMX` 的函数体，但按钮本身**没有渲染**
    （`btnSelectQTFX` / `btnBrowFXMX` 在 HTML 里根本不存在）→ 必须再校验按钮存在。
    """
    funcs = _functions(html)
    samples = _real_samples(html)
    rendered = {
        el.get("name") for el in soup_of(html).find_all("input") if el.get("name")
    }
    entries: list[NodeEntry] = []
    for node, (page, button, label) in catalog.items():
        if button not in rendered:
            continue
        page_name = page.split("/")[-1]
        hit: NodeEntry | None = None
        for fn, (params, body) in funcs.items():
            if not fn.startswith(("Open", "OPEN")):
                continue
            clicks = _CLICK_RE.findall(body)
            if button not in clicks:
                continue
            sets: dict[str, object] = {}
            for lbl, expr in _SET_RE.findall(body) + _INPUTDATA_RE.findall(body):
                expr = expr.strip()
                if expr in params:
                    sets[lbl] = params.index(expr)
                else:
                    literal = expr.strip().strip("'\"")
                    if literal and literal not in params and not literal.startswith("$"):
                        sets[lbl] = literal
            hit = NodeEntry(
                node=node, label=label, page=page_name, function=fn,
                button=button, sets=sets, params=params,
                sample_args=samples.get(fn, []),
            )
            break
        if hit is not None:
            entries.append(hit)
    return entries


def node_payload(entry: NodeEntry) -> dict[str, str]:
    """把 NodeEntry 的「参数 → 隐藏字段」映射套上真实实参，得到要提交的字段。

    实参里如果出现函数自己的形参名（`OpenX(PID, CPID)` 这种占位调用），
    说明这次调用不是真实数据，按空值处理，交给 `fill_missing` 兜底。
    """
    args = list(entry.sample_args) or []
    if any(arg.strip() in entry.params for arg in args):
        args = []
    payload: dict[str, str] = {}
    for lbl, ref in entry.sets.items():
        if isinstance(ref, int):
            payload[lbl] = args[ref].strip() if ref < len(args) else ""
        else:
            payload[lbl] = str(ref).strip()
    return payload


def popup_url(html: str) -> str:
    """从回发响应里取弹窗子页 URL（showModalDialog 优先，其次 window.open）。"""
    for pattern in (_POPUP_RE, _HREF_RE):
        found = pattern.findall(html or "")
        if found:
            return found[0]
    return ""


def is_label_cell(td) -> bool:
    """导出给测试用：判断某格是否「标签格」。"""
    return _is_label(td.get_text(" ", strip=True))
