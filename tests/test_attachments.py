"""附件抽象（tools/attachments.py）的离线测试：解码、解析、URL 回退、上传流程用桩。"""

from __future__ import annotations

import base64

import pytest

from erp_tools.tools.attachments import (
    Attachment,
    AttachmentPageService,
    decode_attachment,
    decode_attachments,
)

PDF = b"%PDF-1.4\n%%EOF\n"

#: 附件页（简化自真实页面）：外层包装表 + 内层 gridView1
FJ_PAGE = """
<html><body><form name="form1" method="post"
   action="./X_FJ.aspx?PID=rid-1&amp;Modify=0" enctype="multipart/form-data">
 <input type="hidden" name="__VIEWSTATE" value="vs" />
 <input type="hidden" name="__EVENTVALIDATION" value="ev" />
 <input type="text" name="txtPXH" /><input type="text" name="txtFJMC" />
 <input type="text" name="lblPID" value="rid-1" />
 <input type="submit" name="btnPostFile" value="" />
 <input type="file" name="FileUploadExcel" />
 <table><tr><td>
   <table id="gridView1">
     <tr><th>选择</th><th>排序号</th><th>附件名称</th><th>附件描述</th>
         <th>附件文件</th><th>备注</th></tr>
     <tr><td><input type="checkbox"/></td><td>1</td>
         <td onclick="OpenSelFieldValue('附件名称','FJMC','客户报价单.pdf','f1')">客户报价单.pdf</td>
         <td>原始报价</td>
         <td onclick="Display_FJ('f1','rid-1')">有</td><td>来自客户</td></tr>
     <tr><td>2</td><td onclick="OpenSelFieldValue('附件名称','FJMC','AI摘要.txt','f2')">AI摘要.txt</td>
         <td></td><td onclick="Display_FJ('f2','rid-1')">有</td><td></td></tr>
   </table>
 </td></tr></table>
 <input type="submit" name="btn_Display_FJ" value="" />
</form></body></html>
"""


# ══════════════════ 解码 ══════════════════
def test_decode_base64() -> None:
    att = decode_attachment({"filename": "a.pdf",
                             "content_base64": base64.b64encode(PDF).decode()})
    assert isinstance(att, Attachment)
    assert att.content == PDF
    assert att.erp_name == "a.pdf"
    assert att.content_type == "application/pdf"


def test_decode_text_and_name() -> None:
    att = decode_attachment({"filename": "note.txt", "content_text": "第一行\n第二行",
                             "name": "AI摘要", "desc": "描述", "remark": "备注"})
    assert att.content.decode("utf-8") == "第一行\n第二行"
    assert att.erp_name == "AI摘要"
    assert (att.desc, att.remark) == ("描述", "备注")


def test_decode_data_uri_learns_type() -> None:
    att = decode_attachment({
        "filename": "x.txt",
        "data_uri": "data:text/plain;base64," + base64.b64encode(b"hi").decode(),
    })
    assert att.content == b"hi"
    assert att.content_type == "text/plain"


def test_decode_from_path(tmp_path) -> None:
    src = tmp_path / "报价单.pdf"
    src.write_bytes(PDF)
    att = decode_attachment({"path": "报价单.pdf"}, base_dir=tmp_path)
    assert att.content == PDF
    assert att.filename == "报价单.pdf"


def test_decode_errors_are_readable(tmp_path) -> None:
    with pytest.raises(Exception) as exc:
        decode_attachment({"filename": "a.pdf"})
    assert "content_base64" in str(exc.value)

    with pytest.raises(Exception) as exc:
        decode_attachment({"path": str(tmp_path / "nope.pdf")})
    assert "不存在" in str(exc.value)

    with pytest.raises(Exception):
        decode_attachment({"filename": "a.bin", "content_base64": "!!!not-base64!!!"})


