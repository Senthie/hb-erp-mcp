"""ERP 通用「附件」能力的读写抽象（各模块复用同一套协议）。

## 结论：附件页是同一套模板，只有「入口」和「删除方式」有差别

实测（2026-11-23，账号 15627）四个模块：

| 模块 | 附件页（`*_FJ.aspx`） | 入口 | 上传页 | 删除 |
| --- | --- | --- | --- | --- |
| 客户报价需求 | `BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx` | 详情页 `<a id=btnFJ>` | 同模板 | `Modify=1` 行内 `Delete$N` |
| 客户报价 / 产品核价 | `BaoJia/BJ_KHBJ/BJ_KHBJ_FJ.aspx` | 详情页 `<a id=btnFJ>` | 同模板 | checkbox + `Btn_DeleteFJ` |
| 生产施工单 | `ShengChan/SC_SCSGD/SC_SCSGD_FJ.aspx` | 详情页 `<a id=btnFJ>` | 同模板（多 `Btn_OtherMessage`） | checkbox + `Btn_DeleteFJ` |
| 采购订单 | `CaiGou/CG_CGDD/CG_CGDD_FJ.aspx` | 详情页 `<a id=btnFJ>` | 同模板 | checkbox + `Btn_DeleteFJ` |
| 客户订单 | `DingDan/DD_KHDD/DD_KHDD_FJ.aspx` | **列表页**单号单元格 `OnOpenFJ(FID,PID,N)` | 同模板 | checkbox + `Btn_DeleteFJ` |

**上传页完全一致**：

```
GET  /Admin/<模块>/<X>_FJ.aspx?PID=<记录ID>&Modify=0&LoginUserID=<uid>
     → form1（multipart/form-data）：
        txtPXH(排序号) txtFJMC(附件名称) txtFJMS(附件描述) txtRemark(备注)
        lblPID(记录ID) lblID(已有附件行ID) btnPostFile(提交) FileUploadExcel(文件)
POST 同一个 action（multipart + __VIEWSTATE/__EVENTVALIDATION）：
        txtPXH=1 & txtFJMC=名称 & btnPostFile= & FileUploadExcel=<文件名,字节>
     → 200，列表多一行；单据页的 `[附件(无)]` 变成 `[附件(有)]`
```

**入口只有两种**：详情页锚点 `<a id="btnFJ">` → `__EVENTTARGET=btnFJ`；
或列表页单元格 `OnOpenFJ('文件ID','产品ID','FJNum')` → 回发 `Btn_FJ`。
本模块的 `discover_entry()` / `resolve_page()` 会自动识别并拿到附件页路径，
所以上层只需要「模块 + 记录 ID」。

**删除也有两种**（自动降级）：`Modify=1` 有行内 `Delete$<行号>` 就点它，
否则勾选该行 checkbox 再回发 `Btn_DeleteFJ`。

文件本体在静态目录 `/Admin/FuJian/<模块>/Picture/<guid>.<ext>`；
⚠ 不同模块落在不同端口（客户订单 :8887、报价需求 :8889），
下载时会在 `cfg.host` 与 `cfg.captcha_host` 之间回退。
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.errors import ErpError, ParseError, UpstreamError
from ..core.htmlutil import get_input_value
from .gridlist import button_payload, form_snapshot

#: 单个附件上限（可用 ERP_ATTACH_MAX_MB 覆盖）
DEFAULT_MAX_MB = 20

#: 附件页里「附件文件：有」的点击入口 `Display_FJ('附件ID','PID')`
_DISPLAY_RE = re.compile(r"Display_FJ\(\s*'([^']+)'\s*,\s*'([^']*)'\s*\)")
_FJMC_RE = re.compile(r"OpenSelFieldValue\(\s*'附件名称'\s*,\s*'FJMC'\s*,\s*'([^']*)'")
_POPUP_RE = re.compile(r"""(?:window\.open|showModalDialog)\(\s*["']([^"']+)["']""")


def max_bytes() -> int:
    raw = (os.getenv("ERP_ATTACH_MAX_MB") or "").strip()
    try:
        mb = float(raw) if raw else DEFAULT_MAX_MB
    except ValueError:
        mb = DEFAULT_MAX_MB
    return int(max(mb, 0.1) * 1024 * 1024)


#: 行内删除：`__doPostBack('gridView1','Delete$<行号>')`（只有 Modify=1 的页面才有该列）
_DELETE_INDEX_RE = re.compile(r"Delete\$(\d+)")

#: 列表页/详情页里「附件」入口（两种写法）
_ANCHOR_FJ_RE = re.compile(r"""<a[^>]*id=["']btnFJ["']""", re.I)
_CELL_FJ_RE = re.compile(
    r"OnOpen(?P<cp>CP)?FJ\(\s*'(?P<fid>[^']*)'\s*,\s*'(?P<pid>[^']*)'"
    r"\s*(?:,\s*'(?P<num>[^']*)'\s*)?\)"
)


# ══════════════════ 模块注册表 ══════════════════
@dataclass(frozen=True)
class AttachmentModule:
    """一个「有附件功能」的模块。

    `page_path` 是实测出来的附件页（相对 `/Admin/`）；留空时由 `resolve_page()`
    从详情页/列表页的入口自动发现。
    """

    key: str
    label: str
    #: 详情页（`anchor` 型入口用）；相对 /Admin/
    detail_path: str = ""
    detail_qxid: str = ""
    #: 列表页（`cell` 型入口用，如客户订单）
    list_path: str = ""
    list_qxid: str = ""
    #: 入口类型：anchor（详情页 <a id=btnFJ>）/ cell（列表页单号单元格 OnOpenFJ）
    entry: str = "anchor"
    #: 已实测的附件页；空=自动发现
    page_path: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.page_path or self.detail_path or self.list_path)

    def as_dict(self) -> dict[str, str]:
        return {
            "module": self.key, "label": self.label, "entry": self.entry,
            "detail_page": f"/Admin/{self.detail_path}" if self.detail_path else "",
            "list_page": f"/Admin/{self.list_path}" if self.list_path else "",
            "attachment_page": f"/Admin/{self.page_path}" if self.page_path else "",
        }


#: 实测清单（2026-11-23，账号 15627）。新增模块只要在这里加一行。
MODULES: dict[str, AttachmentModule] = {
    m.key: m for m in (
        AttachmentModule(
            key="quote_request", label="客户报价需求",
            detail_path="BaoJia/BJ_KHBJXQ/Add.aspx", detail_qxid="876",
            page_path="BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx"),
        AttachmentModule(
            key="price", label="客户报价 / 产品核价",
            detail_path="BaoJia/BJ_KHBJ/Add.aspx", detail_qxid="857",
            page_path="BaoJia/BJ_KHBJ/BJ_KHBJ_FJ.aspx"),
        AttachmentModule(
            key="production", label="生产施工单",
            detail_path="ShengChan/SC_SCSGD/Add.aspx", detail_qxid="740",
            page_path="ShengChan/SC_SCSGD/SC_SCSGD_FJ.aspx"),
        AttachmentModule(
            key="purchase", label="采购订单",
            detail_path="CaiGou/CG_CGDD/Add.aspx", detail_qxid="266",
            page_path="CaiGou/CG_CGDD/CG_CGDD_FJ.aspx"),
        AttachmentModule(
            key="sales_order", label="客户订单",
            list_path="DingDan/DD_KHDD/Index.aspx", list_qxid="687",
            entry="cell",
            page_path="DingDan/DD_KHDD/DD_KHDD_FJ.aspx"),
    )
}

_ALIASES = {
    "报价需求": "quote_request", "客户报价需求": "quote_request", "bjxq": "quote_request",
    "报价": "price", "客户报价": "price", "核价": "price", "产品核价": "price",
    "khbj": "price", "cphj": "price",
    "施工单": "production", "生产施工单": "production", "sgd": "production",
    "采购": "purchase", "采购订单": "purchase", "cgdd": "purchase",
    "订单": "sales_order", "客户订单": "sales_order", "khdd": "sales_order",
}


def get_module(key: str) -> AttachmentModule:
    """模块名/中文别名 -> `AttachmentModule`。"""
    name = (key or "").strip()
    low = name.lower()
    name = _ALIASES.get(name, _ALIASES.get(low, low))
    if name not in MODULES:
        raise ParseError(f"未知附件模块 {key!r}；可用：{', '.join(MODULES)}")
    return MODULES[name]


def module_names() -> list[str]:
    return list(MODULES)


#: 附件页路径缓存（自动发现一次即可）
_page_cache: dict[str, str] = {}


@dataclass
class Attachment:
    """一个待上传的附件。`content` 是原始字节（AI 给的 stream 解出来的）。"""

    filename: str
    content: bytes
    content_type: str = ""
    #: ERP 里的「附件名称」，默认用 filename
    name: str = ""
    desc: str = ""
    remark: str = ""

    @property
    def erp_name(self) -> str:
        return (self.name or self.filename).strip()

    def as_dict(self) -> dict[str, Any]:
        return {"filename": self.filename, "size": len(self.content),
                "content_type": self.content_type, "name": self.erp_name}


def _guess_type(filename: str) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def decode_attachment(item: dict[str, Any] | Attachment, *,
                      base_dir: str | Path | None = None) -> Attachment:
    """把「AI/CLI 传来的一个附件描述」解成 `Attachment`。

    支持这些写法（任选一种内容来源）：

    | 来源 | 写法 |
    | --- | --- |
    | base64 | `{"filename": "a.pdf", "content_base64": "JVBERi0..."}` |
    | base64（别名） | `{"filename": "a.pdf", "content": "..."}` / `{"base64": "..."}` |
    | data URI | `{"filename": "a.pdf", "data_uri": "data:application/pdf;base64,..."}` |
    | 纯文本 | `{"filename": "a.txt", "content_text": "hello"}` / `{"text": "..."}` |
    | 本机文件 | `{"path": "out/a.pdf"}`（CLI 常用；`filename` 可覆盖名字） |
    """
    if isinstance(item, Attachment):
        return item
    if not isinstance(item, dict):
        raise ParseError("attachments 每项必须是对象，如 {filename, content_base64}")

    filename = str(item.get("filename") or item.get("file_name")
                   or item.get("file") or "").strip()
    raw: bytes | None = None

    data_uri = str(item.get("data_uri") or item.get("data_url") or "").strip()
    b64 = str(item.get("content_base64") or item.get("base64")
              or (item.get("content") if isinstance(item.get("content"), str) else "")
              or "").strip()
    text = item.get("content_text")
    if text is None:
        text = item.get("text")

    path_value = str(item.get("path") or "").strip()

    if data_uri:
        head, _, payload = data_uri.partition(",")
        b64 = payload
        if ";" in head and not item.get("content_type"):
            item = {**item, "content_type": head.split(":", 1)[-1].split(";")[0]}
    if b64:
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", b64), validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ParseError(f"附件 {filename or '(未命名)'} 的 base64 解码失败：{exc}") from exc
    elif text is not None:
        raw = str(text).encode("utf-8")
        if not filename:
            filename = "attachment.txt"
    elif path_value:
        src = Path(path_value)
        if not src.is_absolute() and base_dir:
            src = Path(base_dir) / src
        if not src.is_file():
            raise ParseError(f"附件文件不存在：{src}")
        raw = src.read_bytes()
        filename = filename or src.name
    else:
        raise ParseError(
            "附件缺少内容：请提供 content_base64 / content_text / data_uri / path 之一"
        )

    if not filename:
        filename = "attachment.bin"
    limit = max_bytes()
    if len(raw) > limit:
        raise ParseError(f"附件 {filename} 有 {len(raw)} 字节，超过上限 {limit} 字节"
                         f"（可用 ERP_ATTACH_MAX_MB 调整）")
    if not raw:
        raise ParseError(f"附件 {filename} 是空文件")

    return Attachment(
        filename=filename,
        content=raw,
        content_type=str(item.get("content_type") or _guess_type(filename)),
        name=str(item.get("name") or "").strip(),
        desc=str(item.get("desc") or item.get("description") or "").strip(),
        remark=str(item.get("remark") or "").strip(),
    )


def decode_attachments(items: Any, *, base_dir: str | Path | None = None) -> list[Attachment]:
    """批量解码；`items` 可以是 list / 单个 dict / `"a.pdf,b.pdf"`（CLI 路径串）。"""
    if not items:
        return []
    if isinstance(items, Attachment):
        return [items]
    if isinstance(items, dict):
        return [decode_attachment(items, base_dir=base_dir)]
    if isinstance(items, str):
        out = []
        for raw in items.replace("；", ",").replace(";", ",").split(","):
            piece = raw.strip().strip('"').strip("'")
            if piece:
                out.append(decode_attachment({"path": piece}, base_dir=base_dir))
        return out
    return [decode_attachment(item, base_dir=base_dir) for item in items]


@dataclass
class AttachmentPageService:
    """`*_FJ.aspx` 附件页：列表 / 上传 / 取文件直链 / 删除。

    `page_path` 形如 `BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx`（相对 `/Admin/`）。
    不传 `page_path` 时用 `module=` 自动发现（见 `discover_page`）。
    """

    auth: Any
    page_path: str = ""
    #: 返回给调用方的标签（用于日志/错误信息）
    label: str = "附件"
    #: 模块描述（给 module 入口用）
    module: AttachmentModule | None = None

    @classmethod
    def for_module(cls, auth: Any, key: str) -> "AttachmentPageService":
        module = get_module(key)
        return cls(auth, page_path=_page_cache.get(module.key, module.page_path),
                   label=f"{module.label}附件", module=module)

    # ══════════════════ 入口发现 ══════════════════
    @staticmethod
    def discover_entry(html: str) -> dict[str, str]:
        """在详情页/列表页 HTML 里找附件入口。

        返回 `{"kind": "anchor"}` 或
        `{"kind": "cell", "文件ID":…, "产品ID":…, "FJNum":…}`；找不到返回 `{}`。
        """
        if _ANCHOR_FJ_RE.search(html):
            return {"kind": "anchor"}
        m = _CELL_FJ_RE.search(html)
        if m:
            return {"kind": "cell", "文件ID": m.group("fid"),
                    "产品ID": m.group("pid"), "FJNum": m.group("num") or ""}
        return {}

    def _popup_page_path(self, response_text: str) -> str:
        """从回发响应里取出附件页路径（去掉 host 与查询串）。"""
        for url in _POPUP_RE.findall(response_text):
            if "_fj.aspx" not in url.lower():
                continue
            path = re.sub(r"^https?://[^/]+", "", url.split("?")[0])
            return path.lstrip("/").replace("Admin/", "", 1)
        raise ParseError(
            f"{self.label}：回发后没拿到附件页地址（页面可能不允许查看附件，"
            "或该单据还没保存）"
        )

    def discover_page(self, record_id: str) -> str:
        """自动发现附件页路径：点一下入口，从弹窗地址里取。"""
        if self.module is None:
            raise ParseError("没有指定 page_path，也没有 module 可用来发现附件页")
        module = self.module
        from ..core.aspnet import soup_of

        if module.entry == "cell":
            if not module.list_path:
                raise ParseError(f"{module.label} 的附件入口在列表页，但注册表没配 list_path")
            uid = getattr(self.auth, "user_id", "") or ""
            list_url = f"/Admin/{module.list_path}?QXID={module.list_qxid}"
            resp = self.auth.client.get(list_url)
            entry = self.discover_entry(resp.text)
            if not entry:
                raise ParseError(f"{module.label}：列表页里没有找到附件入口")
            body = form_snapshot(resp.text)
            body.update({"lblID": entry["文件ID"], "lblPID": entry["产品ID"],
                         "lblFJNum": entry["FJNum"]})
            body.update(button_payload(resp.text, "Btn_FJ"))
            body["__EVENTTARGET"] = ""
            body["__EVENTARGUMENT"] = ""
            text = self.auth.client.post(list_url, data=body).text
        else:
            uid = getattr(self.auth, "user_id", "") or ""
            detail_url = (f"/Admin/{module.detail_path}?QXID={module.detail_qxid}"
                          f"&id={record_id}&Option=3")
            resp = self.auth.client.get(detail_url)
            if not self.discover_entry(resp.text):
                raise ParseError(f"{module.label}：详情页里没有「附件」入口")
            body = form_snapshot(resp.text)
            body["__EVENTTARGET"] = "btnFJ"
            body["__EVENTARGUMENT"] = ""
            text = self.auth.client.post(detail_url, data=body).text
        return self._popup_page_path(text)

    # ══════════════════ 地址 / 打开 ══════════════════
    def resolve_page_path(self, record_id: str) -> str:
        if self.page_path:
            return self.page_path
        if self.module is not None and self.module.key in _page_cache:
            self.page_path = _page_cache[self.module.key]
            return self.page_path
        path = self.discover_page(record_id)
        if self.module is not None:
            _page_cache[self.module.key] = path
        self.page_path = path
        return path

    def page_url(self, record_id: str, *, modify: str = "0") -> str:
        path = self.resolve_page_path(record_id)
        uid = getattr(self.auth, "user_id", "") or ""
        url = f"/Admin/{path}?PID={record_id}&Modify={modify}"
        return f"{url}&LoginUserID={uid}" if uid else url

    def _open(self, record_id: str, *, modify: str = "0") -> tuple[str, str, str]:
        """返回 (html, 页面 url, form action)。"""
        record_id = (record_id or "").strip()
        if not record_id:
            raise ParseError(f"{self.label}：缺少 record_id（单据必须先保存）")
        url = self.page_url(record_id, modify=modify)
        resp = self.auth.client.get(url)
        if resp.status_code >= 500:
            raise UpstreamError(f"{self.label}页打开失败（HTTP {resp.status_code}）",
                                resp.status_code, resp.text[:300])
        if resp.status_code == 404:
            raise ParseError(
                f"{self.label}页不存在：/Admin/{self.page_path}（可能模块没配 page_path）")
        if "__VIEWSTATE" not in resp.text:
            raise ParseError(f"{self.label}页没有 __VIEWSTATE，可能会话过期或权限不足")
        from ..core.aspnet import soup_of

        form = soup_of(resp.text).find("form")
        action = (form.get("action") if form is not None else "") or ""
        return resp.text, url, action

    # ══════════════════ 读 ══════════════════
    def list_files(self, record_id: str, *, with_urls: bool = False) -> dict[str, Any]:
        """列出单据上的附件（附件名称/描述/备注/排序号）。"""
        html, _url, _action = self._open(record_id)
        files = self._parse_rows(html)
        if with_urls:
            for item in files:
                try:
                    item["url"] = self.file_url(record_id, item["文件ID"], html=html)
                except ErpError as exc:
                    item["url"] = ""
                    item["note"] = str(exc)
        return {"record_id": record_id, "count": len(files), "files": files}

    @staticmethod
    def _parse_rows(html: str) -> list[dict[str, str]]:
        """解析附件列表。按**表头列位置**取值（空单元格不能再按"非空过滤"对齐）。"""
        from ..core.aspnet import soup_of

        soup = soup_of(html)
        headers: list[str] = []
        table = None
        fallback = None
        for tr in soup.find_all("tr"):
            ths = [th.get_text(strip=True) for th in tr.find_all("th")]
            if any("附件名称" in h for h in ths):
                parent = tr.find_parent("table")
                if parent is None:
                    continue
                fallback = parent
                # 同一份列表会在外层包装表里再出现一次，认 id 带 grid 的那张
                if "grid" in (parent.get("id") or "").lower():
                    headers, table = ths, parent
                    break
        if table is None:
            table = fallback
            if table is not None:
                headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if table is None:
            return []
        index = {name: i for i, name in enumerate(headers)}

        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for tr in table.find_all("tr"):
            if tr.find_parent("table") is not table:   # 跳过嵌套表/包装行的行
                continue
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            fid = ""
            for td in tds:
                m = _DISPLAY_RE.search(td.get("onclick") or "")
                if m:
                    fid = m.group(1)
                    break
            if not fid or fid.lower() in seen:      # 没有附件 / 隐藏表重复行
                continue
            seen.add(fid.lower())

            def cell(name: str) -> str:
                """按列名取值。

                行首的「选择」复选框单元格并非每行都有（老数据行会少/多一格），
                所以按**右对齐**换算位置：最后一格永远是备注列。
                """
                pos = index.get(name)
                if pos is None:
                    return ""
                target = len(tds) - (len(headers) - pos)
                if not 0 <= target < len(tds):
                    return ""
                return tds[target].get_text(strip=True)

            name = cell("附件名称")
            if not name:
                for td in tds:
                    m = _FJMC_RE.search(td.get("onclick") or "")
                    if m:
                        name = m.group(1).strip()
                        break
            row_index = ""
            checkbox = ""
            for el in tr.find_all("input"):
                m = _DELETE_INDEX_RE.search(el.get("onclick") or "")
                if m and not row_index:
                    row_index = m.group(1)
                if el.get("type") == "checkbox" and (el.get("name") or "").startswith("gridView"):
                    checkbox = el.get("name") or ""
            rows.append({
                "文件ID": fid,
                "名称": name,
                "排序号": cell("排序号"),
                "描述": cell("附件描述"),
                "备注": cell("备注"),
                "有文件": cell("附件文件"),
                "行号": row_index,
                "复选框": checkbox,
            })
        return rows

    # ══════════════════ 删 ══════════════════
    def _pick(self, rows: list[dict[str, str]], *, name: str = "",
              file_id: str = "") -> dict[str, str]:
        files = "、".join(r["名称"] for r in rows) or "（空）"
        target = None
        for row in rows:
            if file_id and row["文件ID"].lower() == file_id.strip().lower():
                target = row
                break
        if target is None and name:
            for row in rows:
                if row["名称"].strip() == name.strip():
                    target = row
                    break
        if target is None and name:
            for row in rows:                      # 名字记不全时允许包含匹配
                if name.strip() in row["名称"]:
                    target = row
                    break
        if target is None:
            raise ParseError(f"没找到附件 {name or file_id!r}；现有：{files}")
        return target

    def delete_file(self, record_id: str, *, name: str = "", file_id: str = "",
                    confirm: bool = False, strategy: str = "auto") -> dict[str, Any]:
        """删除一个附件。`confirm=False` 只返回「将会删除哪一条」，不写库。

        ERP 有两套删除入口（实测 2026-11-23），`strategy="auto"` 会自动选：

        - `row`（行内）：`Modify=1` 页有 `Delete$<行号>` 图标
          → `__EVENTTARGET=gridView1` + `__EVENTARGUMENT=Delete$N`
          （客户报价需求的附件页是这个）
        - `button`（勾选 + 按钮）：没有行内图标，但每行有 checkbox
          → 勾上该行 `gridView1$ctlXX$N` 再回发 `Btn_DeleteFJ`
          （客户报价 / 生产施工单 / 采购订单 / 客户订单 是这个）
        """
        html, url, _action = self._open(record_id, modify="1")
        rows = self._parse_rows(html)
        if not rows:
            raise ParseError(f"{self.label}：单据上没有附件")
        target = self._pick(rows, name=name, file_id=file_id)

        if strategy == "row" or (strategy == "auto" and target.get("行号")):
            how = "row" if target.get("行号") else ""
        elif strategy in ("auto", "button"):
            how = "button" if target.get("复选框") else ""
        else:
            how = ""
        if not how:
            raise ParseError(
                "当前页面没有删除入口（Modify=1 下既没渲染 Delete 索引也没复选框）；"
                "可能是账号无权限，或该模块的附件页不支持删除"
            )
        from ..core.aspnet import soup_of

        if not confirm:
            return {
                "ok": False, "status": "needs_confirmation", "record_id": record_id,
                "would_delete": {k: target[k] for k in ("名称", "文件ID", "排序号")},
                "count": len(rows),
                "strategy": how,
                "message": "确认删除请传 confirm=true",
            }

        body = form_snapshot(html)
        if how == "row":
            body["__EVENTTARGET"] = "gridView1"
            body["__EVENTARGUMENT"] = f"Delete${target['行号']}"
        else:
            body[target["复选框"]] = "on"
            body.update(button_payload(html, "Btn_DeleteFJ"))
            body["__EVENTTARGET"] = ""
            body["__EVENTARGUMENT"] = ""
        resp = self.auth.client.post(url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(f"{self.label}删除失败（HTTP {resp.status_code}）",
                                resp.status_code, resp.text[:300])
        if not soup_of(resp.text).find("form"):
            raise ParseError(f"{self.label}删除后返回的不是附件页，协议可能变了")

        after = self._parse_rows(self._open(record_id)[0])
        gone = all(r["文件ID"].lower() != target["文件ID"].lower() for r in after)
        return {
            "ok": gone,
            "status": "deleted" if gone else "failed",
            "record_id": record_id,
            "deleted": {k: target[k] for k in ("名称", "文件ID", "排序号")},
            "count": len(after),
            "remaining": [r["名称"] for r in after],
            "strategy": how,
            "message": "" if gone else "回读里还能看到这条附件，删除可能没有生效",
        }

    def delete_files(self, record_id: str, names: list[str], *,
                     confirm: bool = False) -> dict[str, Any]:
        """批量删除（ERP 一次只删一行，所以逐个删并每次重新打开取行号）。"""
        results = []
        for name in names:
            try:
                results.append(self.delete_file(record_id, name=name, confirm=confirm))
            except ErpError as exc:
                results.append({"ok": False, "status": "error", "name": name,
                                "message": str(exc)})
        ok = all(item.get("ok") for item in results) if confirm else True
        return {"ok": ok, "record_id": record_id, "results": results,
                "count": len(results)}

    # ══════════════════ 写 ══════════════════
    def _action_url(self, action: str) -> str:
        """把 `<form action="./X_FJ.aspx?...">` 解析成 `/Admin/<模块>/X_FJ.aspx?...`。"""
        if action.lower().startswith(("http://", "https://")):
            return action
        if action.startswith("./"):
            folder = self.page_path.rsplit("/", 1)[0]
            return f"/Admin/{folder}/{action[2:]}"
        return action

    def upload(self, record_id: str, items: list[Attachment] | list[dict[str, Any]],
               *, verify: bool = True) -> dict[str, Any]:
        """上传 1..N 个附件，返回已上传/失败清单（有回读核对）。"""
        decoded = decode_attachments(items)
        if not decoded:
            raise ParseError("没有要上传的附件")

        uploaded: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        html, _url, action = self._open(record_id)
        existing = self._parse_rows(html)
        next_no = len(existing) + 1

        for offset, att in enumerate(decoded):
            try:
                html = self._post_one(record_id, att, action=action,
                                      order=next_no + offset)
            except ErpError as exc:
                errors.append({"filename": att.filename, "error": str(exc)})
                continue
            uploaded.append(att.as_dict())

        result: dict[str, Any] = {
            "ok": not errors,
            "record_id": record_id,
            "uploaded": uploaded,
            "errors": errors,
            "verified": False,
            "missing": [],
        }
        if verify and uploaded:
            names = {row["名称"] for row in self._parse_rows(
                self._open(record_id)[0])}
            missing = [item["name"] for item in uploaded if item["name"] not in names]
            result["verified"] = not missing
            result["missing"] = missing
            result["count"] = len(names)
            if missing:
                result["ok"] = False
                result["message"] = (
                    "回读没有找到这些附件：" + "、".join(missing)
                    + "；请到 ERP 里确认（可能被服务端过滤）"
                )
        return result

    def _post_one(self, record_id: str, att: Attachment, *, action: str,
                  order: int) -> str:
        html, _url, _action = self._open(record_id)
        body = form_snapshot(html)
        # 只保留附件页自己的字段，避免把整页无关控件也带上
        body.update({
            "txtPXH": str(order),
            "txtFJMC": att.erp_name,
            "txtFJMS": att.desc,
            "txtRemark": att.remark,
            "lblPID": record_id,
        })
        body.pop("FileUploadExcel", None)
        body.update(button_payload(html, "btnPostFile"))
        body["__EVENTTARGET"] = ""
        body["__EVENTARGUMENT"] = ""
        target = self._action_url(action or self.page_url(record_id))
        files = {"FileUploadExcel": (att.filename, att.content,
                                    att.content_type or _guess_type(att.filename))}
        resp = self.auth.client.post(target, data=body, files=files)
        if resp.status_code >= 500:
            raise UpstreamError(
                f"{self.label}上传失败（HTTP {resp.status_code}）",
                resp.status_code, resp.text[:300])
        if "__VIEWSTATE" not in resp.text:
            raise ParseError(f"{self.label}上传后返回的不是附件页，协议可能变了")

        # 核对这一行是否落库：有的分支 POST 响应里还是旧列表，所以要再 GET 一次
        names = {row["名称"] for row in self._parse_rows(resp.text)}
        if att.erp_name not in names:
            try:
                names = {row["名称"] for row in self._parse_rows(self._open(record_id)[0])}
            except ErpError:
                names = set()
        if att.erp_name not in names:
            hint = ""
            m = re.search(r"alert\(\s*['\"]([^'\"]{0,120})", resp.text)
            if m:
                hint = f"（ERP 提示：{m.group(1)}）"
            raise ParseError(f"附件 {att.filename} 上传后没有出现在列表里{hint}")
        return resp.text

    # ══════════════════ 下载 ══════════════════
    def file_url(self, record_id: str, file_id: str, *, html: str | None = None) -> str:
        """点「附件文件：有」(`Display_FJ` → `btn_Display_FJ`) 拿静态直链。"""
        html, url, _action = (html, self.page_url(record_id), "") if html else self._open(record_id)
        body = form_snapshot(html)
        body.update({"lblID": file_id, "lblPID": record_id})
        body.update(button_payload(html, "btn_Display_FJ"))
        body["__EVENTTARGET"] = ""
        body["__EVENTARGUMENT"] = ""
        resp = self.auth.client.post(url, data=body)
        for candidate in _POPUP_RE.findall(resp.text):
            if "/FuJian/" in candidate:
                return candidate
        raise ParseError(f"附件 {file_id} 没有拿到下载地址")

    @staticmethod
    def candidate_urls(url: str, cfg: Any) -> list[str]:
        """同一路径在 `cfg.host` 与 `cfg.captcha_host` 之间回退（不同模块落不同端口）。"""
        out = [url]
        path = re.sub(r"^https?://[^/]+", "", url)
        for host in (getattr(cfg, "host", ""), getattr(cfg, "captcha_host", "")):
            if not host:
                continue
            candidate = host.rstrip("/") + path
            if candidate not in out:
                out.append(candidate)
        return out

    def fetch_file(self, url: str) -> tuple[bytes, str, str]:
        """下载附件字节，返回 (内容, Content-Type, 实际使用的 URL)。"""
        import requests

        last = ""
        for candidate in self.candidate_urls(url, self.auth.cfg):
            try:
                resp = requests.get(candidate, timeout=180)
            except requests.RequestException as exc:      # noqa: PERF203
                last = f"{candidate} → {exc}"
                continue
            if resp.status_code == 200 and resp.content:
                return resp.content, resp.headers.get("Content-Type", ""), candidate
            last = f"{candidate} → HTTP {resp.status_code}"
        raise UpstreamError("附件下载失败（已尝试全部候选地址）", 404, last)
