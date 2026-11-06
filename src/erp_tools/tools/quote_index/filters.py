"""查询条件的解析与应用。

页面只提供「两个关键字框 + 若干下拉」的服务端过滤能力，表达力有限；
所以这里把条件分成两层：
  * 服务端：日期区间、`txtKey` + `DropField`（`--key` / `--field`）——用于减少传输量；
  * 客户端：`--where` 的任意多条件——在已取回的记录上过滤，表达力不受页面限制。

`--where` 语法（逗号分隔多个条件）：
    客户名称~ADG          → 包含
    报价状态=需求审批      → 等于
    制单日期>=2026-10-01   → 日期/数值比较
    制单人!=李东员         → 不等于
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ...core.errors import ParseError

_OPS = ("!=", ">=", "<=", "=", "~", ">", "<")
_COND_RE = re.compile(
    r"^\s*(?P<field>[^=~<>!\s][^=~<>!]*?)\s*(?P<op>!=|>=|<=|=|~|>|<)\s*(?P<value>.*)$"
)
_SPLIT_RE = re.compile(r"[,，;；]")
_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: str


def parse_where(expr: str | Iterable[str] | None) -> list[Condition]:
    """把 `--where` 字符串解析成条件列表。"""
    if not expr:
        return []
    if not isinstance(expr, str):
        expr = ",".join(expr)
    conditions: list[Condition] = []
    for part in _SPLIT_RE.split(expr):
        part = part.strip()
        if not part:
            continue
        m = _COND_RE.match(part)
        if m is None:
            raise ParseError(
                f"无法解析条件 {part!r}；支持 字段=值 / 字段~值 / 字段!=值 / 字段>=值")
        op = m.group("op")
        if op not in _OPS:
            raise ParseError(f"不支持的操作符 {op!r}")
        conditions.append(Condition(m.group("field").strip(), op, m.group("value").strip()))
    return conditions


def resolve_field(name: str, headers: list[str]) -> str:
    """把用户写的字段名对齐到实际表头（支持唯一子串匹配）。"""
    if name in headers:
        return name
    hits = [h for h in headers if name and name in h]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ParseError(f"没有列 {name!r}；可用列：{', '.join(headers)}")
    raise ParseError(f"字段 {name!r} 有歧义，匹配到：{', '.join(hits)}")


def apply_conditions(rows: list[dict], conditions: list[Condition],
                     headers: list[str]) -> list[dict]:
    if not conditions:
        return rows
    resolved = [Condition(resolve_field(c.field, headers), c.op, c.value) for c in conditions]
    return [row for row in rows if all(_match(row.get(c.field), c) for c in resolved)]


def _match(raw, cond: Condition) -> bool:
    value = "" if raw is None else str(raw)
    target = cond.value
    op = cond.op

    if op == "~":
        return target.lower() in value.lower()

    left, right = value.strip(), target.strip()
    if _is_number(left) and _is_number(right):
        return _compare(float(left), float(right), op)
    if op == "=":
        return left == right or left.lower() == right.lower()
    if op == "!=":
        return not (left == right or left.lower() == right.lower())
    # 字符串的大小比较按字典序（日期 yyyy-mm-dd 也适用）
    return _compare(left, right, op)


def _compare(left, right, op: str) -> bool:
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == "<":
        return left < right
    return False


def _is_number(text: str) -> bool:
    return bool(_NUM_RE.match(text))
