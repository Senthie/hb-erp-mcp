"""Markdown 表格渲染（考勤用）。"""

from __future__ import annotations

import re

_NUMERIC_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _ljust(text: str, width: int) -> str:
    """按显示宽度左对齐（中文按 2 列算）。"""
    return text + " " * max(0, width - display_width(text))


def _rjust(text: str, width: int) -> str:
    return " " * max(0, width - display_width(text)) + text


def display_width(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def to_markdown_table(headers: list[str], rows: list[list[str]],
                      *, max_col_width: int = 50) -> str:
    """把表头 + 行数据渲染成对齐的 Markdown 表格。"""
    if not headers:
        return "（无数据）"
    if not rows:
        return "（无数据）"

    n = len(headers)
    cells: list[list[str]] = []
    for row in rows:
        norm = [str(v) if v is not None else "" for v in row][:n]
        norm += [""] * (n - len(norm))
        cells.append([_clip(v, max_col_width) for v in norm])

    widths = []
    for i, header in enumerate(headers):
        w = display_width(_clip(header, max_col_width))
        for row in cells:
            w = max(w, display_width(row[i]))
        widths.append(w)

    lines = [
        "| " + " | ".join(_ljust(_clip(h, max_col_width), widths[i])
                          for i, h in enumerate(headers)) + " |",
        "| " + " | ".join("-" * w for w in widths) + " |",
    ]
    for row in cells:
        parts = []
        for i, value in enumerate(row):
            parts.append(_rjust(value, widths[i]) if _NUMERIC_RE.match(value)
                         else _ljust(value, widths[i]))
        lines.append("| " + " | ".join(parts) + " |")
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    if display_width(text) <= limit:
        return text
    out, width = [], 0
    for ch in text:
        w = 2 if ord(ch) > 0x2E80 else 1
        if width + w > limit - 3:
            break
        out.append(ch)
        width += w
    return "".join(out) + "..."
