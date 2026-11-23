"""ERP 通用「上传附件」页（`*_FJ.aspx`）的读写抽象。

客户订单 `DingDan/DD_KHDD/DD_KHDD_FJ.aspx`、客户报价需求
`BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx` 用的是**同一套模板**，所以这里做成一个
可复用的 `AttachmentPageService`：换一个 `page_path` 就能用。

实测协议（2026-11-23，账号 15627）：

```
GET  /Admin/<模块>/<X>_FJ.aspx?PID=<记录ID>&Modify=0&LoginUserID=<uid>
     → form1 是 multipart/form-data，字段：
        txtPXH(排序号) txtFJMC(附件名称) txtFJMS(附件描述) txtRemark(备注)
        lblPID(记录ID) lblID(已有附件行ID) btnPostFile(提交) FileUploadExcel(文件)
POST 同一个 action（multipart），原样带上 __VIEWSTATE/__EVENTVALIDATION：
        txtPXH=1 & txtFJMC=名称 & btnPostFile= & FileUploadExcel=<文件名,字节>
     → 200，页面列表里多一行；记录页的 `[附件(无)]` 变成 `[附件(有)]`

文件本体在静态目录 `/Admin/FuJian/<模块>/Picture/<guid>.<ext>`：
。⚠ 实测**不同模块落在不同端口**（客户订单在 :8887，报价需求在 :8889），
  所以下载时会在 `cfg.host` 与 `cfg.captcha_host` 之间回退。
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
    """`*_FJ.aspx` 附件页：列表 / 上传 / 取文件直链。

    `page_path` 形如 `BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx`（相对 `/Admin/`）。
    """

    auth: Any
    page_path: str
    #: 返回给调用方的标签（用于日志/错误信息）
    label: str = "附件"

    # ══════════════════ 地址 / 打开 ══════════════════
    def page_url(self, record_id: str, *, modify: str = "0") -> str:
        uid = getattr(self.auth, "user_id", "") or ""
        url = f"/Admin/{self.page_path}?PID={record_id}&Modify={modify}"
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
            for el in tr.find_all("input"):
                m = _DELETE_INDEX_RE.search(el.get("onclick") or "")
                if m:
                    row_index = m.group(1)
                    break
            rows.append({
                "文件ID": fid,
                "名称": name,
                "排序号": cell("排序号"),
                "描述": cell("附件描述"),
                "备注": cell("备注"),
                "有文件": cell("附件文件"),
                "行号": row_index,
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
                    confirm: bool = False) -> dict[str, Any]:
        """删除一个附件。`confirm=False` 只返回「将会删除哪一条」，不写库。

        协议（实测 2026-11-23）：附件页 `Modify=1` 才有删除列，
        行内删除 = `__doPostBack('gridView1','Delete$<行号>')`，
        也就是 POST 时 `__EVENTTARGET=gridView1` + `__EVENTARGUMENT=Delete$N`。
        """
        html, url, _action = self._open(record_id, modify="1")
        rows = self._parse_rows(html)
        if not rows:
            raise ParseError(f"{self.label}：单据上没有附件")
        target = self._pick(rows, name=name, file_id=file_id)
        if not target.get("行号"):
            raise ParseError(
                "当前页面没有删除入口（Modify=1 下没渲染 Delete 索引）；"
                "可能是账号无权限，或该模块的附件页不支持删除"
            )
        from ..core.aspnet import soup_of

        if not confirm:
            return {
                "ok": False, "status": "needs_confirmation", "record_id": record_id,
                "would_delete": {k: target[k] for k in ("名称", "文件ID", "排序号")},
                "count": len(rows),
                "message": "确认删除请传 confirm=true",
            }

        body = form_snapshot(html)
        body["__EVENTTARGET"] = "gridView1"
        body["__EVENTARGUMENT"] = f"Delete${target['行号']}"
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
