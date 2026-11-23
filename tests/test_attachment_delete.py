"""删除附件（Modify=1 行内 Delete$N）的离线测试。"""

from __future__ import annotations

import pytest

from erp_tools.tools.attachments import AttachmentPageService
from erp_tools.tools.gridlist import form_snapshot  # noqa: F401  (确保依赖可用)

#: Modify=1 附件页：比只读页多一列「删除」，行内图标带 __doPostBack('gridView1','Delete$N')
FJ_PAGE_EDIT = """
<html><body><form name="form1" method="post"
   action="./X_FJ.aspx?PID=rid-1&amp;Modify=1" enctype="multipart/form-data">
 <input type="hidden" name="__VIEWSTATE" value="vs" />
 <input type="hidden" name="__EVENTVALIDATION" value="ev" />
 <input type="submit" name="Btn_DeleteFJ" value="×" />
 <input type="submit" name="btnPostFile" value="" />
 <table><tr><td>
   <table id="gridView1">
     <tr><th>删除</th><th>选择</th><th>排序号</th><th>附件名称</th>
         <th>附件描述</th><th>附件文件</th><th>备注</th></tr>
     <tr><td><input type="image" alt="删除此行数据"
             onclick="__doPostBack('gridView1','Delete$0');return false;"/></td>
         <td><input type="checkbox"/></td><td>1</td>
         <td onclick="OpenSelFieldValue('附件名称','FJMC','报价单.pdf','f1')">报价单.pdf</td>
         <td>原始</td><td onclick="Display_FJ('f1','rid-1')">有</td><td>备注A</td></tr>
     <tr><td><input type="image" alt="删除此行数据"
             onclick="__doPostBack('gridView1','Delete$1');return false;"/></td>
         <td><input type="checkbox"/></td><td>2</td>
         <td onclick="OpenSelFieldValue('附件名称','FJMC','AI摘要.txt','f2')">AI摘要.txt</td>
         <td></td><td onclick="Display_FJ('f2','rid-1')">有</td><td></td></tr>
   </table>
 </td></tr></table>
</form></body></html>
"""

#: 只读页（Modify=0）：没有删除列 → 不能删
FJ_PAGE_READONLY = """
<html><body><form name="form1" method="post" action="./X_FJ.aspx?PID=rid-1">
 <input type="hidden" name="__VIEWSTATE" value="vs" />
 <table><tr><td>
   <table id="gridView1">
     <tr><th>选择</th><th>排序号</th><th>附件名称</th><th>附件描述</th>
         <th>附件文件</th><th>备注</th></tr>
     <tr><td><input type="checkbox"/></td><td>1</td>
         <td onclick="OpenSelFieldValue('附件名称','FJMC','报价单.pdf','f1')">报价单.pdf</td>
         <td></td><td onclick="Display_FJ('f1','rid-1')">有</td><td></td></tr>
   </table>
 </td></tr></table>
</form></body></html>
"""


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status
        self.headers: dict[str, str] = {}


class _Client:
    """按 URL 里的 Modify 返回不同页面；删除后列表少一行。"""

    def __init__(self, edit_html: str = FJ_PAGE_EDIT) -> None:
        self.edit_html = edit_html
        self.calls: list[tuple[str, dict]] = []
        self.deleted: set[str] = set()

    def _rows_page(self) -> str:
        html = FJ_PAGE_EDIT
        for idx, fid in enumerate(("f1", "f2")):
            if fid in self.deleted:
                html = _page_without(idx)
        return html

    def get(self, path: str, **_kw) -> _Resp:
        self.calls.append(("GET", {"path": path}))
        return _Resp(self._rows_page())      # 两种 Modify 都反映当前行（删除后会少一行）

    def post(self, path: str, data=None, files=None, **_kw) -> _Resp:
        self.calls.append(("POST", {"path": path, "data": data or {}}))
        arg = (data or {}).get("__EVENTARGUMENT", "")
        if arg.startswith("Delete$"):
            idx = int(arg.split("$", 1)[1])
            fid = {0: "f1", 1: "f2"}[idx]
            self.deleted.add(fid)
            return _Resp(_page_without(idx))
        return _Resp(self.edit_html)


