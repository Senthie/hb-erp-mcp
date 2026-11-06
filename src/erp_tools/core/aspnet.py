"""ASP.NET WebForms 表单工具。

ERP 全站是 WebForms：任何 POST 都必须把当前页面的隐藏字段原样带回。
这里集中处理三件事：
  1. 提取 __VIEWSTATE / __VIEWSTATEGENERATOR / __EVENTVALIDATION；
  2. 组装回发体（__EVENTTARGET + ScriptManager1 + __ASYNCPOST）；
  3. 识别并拆解 ScriptManager 的 delta 响应（`1|#||2|...|updatePanel|...`）。
"""

from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup

from .errors import UpstreamError

VIEWSTATE_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION", "__LASTFOCUS")

# delta 响应里可能夹带的一条错误：`15|error|500|消息|`
_DELTA_ERROR_RE = re.compile(r"\|\s*error\s*\|[^|]*\|([^|]*)\|", re.I)


def soup_of(html: str) -> BeautifulSoup:
    """优先 lxml（对大页面更稳更快），不可用时退回 html.parser。"""
    try:
        return BeautifulSoup(html or "", "lxml")
    except Exception:
        return BeautifulSoup(html or "", "html.parser")


def extract_viewstate(html: str) -> dict[str, str]:
    """提取所有隐藏状态字段（页面里没有的就不出现）。"""
    soup = soup_of(html)
    out: dict[str, str] = {}
    for fid in VIEWSTATE_FIELDS:
        el = soup.find("input", {"name": fid}) or soup.find("input", {"id": fid})
        if el is not None:
            value = el.get("value")
            out[fid] = "" if value is None else str(value)
    return out


def build_postback(
    base: dict[str, str],
    viewstate: dict[str, str] | None = None,
    event_target: str = "",
    *,
    async_postback: bool = False,
    update_panel: str = "UpdatePanel1",
    strip_keys: Iterable[str] = (),
) -> dict[str, str]:
    """组装一次完整的 WebForms 回发体。

    event_target 为空 = 由某个按钮触发（此时用 ScriptManager1=UpdatePanel1|<按钮>）。
    """
    body = dict(base)
    if viewstate:
        body.update(viewstate)
    for key in strip_keys:
        body.pop(key, None)

    body["__EVENTTARGET"] = event_target
    body.setdefault("__EVENTARGUMENT", "")
    body.setdefault("__LASTFOCUS", "")
    if event_target:
        body["ScriptManager1"] = f"{update_panel}|{event_target}"
    if async_postback:
        body["__ASYNCPOST"] = "true"
    return body


def delta_update_panel(response_text: str) -> str:
    """从 ScriptManager delta 响应里取出 UpdatePanel 的 HTML 片段。

    delta 形如：`1|#||2|1234|3|...|updatePanel|UpdatePanel1|<html>|hiddenField|__VIEWSTATE|...`
    如果响应本身是完整 HTML（服务端没走异步回发），原样返回。
    """
    text = response_text or ""
    if "<html" in text.lower() or "<!doctype" in text.lower():
        return text

    m = _DELTA_ERROR_RE.search(text)
    if m:
        raise UpstreamError(f"ERP 异步回发报错：{m.group(1).strip()[:200]}")

    start = text.find("|updatePanel|")
    if start == -1:
        return text
    seg_start = text.find("|", start + len("|updatePanel|"))
    if seg_start == -1:
        return text
    seg_start += 1
    end = text.find("|hiddenField|", seg_start)
    if end == -1:
        # 片段可能是最后一段
        end = len(text)
    return text[seg_start:end]


def assert_not_request_validation_error(response_text: str) -> None:
    """识别 ASP.NET 的「潜在危险的 Request.Form 值」——典型原因是把未编码的
    `<r><c></c></r>` 直接当表单值提交（浏览器是先 encodeURIComponent 再提交的）。"""
    if "潜在危险的 Request" in response_text:
        raise UpstreamError(
            "ERP 拒绝了提交（Request.Form 含潜在危险字符），通常是网格数据未二次编码",
            body=response_text[:300],
        )
