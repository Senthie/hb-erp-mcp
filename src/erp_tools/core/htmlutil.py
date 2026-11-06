"""HTML / 表单取值工具（从历史脚本中原样上移，行为保持不变）。

这些函数对应 ERP 页面里几个「不讲武德」的地方：
  * 明细区 textarea 没有 name/id，只能靠左边单元格的中文标签定位；
  * 同一个标签在一页里可能出现多次（明细行 / 顶层字段 / 空行），
    要取「第一个能拿到非空 textarea」的那个；
  * 数量字段既有汇总值也有多语言明细（CPSL1..10），不能混用。
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from bs4 import BeautifulSoup

SELECT_PLACEHOLDERS = ("<--请选择-->", "<--請選擇-->", "")

#: 产品明细区里「标签 -> textarea」的字段名（页面里这些 textarea 没有 name 属性）
TEXTAREA_LABELS = ("产品描述", "配件描述", "备注", "修改记录", "检测项目")


def norm_label(text: str | None) -> str:
    """归一化标签文字：去全角空格、首尾空白，并去掉结尾的冒号。"""
    return (text or "").replace("\u3000", " ").strip().rstrip("：:").strip()


def get_input_value(soup: BeautifulSoup, name: str) -> str | None:
    el = soup.find("input", {"name": name})
    if el is None:
        return None
    value = el.get("value")
    return "" if value is None else str(value)


def get_selected_text(soup: BeautifulSoup, name: str) -> str | None:
    sel = soup.find("select", {"name": name})
    if sel is None:
        return None
    chosen = None
    for opt in sel.find_all("option"):
        if opt.has_attr("selected"):
            chosen = opt
            break
    if chosen is None:
        chosen = sel.find("option")
    if chosen is None:
        return None
    text = chosen.get_text(strip=True)
    return "" if text in SELECT_PLACEHOLDERS else text


def get_textarea_value(soup: BeautifulSoup, name: str) -> str | None:
    el = soup.find("textarea", {"name": name})
    if el is None:
        return None
    return el.get_text().strip()


def textarea_near(td) -> str | None:
    """在标签 td 的右邻 td → 自身 → 所在 tr 中依次找 textarea。"""
    candidates = []
    nxt = td.find_next_sibling("td")
    if nxt is not None:
        candidates.append(nxt)
    candidates.append(td)
    tr = td.find_parent("tr")
    if tr is not None:
        candidates.append(tr)
    for el in candidates:
        ta = el.find("textarea")
        if ta is not None:
            return ta.get_text().strip()
    return None


def textarea_label_scopes(soup: BeautifulSoup, extra_ids: Sequence[tuple[str, str]] = ()) -> list:
    """返回「标签 -> textarea」的查找容器，按优先级排列。

    页面存在两种布局（历史事故，勿删）：
      A) 这些行嵌在 td#tdCPZLLIst 内；
      B) td#tdCPZLLIst 存在且有内容，但**不含**这些行，它们在 div#Pan_gridView7 里。
    旧实现把作用域固定为 td#tdCPZLLIst 且不回退（布局 B 下该 td 非空，`or soup`
    永远不会触发），导致整批记录字段为空。
    """
    scopes = []
    ids: list[tuple[str, str]] = [("td", "tdCPZLLIst"), ("div", "Pan_gridView7")]
    ids.extend(extra_ids)
    for tag, el_id in ids:
        el = soup.find(tag, {"id": el_id})
        if el is not None and el not in scopes:
            scopes.append(el)
    scopes.append(soup)
    return scopes


def extract_labeled_textareas(
    soup: BeautifulSoup,
    labels: Iterable[str] = TEXTAREA_LABELS,
    scopes: Sequence | None = None,
) -> dict[str, str | None]:
    """一次性扫描页面，返回 {标签: textarea 内容}。

    找不到对应 textarea 的标签值为 None；找到但内容为空则为 ""。
    """
    labels = tuple(labels)
    targets = {norm_label(lb): lb for lb in labels}
    found: dict[str, str | None] = {lb: None for lb in labels}
    seen: set[int] = set()

    # 先严格匹配标签文字，再宽松匹配（容忍「产品描述（含补充）：」这类修饰）
    for strict in (True, False):
        for scope in (scopes if scopes is not None else textarea_label_scopes(soup)):
            for td in scope.find_all("td"):
                if id(td) in seen:
                    continue
                txt = norm_label(td.get_text())
                if not txt:
                    continue

                label = targets.get(txt)
                if label is None:
                    if strict:
                        continue
                    if "【" in txt:  # 工具栏的【产品描述】链接
                        continue
                    for t, lb in targets.items():
                        if txt.startswith(t) and len(txt) <= len(t) + 12:
                            label = lb
                            break
                    if label is None:
                        continue

                value = textarea_near(td)
                if value is None:
                    continue

                seen.add(id(td))
                if value or found[label] is None:
                    found[label] = value

        if all(found.values()):
            break
    return found


def label_value_map(td, label_map: dict[str, str]) -> dict[str, str]:
    """「标签 td -> 下一个 td 里的值」批量提取（input.value / span / 纯文本）。"""
    out: dict[str, str] = {}
    if td is None:
        return out
    for cell in td.find_all("td"):
        label = cell.get_text(strip=True)
        if label not in label_map:
            continue
        nxt = cell.find_next_sibling("td")
        if nxt is None:
            continue
        inp = nxt.find("input")
        if inp is not None and inp.get("value") is not None:
            out[label_map[label]] = str(inp["value"])
            continue
        span = nxt.find("span")
        if span is not None:
            out[label_map[label]] = span.get_text(strip=True)
            continue
        out[label_map[label]] = nxt.get_text(strip=True)
    return out


_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)?)")


def parse_qty(val) -> int | float | str | None:
    """'4500' / '4500本' / '1,374,800本' → 数字；'.' / '' / None → None。

    解析不出数字时原样返回字符串（不丢数据）。
    """
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if s in ("", "."):
        return None
    m = _NUMBER_RE.match(s)
    if m is None:
        return s
    f = float(m.group(1))
    return int(f) if f.is_integer() else f
