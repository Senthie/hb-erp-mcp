"""列表页「行内点击事件」→ 可调用的子页动作。

生产施工单列表（`ShengChan/SC_SCSGD/Index.aspx`）每一行上挂着一堆点击事件，
实测（2026-11-23，账号 00469 / UserID 1662）它们最终都是**打开某个子页**：

| 触发 | 页面 | 打开方式 |
| --- | --- | --- |
| 双击行 `OnDBClickOpenData(pid,'740',3)` | `SC_SCSGD/Add.aspx?Option=3` | 见 `erp_production_detail` |
| 电子稿袋 `OpenDirFileList(pid,cpid)` | `SC_SCSGD/WJML_Brow.aspx` | GET |
| 文件跟进情况 `OpenSelectWJQK(cpid)` | `BaoJia/BJ_KHBJXQ/Add_WJQK.aspx` | GET（需 `Modify=1&QXID=1533`） |
| 产品跟进信息 `OpenLinkGJQK(cpid)` | `BaoJia/BJ_KHBJXQ/KHBJXQ_CPGJXX.aspx` | GET |
| 做货指引 `OpenLinkZHZY(cpid)` | `BaoJia/BJ_KHBJXQ/KHBJXQ_CPZHZY.aspx` | GET（页面极大） |
| 专色油墨 `OpenLinkZSYM(pid,cpid)` | `SC_SCSGD/Add_D1_D16.aspx` | GET |
| 临时材料清单 `LinCLQD('',pid,'740')` | `ShengChan/SC_CPCLQD/Add.aspx` | GET（QXID=3029） |
| 打印 `IndexPrint(pid)` | `Report/ReportList.aspx` | GET（QXBH=736） |
| 信息 `showMessagePop(pid)` | 无独立页（面板内 5 个按钮逐个回发） | **不支持** |
| 部件备注 `OpenModifyBJWindow(pid,cpid)` | `SC_SCSGD/Show_BJRemark.aspx` | **ERP 侧 500**（浏览器同样打不开） |
| 核价否 `OpenSelectCPHJ(cpid)` | 回发后不弹窗 | **不支持** |

两类「靠回发才给 URL」的动作（`BtnBJRemark`/`btn_WJQK`）取决于页面当时的选中行上下文，
服务端会用**它自己**的产品上下文（实测忽略 `lblCPID`），所以这里一律走 GET 直链：
`wjqk` 补上 `Modify=1&QXID=1533` 后可以直接打开，`bjremark` 则确认是坏页。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from ...core.aspnet import soup_of
from ...core.errors import ParseError

#: 生产施工单「打印」用的报表组编号（实测 00469 下为 736）
PRINT_REPORT_QXID = "736"
#: 临时材料清单页的 QXID（`LinCLQD(...,'3029','740')` 里的第二个参数）
CLQD_QXID = "3029"
#: 文件跟进情况页的 QXID（回发弹窗里带的固定值）
WJQK_QXID = "1533"


@dataclass(frozen=True)
class RowAction:
    key: str
    label: str
    #: 相对 `/Admin/` 的模板，支持 `{pid}` / `{cpid}` / `{qxid}` / `{uid}` 占位
    template: str
    #: 需要哪些参数
    needs: tuple[str, ...] = ("pid",)
    #: 不可用时的说明（None = 可用）
    note: str = ""

    @property
    def usable(self) -> bool:
        return not self.note

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.key, "label": self.label,
                               "usable": self.usable}
        if self.usable:
            out["page"] = self.template.split("?", 1)[0]
        else:
            out["note"] = self.note
        return out


#: 生产施工单列表行的动作（键 → 定义）
PRODUCTION_ROW_ACTIONS: dict[str, RowAction] = {
    "esign": RowAction(
        "esign", "电子稿袋（文件浏览）",
        "ShengChan/SC_SCSGD/WJML_Brow.aspx"
        "?ID=-1&PID={pid}&CPID={cpid}&QXID={qxid}&LoginUserID={uid}",
        ("pid", "cpid"),
    ),
    "gjqk": RowAction(
        "gjqk", "产品跟进信息",
        "BaoJia/BJ_KHBJXQ/KHBJXQ_CPGJXX.aspx?ID={cpid}&LoginUserID={uid}",
        ("cpid",),
    ),
    "zhzy": RowAction(
        "zhzy", "做货指引",
        "BaoJia/BJ_KHBJXQ/KHBJXQ_CPZHZY.aspx?ID={cpid}&LoginUserID={uid}",
        ("cpid",),
    ),
    "wjqk": RowAction(
        "wjqk", "文件跟进情况",
        "BaoJia/BJ_KHBJXQ/Add_WJQK.aspx"
        f"?PID={{pid}}&CPID={{cpid}}&Modify=1&QXID={WJQK_QXID}&LoginUserID={{uid}}",
        ("pid", "cpid"),
    ),
    "zszm": RowAction(
        "zszm", "专色油墨",
        "ShengChan/SC_SCSGD/Add_D1_D16.aspx?ID={pid}&CPID={cpid}&LoginUserID={uid}",
        ("pid", "cpid"),
    ),
    "clqd": RowAction(
        "clqd", "临时材料清单",
        "ShengChan/SC_CPCLQD/Add.aspx"
        f"?id={{pid}}&QXID={CLQD_QXID}&ReturnUrl=../../ShengChan/sc_scsgd/index.aspx"
        "&ReturnQXID={qxid}&LoginUserID={uid}",
    ),
    "print": RowAction(
        "print", "打印报表列表",
        "Report/ReportList.aspx"
        f"?ID={{pid}}&QXBH={PRINT_REPORT_QXID}&PrinterName={{uid}}&LoginUserID={{uid}}",
    ),
    "info": RowAction(
        "info", "行「信息」", "",
        ("pid",),
        note="这个动作没有独立页面：它在列表页的浮动面板里用 5 个按钮"
             "（默认/审批/其它/RC/讨论）逐个回发，服务端把内容写回面板，无法离线取。",
    ),
    "bjremark": RowAction(
        "bjremark", "部件备注", "",
        ("pid", "cpid"),
        note="ERP 侧该页报 500（未将对象引用设置到对象的实例），浏览器点开同样是错误页。",
    ),
    "cphj": RowAction(
        "cphj", "核价否", "",
        ("cpid",),
        note="回发后不产生弹窗（服务端只把结果写回网格单元格），没有可读子页。",
    ),
}

_ALIASES = {
    "电子稿袋": "esign", "稿袋": "esign", "文件浏览": "esign", "wjml": "esign",
    "跟进信息": "gjqk", "产品跟进": "gjqk", "gjqk": "gjqk",
    "做货指引": "zhzy", "zhzy": "zhzy",
    "文件状态": "wjqk", "文件跟进": "wjqk", "wjqk": "wjqk",
    "专色油墨": "zszm", "油墨": "zszm",
    "材料清单": "clqd", "临时材料": "clqd", "clqd": "clqd", "领料": "clqd",
    "打印": "print", "报表": "print", "print": "print",
    "信息": "info", "消息": "info",
    "部件备注": "bjremark", "备注": "bjremark",
    "核价": "cphj", "核价否": "cphj",
}


def resolve_action(name: str, table: dict[str, RowAction] | None = None) -> str:
    """动作名/中文别名 → 注册键。"""
    table = table or PRODUCTION_ROW_ACTIONS
    raw = (name or "").strip()
    if not raw:
        raise ParseError("action 必填；可用 action=actions 查看这一行能点什么")
    low = raw.lower()
    if low in table:
        return low
    if raw in _ALIASES:
        return _ALIASES[raw]
    if low in _ALIASES:
        return _ALIASES[low]
    raise ParseError(f"未知动作 {raw!r}；可用："
                     + "、".join(f"{k}({v.label})" for k, v in table.items()))


def build_url(action: str, *, pid: str, cpid: str = "", qxid: str = "",
              uid: str = "", table: dict[str, RowAction] | None = None) -> str:
    """拼出子页 URL（相对 `/Admin/`）。参数不够时抛 `ParseError`。"""
    table = table or PRODUCTION_ROW_ACTIONS
    key = resolve_action(action, table)
    spec = table[key]
    if not spec.usable:
        raise ParseError(f"{spec.label} 不可读：{spec.note}")
    for need in spec.needs:
        value = {"pid": pid, "cpid": cpid}[need]
        if not value:
            raise ParseError(f"{spec.label} 需要 {need}，这一行没有该值")
    return "/Admin/" + spec.template.format(pid=pid, cpid=cpid, qxid=qxid, uid=uid)


# ══════════════════ 子页解析 ══════════════════
def parse_popup(html: str, *, base_path: str = "/Admin/ShengChan/SC_SCSGD/",
                max_tables: int = 8, max_rows: int = 200) -> dict[str, Any]:
    """把「弹窗子页」解析成 `{title, fields, tables, links, texts}`。

    这些页面各写各的，没有统一的字段布局，所以给的是**通用结构化视图**：
    每个表格给 `{id, headers, rows, truncated}`，链接给绝对化后的
    `{text, href}`（`javascript:__doPostBack` 之类会被过滤）。
    """
    soup = soup_of(html)
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    tables: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        trs = [tr for tr in table.find_all("tr") if tr.find_all(["td", "th"])]
        if len(trs) < 2:
            continue
        header_cells = trs[0].find_all(["td", "th"])
        headers = [_cell_text(c) for c in header_cells]
        if not any(headers):
            headers = [f"col{i + 1}" for i in range(len(header_cells))]
        body: list[dict[str, str]] = []
        for tr in trs[1:]:
            cells = tr.find_all(["td", "th"])
            values = [_cell_text(c) for c in cells]
            if not any(values):
                continue                 # 空占位行
            row = {headers[i] if i < len(headers) else f"col{i + 1}": v
                   for i, v in enumerate(values)}
            body.append(row)
        if not body:
            continue
        tables.append({
            "id": table.get("id") or "",
            "headers": headers,
            "rows": body[:max_rows],
            "truncated": len(body) > max_rows,
            "count": len(body),
        })
        if len(tables) >= max_tables:
            break

    links: list[dict[str, str]] = []
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        text = a.get_text(strip=True)
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        links.append({"text": text[:60], "href": urljoin("http://erp" + base_path, href)})
    # 去掉 http://erp 前缀，只保留路径（与其它接口一致）
    links = [{"text": l["text"], "href": l["href"][len("http://erp"):]} for l in links]

    texts: dict[str, str] = {}
    for area in soup.find_all("textarea"):
        name = area.get("name") or area.get("id")
        if name:
            texts[name] = area.get_text().strip()

    return {"title": title, "tables": tables, "links": links[:60], "texts": texts}


def _cell_text(cell) -> str:
    text = cell.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


__all__ = [
    "CLQD_QXID",
    "PRINT_REPORT_QXID",
    "PRODUCTION_ROW_ACTIONS",
    "WJQK_QXID",
    "RowAction",
    "build_url",
    "parse_popup",
    "resolve_action",
]
