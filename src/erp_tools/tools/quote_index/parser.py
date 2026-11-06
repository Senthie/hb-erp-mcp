"""客户报价需求【列表】页（BaoJia/BJ_KHBJXQ/Index.aspx）解析。

页面事实（实测 2026-11）：
  * 结果在 `div#gViewDiv > table#gridView`；
  * 第 1 行是 `tr.FixedTitleRow` 表头（38 列），数据行同样是 38 个 td，列序一一对应；
  * 每行的**主记录 ID** 在 `<tr ondblclick="OnDBClickOpenData('<主ID>','876',3)">` 里，
    `onclick="showMessagePop('<主ID>')"` 也带同一个 ID。
    注意行内还有别的 GUID（产品/客板子表 ID），不要按「第一个 GUID」取，必须认函数名。
  * 分页：`lblCurrentPage / lblPageCount / lblRowsCount`，翻页用 `__EVENTTARGET=btnNext`
    全量回发（本页可用，已实测）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...core.aspnet import soup_of

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
class QuoteListPage:
    headers: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    page: int = 1
    pages: int = 1
    total: int = 0

    @property
    def has_more_pages(self) -> bool:
        return self.pages > self.page


def _text(el) -> str:
    return el.get_text(strip=True) if el is not None else ""


def _clean_header(text: str) -> str:
    """表头里常见「职 位名称」这类脏空格（换行 / \\xa0）。"""
    return _WS_RE.sub("", text or "")


def _unique(name: str, used: dict[str, int]) -> str:
    name = name or "col"
    if name not in used:
        used[name] = 1
        return name
    used[name] += 1
    return f"{name}_{used[name]}"


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


def _to_int(text: str, default: int) -> int:
    text = (text or "").strip()
    return int(text) if text.isdigit() else default


def parse_list_page(html: str) -> QuoteListPage:
    """解析一次列表查询结果。"""
    soup = soup_of(html)

    meta = dict(
        page=_to_int(_text(soup.find(id="lblCurrentPage")), 1),
        pages=_to_int(_text(soup.find(id="lblPageCount")), 1),
        total=_to_int(_text(soup.find(id="lblRowsCount")), 0),
    )

    div = soup.find("div", id=GRID_DIV_ID)
    if div is None:
        return QuoteListPage(**meta)
    table = div.find("table")
    if table is None:
        return QuoteListPage(**meta)

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
                headers = [_unique(_clean_header(_text(c)), used) for c in cells]
            continue

        texts = [_text(c) for c in cells]
        if len(texts) < 4:
            continue
        if any(marker in "".join(texts) for marker in TOTAL_ROW_MARKERS):
            continue
        # 查不到数据时 ERP 会返回一行全空占位（ondblclick 里 ID 为空），必须丢掉
        if not any(t.strip() for t in texts):
            continue

        record = {name: (texts[i] if i < len(texts) else "") for i, name in enumerate(headers)}
        record_id = extract_record_id(tr)
        if record_id:
            record["记录ID"] = record_id
        if not record.get("表单编号"):
            continue
        rows.append(record)

    return QuoteListPage(headers=headers, rows=rows, **meta)
