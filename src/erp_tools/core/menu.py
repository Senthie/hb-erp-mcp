"""ERP 左侧菜单树解析（`GetMenuTree` 返回的 **JS 字面量**，不是 JSON）。

接口：`GET /Admin/indexAjax.ashx?FunName=GetMenuTree&Value1=<LoginUserID>&Value2=`
（必须 GET，POST 返回空体）

返回体长这样 —— 键名不带引号、字符串用单引号、`children` 嵌套：

```js
[{isexpand:'false',text:'业务报价管理',children:[
  {isexpand:'false',text:'业务需求',children:[
    {isexpand:'false',text:'客户报价需求',children:[
      {url:'BaoJia/BJ_KHBJXQ/Add.aspx?QXID=873&MenuTree=1&LoginUserID=2474',
       text:'新增-客户报价需求',ShowText:'新增',ParentName:'客户报价需求',ParentID:'471'},
      ...
```

页面事实（实测 2026-11，账号 2474 / 21146 一致）：

* **菜单是按账号权限渲染的**：换人可能少模块，所以抓下来的树要记 `账号 + 时间`；
* **同一个页面路径会挂在多个菜单下**（`KuCun/KC_CLCRKSQ/Add.aspx` 既是
  「采购申请 1036」又是「部门领料申请 331」）→ QXID 是**菜单项**维度，不能按路径去重；
* 有 `url:''` 的纯分组节点（「补数管理」「系统管理」），保留但标为「无页面」；
* `text` 里会夹 ERP 自己 HTML 换行留下的空格（`业务需 求`、`新增-设备 维修通知单`），
  比对/展示统一用 `MenuItem.label`（只去掉 CJK 之间的空白，英文空格保留）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from .auth import normalize_page_path

_QXID_RE = re.compile(r"[?&]QXID=(\d+)", re.I)
#: 只在两个中日韩字符之间去空白（保留 `FOB 盐田` 这类英文空格）
_CJK_GAP_RE = re.compile(r"(?<=[\u3000-\u9fff\uff00-\uffef])[ \t]+(?=[\u3000-\u9fff\uff00-\uffef])")


@dataclass
class MenuItem:
    """菜单树的一个节点（可能是分组，也可能是页面）。"""

    text: str = ""
    url: str = ""
    show_text: str = ""
    parent_name: str = ""
    parent_id: str = ""
    isexpand: str = "false"
    children: list["MenuItem"] = field(default_factory=list)

    # ---------- 派生属性 ----------
    @property
    def label(self) -> str:
        """去掉 ERP 塞进文案里的换行空格：`业务需 求` → `业务需求`。"""
        return _CJK_GAP_RE.sub("", (self.text or "").strip())

    @property
    def page_path(self) -> str:
        """归一后的页面路径（无页面时为空串）。"""
        return normalize_page_path(self.url) if self.url else ""

    @property
    def qxid(self) -> str:
        m = _QXID_RE.search(self.url or "")
        return m.group(1) if m else ""

    @property
    def is_page(self) -> bool:
        return bool(self.page_path)

    def to_dict(self) -> dict:
        data: dict = {"text": self.text}
        if self.url:
            data["url"] = self.url
        if self.show_text:
            data["show_text"] = self.show_text
        if self.parent_name:
            data["parent_name"] = self.parent_name
        if self.parent_id:
            data["parent_id"] = self.parent_id
        if self.isexpand and self.isexpand != "false":
            data["isexpand"] = self.isexpand
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "MenuItem":
        return cls(
            text=str(data.get("text") or ""),
            url=str(data.get("url") or ""),
            show_text=str(data.get("show_text") or ""),
            parent_name=str(data.get("parent_name") or ""),
            parent_id=str(data.get("parent_id") or ""),
            isexpand=str(data.get("isexpand") or "false"),
            children=[cls.from_dict(c) for c in data.get("children") or []],
        )


# ══════════════════ JS 字面量读取器 ══════════════════
class MenuParseError(ValueError):
    """菜单字面量解析失败。"""


class _Reader:
    """够用就好的 JS 字面量递归下降解析（只认字符串/数字/标识符/数组/对象）。"""

    def __init__(self, text: str) -> None:
        self.s = text or ""
        self.i = 0
        self.n = len(self.s)

    # ---------- 基础 ----------
    def _skip(self) -> None:
        while self.i < self.n and self.s[self.i] in " \t\r\n":
            self.i += 1

    def _fail(self, msg: str) -> None:
        raise MenuParseError(f"{msg}（位置 {self.i}，附近：{self.s[self.i:self.i + 40]!r}）")

    def _expect(self, ch: str) -> None:
        self._skip()
        if self.i >= self.n or self.s[self.i] != ch:
            self._fail(f"期望 {ch!r}")
        self.i += 1

    # ---------- 值 ----------
    def parse_value(self):
        self._skip()
        if self.i >= self.n:
            self._fail("意外结束")
        ch = self.s[self.i]
        if ch == "[":
            return self.parse_array()
        if ch == "{":
            return self.parse_object()
        if ch in "'\"":
            return self.parse_string()
        return self.parse_bare()

    def parse_array(self) -> list:
        self._expect("[")
        out: list = []
        self._skip()
        if self.i < self.n and self.s[self.i] == "]":
            self.i += 1
            return out
        while True:
            out.append(self.parse_value())
            self._skip()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                self._skip()
                if self.i < self.n and self.s[self.i] == "]":   # 容忍尾逗号
                    self.i += 1
                    return out
                continue
            self._expect("]")
            return out

    def parse_object(self) -> dict:
        self._expect("{")
        out: dict = {}
        self._skip()
        if self.i < self.n and self.s[self.i] == "}":
            self.i += 1
            return out
        while True:
            self._skip()
            if self.i >= self.n:
                self._fail("对象没有结束")
            ch = self.s[self.i]
            key = self.parse_string() if ch in "'\"" else self.parse_bare()
            self._expect(":")
            out[str(key)] = self.parse_value()
            self._skip()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
                self._skip()
                if self.i < self.n and self.s[self.i] == "}":
                    self.i += 1
                    return out
                continue
            self._expect("}")
            return out

    def parse_string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        buf: list[str] = []
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == "\\":
                self.i += 1
                if self.i >= self.n:
                    self._fail("字符串以反斜杠结尾")
                esc = self.s[self.i]
                buf.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(esc, esc))
                self.i += 1
                continue
            if ch == quote:
                self.i += 1
                return "".join(buf)
            buf.append(ch)
            self.i += 1
        self._fail("字符串没有结束引号")

    def parse_bare(self) -> str:
        start = self.i
        while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] in "_.-+"):
            self.i += 1
        if start == self.i:
            self._fail("无法识别的记号")
        return self.s[start:self.i]


# ══════════════════ 对外接口 ══════════════════
def parse_menu_tree(text: str) -> list[MenuItem]:
    """把菜单 JS 字面量解析成 `MenuItem` 列表（顶层模块）。"""
    reader = _Reader(text)
    reader._skip()
    if reader.i >= reader.n:
        raise MenuParseError("菜单内容为空")
    data = reader.parse_value()
    if not isinstance(data, list):
        raise MenuParseError("菜单顶层不是数组")
    items = [_to_item(node) for node in data if isinstance(node, dict)]
    items = [item for item in items if item.text]
    if not items:
        raise MenuParseError("菜单里没有任何节点")
    return items


def _to_item(node: dict) -> MenuItem:
    children = node.get("children")
    return MenuItem(
        text=str(node.get("text") or ""),
        url=str(node.get("url") or ""),
        show_text=str(node.get("showText") or node.get("show_text") or ""),
        parent_name=str(node.get("ParentName") or node.get("parent_name") or ""),
        parent_id=str(node.get("ParentID") or node.get("parent_id") or ""),
        isexpand=str(node.get("isexpand") or "false"),
        children=[_to_item(c) for c in children if isinstance(c, dict)] if children else [],
    )


def walk(tree: list[MenuItem],
         path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], MenuItem]]:
    """深度优先遍历：产出 (祖先 label 路径, 节点)。"""
    for item in tree:
        here = path + (item.label,)
        yield here, item
        if item.children:
            yield from walk(item.children, here)


def menu_pages(tree: list[MenuItem]) -> list[tuple[tuple[str, ...], MenuItem]]:
    """只取**有页面**的节点（即真正的菜单叶子）。"""
    return [(path, item) for path, item in walk(tree) if item.is_page]


def duplicate_paths(tree: list[MenuItem]) -> dict[str, list[str]]:
    """同一页面路径挂在多个菜单下 → {路径: [QXID, ...]}（长度 > 1 才算重复）。

    实测 `KuCun/KC_CLCRKSQ/Add.aspx` 既是「采购申请(1036)」又是「部门领料申请(331)」，
    所以 QXID 必须按「菜单项」记，不能按路径记。
    """
    buckets: dict[str, list[str]] = {}
    for _path, item in menu_pages(tree):
        buckets.setdefault(item.page_path, []).append(item.qxid or "")
    return {k: v for k, v in buckets.items() if len(v) > 1}


def qxid_map(tree: list[MenuItem]) -> dict[str, str]:
    """{页面路径: 第一个 QXID}，与 `auth.parse_menu_urls` 口径一致（首个生效）。"""
    mapping: dict[str, str] = {}
    for _path, item in menu_pages(tree):
        if item.qxid:
            mapping.setdefault(item.page_path, item.qxid)
    return mapping


def tree_to_items(data: list[dict] | None) -> list[MenuItem]:
    """从 `docs/menu.tree.json` 里的 `tree` 段还原。"""
    return [MenuItem.from_dict(node) for node in (data or [])]


__all__ = [
    "MenuItem",
    "MenuParseError",
    "parse_menu_tree",
    "walk",
    "menu_pages",
    "duplicate_paths",
    "qxid_map",
    "tree_to_items",
]
