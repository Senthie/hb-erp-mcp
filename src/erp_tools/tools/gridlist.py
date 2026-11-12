"""通用「列表页」解析：gViewDiv > table#gridView 这一类 WebForms 表格。

BaoJia 下多个页面（BJ_KHBJXQ 报价需求、BJ_KHBJ 客户报价 …）是同一套模板生成的：
  * 结果表在 `div#gViewDiv > table#gridView`；
  * 第一行是 `tr.FixedTitleRow` 表头，数据行单元格数与表头一一对应；
  * 每行主记录 ID 在 `ondblclick="OnDBClickOpenData('<ID>','<QXID>',3)"` 里
    （或 `onclick="showMessagePop('<ID>')"`），行内别的 GUID 不是主记录 ID；
  * 末尾可能有一行「合计」，必须丢掉；
  * 查不到数据时返回一行全空占位。

这里只放**与具体页面无关**的解析与表单快照逻辑；页面特有的列语义留在各自 tools 目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..core.aspnet import soup_of

GRID_DIV_ID = "gViewDiv"
TOTAL_ROW_MARKERS = ("合计",)
#: 记录 ID 只从这两个函数里取
ID_PATTERNS = (
    re.compile(r"OnDBClickOpenData\(\s*'([^']+)'"),
    re.compile(r"showMessagePop\(\s*'([^']+)'"),
)
#: 表格最左侧的 3 个操作列（选择 / 信息 / 打印），默认不导出
ACTION_COLUMNS = ("选择", "信息", "打印")

_WS_RE = re.compile(r"[\s\u3000\u00a0\u200b\ufeff]+")


@dataclass
class GridPage:
    headers: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    page: int = 1
    pages: int = 1
    total: int = 0

    @property
    def has_more_pages(self) -> bool:
        return self.pages > self.page


def clean_header(text: str) -> str:
    """表头里常见「职 位名称」这类脏空格（换行 / \\xa0）。"""
    return _WS_RE.sub("", text or "")


def _text(el) -> str:
    return el.get_text(strip=True) if el is not None else ""


def _unique(name: str, used: dict[str, int]) -> str:
    name = name or "col"
    if name not in used:
        used[name] = 1
        return name
    used[name] += 1
    return f"{name}_{used[name]}"


def _to_int(text: str, default: int) -> int:
    text = (text or "").strip()
    return int(text) if text.isdigit() else default


def extract_record_id(tr) -> str | None:
    """从行属性里取主记录 ID（只认 OnDBClickOpenData / showMessagePop）。"""
    for attr in ("ondblclick", "onclick"):
        raw = tr.get(attr) or ""
        for pattern in ID_PATTERNS:
            m = pattern.search(raw)
            if m:
                return m.group(1)
    for el in tr.find_all(True):
        raw = el.get("onclick") or ""
        for pattern in ID_PATTERNS:
            m = pattern.search(raw)
            if m:
                return m.group(1)
    return None


def form_snapshot(html: str) -> dict[str, str]:
    """把页面 form1 里的所有可提交控件状态快照成字典。

    跳过：`gridView$*`（每行勾选框）、image/submit/button（不参与普通提交）、
    未勾选的 checkbox。select 取当前选中项，textarea 取文本
    （漏掉 textarea 会让保存被挡在「【配件描述】不能为空！」）。
    """
    soup = soup_of(html)
    data: dict[str, str] = {}

    for el in soup.find_all("input"):
        name = el.get("name")
        if not name or name.startswith("gridView$"):
            continue
        if el.get("type") in ("image", "submit", "button", "reset", "file"):
            continue
        if el.get("type") == "checkbox" and not el.has_attr("checked"):
            continue
        data[name] = el.get("value") or ""

    for sel in soup.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        data[name] = (opt.get("value") or "") if opt is not None else ""

    for area in soup.find_all("textarea"):
        name = area.get("name")
        if not name:
            continue
        data[name] = area.string or ""

    return data


def field_codes(page_html: str, which: str = "DropField") -> dict[str, str]:
    """取关键字字段下拉的 中文名 -> 代码 映射（如 客户名称 -> KHMC）。"""
    soup = soup_of(page_html)
    sel = soup.find("select", {"name": which})
    if sel is None:
        return {}
    out: dict[str, str] = {}
    for opt in sel.find_all("option"):
        label = opt.get_text(strip=True)
        value = opt.get("value") or ""
        if label and value:
            out.setdefault(label, value)
    return out


def select_choices(page_html: str, control: str) -> list[tuple[str, str]]:
    """返回某个 <select> 的 [(提交值, 显示文字), ...]。"""
    soup = soup_of(page_html)
    sel = soup.find("select", {"name": control}) or soup.find("select", {"id": control})
    if sel is None:
        return []
    return [(o.get("value") or "", o.get_text(strip=True)) for o in sel.find_all("option")]


def submit_button(page_html: str, name: str) -> dict[str, str] | None:
    """取一个提交按钮的 `{type, value}`；页面上没有该按钮时返回 None。

    WebForms 只有把**被点击按钮的 name/value** 一起发回去，`btnXxx_Click` 才会执行。
    `image` 类型用 `.x/.y` 坐标代替 value，`submit` 类型必须发它自己的 value
    （各页面的中文文案不同，如「搜索」，所以只能从页面里读）。
    """
    soup = soup_of(page_html)
    el = soup.find("input", {"name": name}) or soup.find("input", {"id": name})
    if el is None:
        return None
    return {"type": (el.get("type") or "").lower(), "value": el.get("value") or ""}


def button_payload(page_html: str, name: str, *, x: str = "5", y: str = "1") -> dict[str, str]:
    """生成「点击了这个按钮」要带上的提交字段。"""
    button = submit_button(page_html, name)
    if button is None:
        return {}
    if button["type"] == "image":
        return {f"{name}.x": x, f"{name}.y": y}
    return {name: button["value"]}


def resolve_select_value(page_html: str, control: str, wanted: str) -> str:
    """把用户输入（下拉的提交值或显示文字）对齐成提交值。

    找不到时抛 `ParseError`，并列出该下拉的真实选项，避免把错误值默默塞给 ERP
    （ERP 对未知选项通常不报错，只是查不到数据）。
    """
    from ..core.errors import ParseError

    text = (wanted or "").strip()
    if not text:
        return ""
    choices = select_choices(page_html, control)
    for value, label in choices:
        if text == value or text == label:
            return value
    raise ParseError(
        f"{control} 没有选项 {text!r}；可用："
        + ", ".join(f"{label}({value})" for value, label in choices)
    )


def parse_grid_page(
    html: str,
    *,
    div_id: str = GRID_DIV_ID,
    require_columns: Iterable[str] = (),
) -> GridPage:
    """解析一次列表查询结果。

    `require_columns`：这些列没有值的行会被丢弃（用于过滤空占位行）。
    """
    soup = soup_of(html)

    meta = dict(
        page=_to_int(_text(soup.find(id="lblCurrentPage")), 1),
        pages=_to_int(_text(soup.find(id="lblPageCount")), 1),
        total=_to_int(_text(soup.find(id="lblRowsCount")), 0),
    )

    div = soup.find("div", id=div_id)
    if div is None:
        return GridPage(**meta)
    table = div.find("table")
    if table is None:
        return GridPage(**meta)

    required = tuple(require_columns)
    headers: list[str] = []
    used: dict[str, int] = {}
    rows: list[dict] = []

    for tr in table.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            cells = tr.find_all("th", recursive=False)
        if not cells:
            continue

        if not headers:
            classes = tr.get("class") or []
            if "FixedTitleRow" in classes or tr.find("th") is not None:
                headers = [_unique(clean_header(_text(c)), used) for c in cells]
            continue

        texts = [_text(c) for c in cells]
        if len(texts) < 4:
            continue
        if any(marker in "".join(texts) for marker in TOTAL_ROW_MARKERS):
            continue
        # 查不到数据时 ERP 会返回一行全空占位（记录 ID 为空），必须丢掉
        if not any(t.strip() for t in texts):
            continue

        record = {name: (texts[i] if i < len(texts) else "") for i, name in enumerate(headers)}
        record_id = extract_record_id(tr)
        if record_id:
            record["记录ID"] = record_id
        if any(not record.get(col) for col in required):
            continue
        rows.append(record)

    return GridPage(headers=headers, rows=rows, **meta)
