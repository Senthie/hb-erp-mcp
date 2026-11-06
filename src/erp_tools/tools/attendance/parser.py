"""考勤表格（RS_KQDA_M/Add.aspx → div#gViewDiv2 → table#gridView）解析。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...core.aspnet import soup_of

GRID_DIV_ID = "gViewDiv2"
#: 表头里必有的列名，用来确认抓到的是一张考勤表
HEADER_SENTINEL = "工卡号"
TOTAL_ROW_MARKERS = ("合计",)


@dataclass
class AttendanceTable:
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


_WS_RE = re.compile(r"[\s\u3000\u00a0\u200b\ufeff]+")


def _clean_header(text: str) -> str:
    """表头单元格里常见「职位名 称」「\xa0姓名」这类脏空格，统一抹平。"""
    return _WS_RE.sub("", text or "")


def _unique(name: str, used: dict[str, int]) -> str:
    """处理重名列：实际迟到分钟 / 迟到分钟 在表头里各出现两次。"""
    if name not in used:
        used[name] = 1
        return name
    used[name] += 1
    return f"{name}_{used[name]}"


def _is_header_row(tr) -> bool:
    if tr.find("th") is not None:
        return True
    classes = tr.get("class") or []
    if any("FixedTitleRow" in c for c in classes):
        return True
    return HEADER_SENTINEL in tr.get_text()


def parse_attendance_table(html: str) -> AttendanceTable:
    """把一次查询结果解析成表头 + 行字典。"""
    soup = soup_of(html)

    page = _to_int(_text(soup.find(id="lblCurrentPage")), 1)
    pages = _to_int(_text(soup.find(id="lblPageCount")), 1)
    total = _to_int(_text(soup.find(id="lblRowsCount")), 0)

    div = soup.find("div", id=GRID_DIV_ID)
    if div is None:
        return AttendanceTable(page=page, pages=pages, total=total)

    table = div.find("table")
    if table is None:
        return AttendanceTable(page=page, pages=pages, total=total)

    headers: list[str] = []
    used: dict[str, int] = {}
    rows: list[dict] = []

    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        texts = [("" if c.find("input", {"type": "checkbox"}) else _text(c)) for c in cells]

        if not headers:
            if _is_header_row(tr):
                headers = [_unique(t or f"col{i + 1}", used) for i, t in enumerate(texts)]
                used = {}
            continue

        joined = "".join(texts)
        if any(marker in joined for marker in TOTAL_ROW_MARKERS) and not texts[0]:
            continue
        if not any(t.strip() for t in texts):
            continue

        row = {}
        for i, name in enumerate(headers):
            row[name] = texts[i] if i < len(texts) else ""
        rows.append(row)

    return AttendanceTable(headers=headers, rows=rows, page=page, pages=pages, total=total)


def _to_int(text: str, default: int) -> int:
    text = (text or "").strip()
    return int(text) if text.isdigit() else default