def test_decode_attachments_accepts_string_paths(tmp_path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")
    items = decode_attachments(f"{a},{b}")
    assert [i.filename for i in items] == ["a.txt", "b.txt"]
    assert decode_attachments([]) == []


def test_size_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ERP_ATTACH_MAX_MB", "0.1")   # 100KB
    with pytest.raises(Exception) as exc:
        decode_attachment({"filename": "big.bin",
                           "content_base64": base64.b64encode(b"x" * 200_000).decode()})
    assert "超过上限" in str(exc.value)


# ══════════════════ 列表解析 ══════════════════
def test_parse_rows_uses_column_positions() -> None:
    rows = AttachmentPageService._parse_rows(FJ_PAGE)
    assert [r["文件ID"] for r in rows] == ["f1", "f2"]
    assert rows[0]["名称"] == "客户报价单.pdf"
    assert rows[0]["排序号"] == "1"
    assert rows[0]["描述"] == "原始报价"
    assert rows[0]["备注"] == "来自客户"
    assert rows[0]["有文件"] == "有"
    # 第二行缺「选择」复选框、备注为空，仍要对齐
    assert rows[1]["排序号"] == "2"
    assert rows[1]["名称"] == "AI摘要.txt"
    assert rows[1]["描述"] == ""
    assert rows[1]["备注"] == ""


def test_parse_rows_empty_page() -> None:
    assert AttachmentPageService._parse_rows("<html><body>没有表格</body></html>") == []


# ══════════════════ URL 回退 ══════════════════
class _Cfg:
    host = "http://erp.local:8889"
    captcha_host = "http://erp.local:8887"


def test_candidate_urls_fallbacks() -> None:
    urls = AttachmentPageService.candidate_urls(
        "http://erp.local:8887/Admin/FuJian/A/Picture/x.pdf?time=1", _Cfg())
    assert urls[0].startswith("http://erp.local:8887")
    assert "http://erp.local:8889/Admin/FuJian/A/Picture/x.pdf?time=1" in urls


# ══════════════════ 上传流程（桩）══════════════════
class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status
        self.headers: dict[str, str] = {}


class _FakeClient:
    """模拟附件页：POST 上传后，后续 GET/POST 都能看到这条附件。"""

    def __init__(self, echo: bool = True) -> None:
        self.uploads: list[tuple[str, dict]] = []
        self.echo = echo
        self.name = ""

    def _page(self) -> str:
        if self.echo and self.name:
            return FJ_PAGE.replace("AI摘要.txt", self.name)
        return FJ_PAGE

    def get(self, _path: str, **_kw) -> _Resp:
        return _Resp(self._page())

    def post(self, path: str, data=None, files=None, **_kw) -> _Resp:
        self.uploads.append((path, {"data": data or {}, "files": files or {}}))
        self.name = (data or {}).get("txtFJMC", "")
        return _Resp(self._page())


class _Auth:
    def __init__(self) -> None:
        self.client = _FakeClient()
        self.user_id = "2069"
        self.cfg = _Cfg()


def test_upload_posts_multipart_and_verifies() -> None:
    auth = _Auth()
    svc = AttachmentPageService(auth, "BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx")

    result = svc.upload("rid-1", decode_attachments([
        {"filename": "new.pdf", "content_base64": base64.b64encode(PDF).decode(),
         "desc": "描述"},
    ]))
    assert result["ok"] is True
    assert result["verified"] is True
    assert result["uploaded"][0]["name"] == "new.pdf"

    path, payload = auth.client.uploads[0]
    assert path == "/Admin/BaoJia/BJ_KHBJXQ/X_FJ.aspx?PID=rid-1&Modify=0"
    assert payload["data"]["txtFJMC"] == "new.pdf"
    assert payload["data"]["txtFJMS"] == "描述"
    assert "btnPostFile" in payload["data"]
    filename, content, ctype = payload["files"]["FileUploadExcel"]
    assert (filename, content, ctype) == ("new.pdf", PDF, "application/pdf")


def test_upload_reports_missing_on_readback() -> None:
    class _Dropping(_Auth):
        def __init__(self) -> None:
            super().__init__()
            self.client = _FakeClient(echo=False)   # 服务端吞掉上传的附件

    svc = AttachmentPageService(_Dropping(), "BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx")
    result = svc.upload("rid-1", decode_attachments([
        {"filename": "ghost.pdf", "content_text": "x"}]))
    assert result["ok"] is False
    assert result["uploaded"] == []
    assert result["missing"] == []              # 逐个上传阶段就判定失败
    assert "ghost.pdf" in result["errors"][0]["filename"]
    assert "没有出现在列表里" in result["errors"][0]["error"]