def _page_without(idx: int) -> str:
    """真实 ERP 删掉一行后，该行的名称格与「有」格都消失。"""
    html = FJ_PAGE_EDIT
    fid, name = ("f1", "报价单.pdf") if idx == 0 else ("f2", "AI摘要.txt")
    html = html.replace(
        f"""<td onclick="OpenSelFieldValue('附件名称','FJMC','{name}','{fid}')">{name}</td>""", "")
    html = html.replace(f"""<td onclick="Display_FJ('{fid}','rid-1')">有</td>""", "")
    return html


def _rows_page_after(idx: int) -> str:      # noqa: D401 - 简单桩
    html = FJ_PAGE_EDIT
    fid, name = ("f1", "报价单.pdf") if idx == 0 else ("f2", "AI摘要.txt")
    html = html.replace(
        f"""<td onclick="OpenSelFieldValue('附件名称','FJMC','{name}','{fid}')">{name}</td>""", "")
    html = html.replace(f"""<td onclick="Display_FJ('{fid}','rid-1')">有</td>""", "")
    return html
class _Auth:
    def __init__(self, client: _Client | None = None) -> None:
        self.client = client or _Client()
        self.user_id = "2069"
        self.cfg = type("C", (), {"host": "http://h:8889",
                                  "captcha_host": "http://h:8887"})()


def _svc(auth: _Auth) -> AttachmentPageService:
    return AttachmentPageService(auth, "BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx")


# ══════════════════ 行号解析 ══════════════════
def test_parse_rows_reads_delete_index() -> None:
    rows = AttachmentPageService._parse_rows(FJ_PAGE_EDIT)
    assert [r["行号"] for r in rows] == ["0", "1"]
    assert rows[0]["名称"] == "报价单.pdf"
    assert rows[1]["名称"] == "AI摘要.txt"


def test_parse_rows_without_delete_column() -> None:
    rows = AttachmentPageService._parse_rows(FJ_PAGE_READONLY)
    assert rows and all(r["行号"] == "" for r in rows)


# ══════════════════ 删除 ══════════════════
def test_delete_preview_does_not_write() -> None:
    auth = _Auth()
    result = _svc(auth).delete_file("rid-1", name="报价单.pdf")
    assert result["status"] == "needs_confirmation"
    assert result["would_delete"]["名称"] == "报价单.pdf"
    assert result["would_delete"]["文件ID"] == "f1"
    assert result["count"] == 2
    assert not [c for c in auth.client.calls if c[0] == "POST"]


def test_delete_posts_delete_index_and_verifies() -> None:
    auth = _Auth()
    result = _svc(auth).delete_file("rid-1", name="AI摘要.txt", confirm=True)
    assert result["ok"] is True and result["status"] == "deleted"
    assert result["deleted"]["文件ID"] == "f2"
    assert result["remaining"] == ["报价单.pdf"]

    post = [c for c in auth.client.calls if c[0] == "POST"][0][1]
    assert post["data"]["__EVENTTARGET"] == "gridView1"
    assert post["data"]["__EVENTARGUMENT"] == "Delete$1"
    # 删除必须走 Modify=1
    assert "Modify=1" in post["path"]


def test_delete_by_file_id() -> None:
    result = _svc(_Auth()).delete_file("rid-1", file_id="f1", confirm=True)
    assert result["ok"] is True
    assert result["deleted"]["名称"] == "报价单.pdf"


def test_delete_unknown_name_lists_existing() -> None:
    with pytest.raises(Exception) as exc:
        _svc(_Auth()).delete_file("rid-1", name="不存在.pdf", confirm=True)
    assert "没找到附件" in str(exc.value)
    assert "报价单.pdf" in str(exc.value)


def test_delete_without_delete_column_raises() -> None:
    class _NoEdit(_Client):
        def get(self, path: str, **_kw) -> _Resp:
            return _Resp(FJ_PAGE_READONLY)      # 永远拿不到 Modify=1 的删除列

    auth = _Auth(_NoEdit())
    with pytest.raises(Exception) as exc:
        _svc(auth).delete_file("rid-1", name="报价单.pdf", confirm=True)
    assert "没有删除入口" in str(exc.value)


def test_delete_files_batch_reports_each() -> None:
    auth = _Auth()
    result = _svc(auth).delete_files("rid-1", ["报价单.pdf", "不存在.pdf"], confirm=True)
    assert result["count"] == 2
    assert result["results"][0]["ok"] is True
    assert result["results"][1]["ok"] is False
    assert result["ok"] is False
