"""分片上传与「上传后不再静默截断」的校验测试（离线，全部用桩）。"""

from __future__ import annotations

import base64
import hashlib

import pytest

from erp_tools.tools.attachments import AttachmentPageService, decode_attachments
from erp_tools.tools.attachments import CHUNK_STAGE_TTL

PAGE_PATH = "BaoJia/BJ_KHBJXQ/BJ_KHBJXQ_FJ.aspx"
BLOB = bytes(range(256)) * 8          # 2048 字节，切成 4 片


# ══════════════════ 桩：附件页 ══════════════════
CLASSIC_PAGE = """
<html><body><form name="form1" method="post" action="./X_FJ.aspx?PID=rid-1">
 <input type="hidden" name="__VIEWSTATE" value="vs" />
 <input type="hidden" name="__EVENTVALIDATION" value="ev" />
 <input type="text" name="txtPXH" /><input type="text" name="txtFJMC" />
 <input type="text" name="lblPID" value="rid-1" />
 <input type="submit" name="btnPostFile" value="" />
 <input type="file" name="FileUploadExcel" />
 <input type="submit" name="btn_DeleteFJ" value="" />
 <input type="submit" name="btn_Display_FJ" value="" />
 <table><tr><td>
   <table id="gridView1">
     <tr><th>选择</th><th>排序号</th><th>附件名称</th><th>附件描述</th>
         <th>附件文件</th><th>备注</th></tr>
     {rows}
   </table>
 </td></tr></table>
</form></body></html>
"""


def _row(no: int, name: str, file_id: str) -> str:
    return (
        "<tr><td><input type='checkbox'/></td>"
        f"<td>{no}</td>"
        f"<td onclick=\"OpenSelFieldValue('附件名称','FJMC','{name}','{file_id}')\">{name}</td>"
        "<td></td>"
        f"<td onclick=\"Display_FJ('{file_id}','rid-1')\">有</td><td></td></tr>"
    )


class _Resp:
    def __init__(self, text: str = "", status: int = 200) -> None:
        self.text = text
        self.status_code = status
        self.headers: dict[str, str] = {}


class _Client:
    """记住上传过的附件；`display` 时回一个指向静态文件的弹窗。"""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}       # 名称 -> 内容（落库）
        self.uploads: list[dict] = []

    def _page(self) -> str:
        rows = "".join(_row(i + 1, n, f"f{i}")
                       for i, n in enumerate(self.files))
        return CLASSIC_PAGE.replace("{rows}", rows)

    def get(self, _path: str, **_kw) -> _Resp:
        return _Resp(self._page())

    def post(self, _path: str, data=None, files=None, **_kw) -> _Resp:
        data = data or {}
        if files:
            filename, content, _ctype = files["FileUploadExcel"]
            self.files[data.get("txtFJMC") or filename] = content
            self.uploads.append({"name": data.get("txtFJMC"), "size": len(content)})
            return _Resp(self._page())
        if data.get("btn_Display_FJ") is not None or data.get("lblID"):
            fid = data.get("lblID") or ""
            return _Resp(f"<script>window.open('/Admin/FuJian/A/{fid}.zip')</script>")
        return _Resp(self._page())


class _Cfg:
    host = "http://erp.local:8889"
    captcha_host = "http://erp.local:8887"


class _Auth:
    def __init__(self) -> None:
        self.client = _Client()
        self.cfg = _Cfg()
        self.user_id = "2069"


@pytest.fixture()
def svc(monkeypatch, tmp_path):
    monkeypatch.setenv("ERP_ATTACH_STAGE_DIR", str(tmp_path / "stage"))
    auth = _Auth()
    service = AttachmentPageService(auth, PAGE_PATH)

    # 落库文件按 client.files 里的内容回放（file_id 形如 f0）
    def _fetch(url: str):
        fid = url.rsplit("/", 1)[-1].split(".")[0]
        names = list(auth.client.files)
        idx = int(fid.lstrip("f") or 0)
        if idx >= len(names):
            raise AssertionError(f"没有 {fid} 这个文件")
        return auth.client.files[names[idx]], "application/zip", url

    service.fetch_file = _fetch      # type: ignore[method-assign]
    return service


def _chunk(idx: int, total: int, part: bytes, **extra) -> dict:
    return {"filename": "样本.zip", "upload_key": "样本.zip",
            "chunk_index": idx, "total_chunks": total,
            "chunk_base64": base64.b64encode(part).decode(), **extra}


# ══════════════════ 分片暂存 ══════════════════
def test_chunks_accumulate_across_calls(svc) -> None:
    parts = [BLOB[i:i + 512] for i in range(0, len(BLOB), 512)]
    assert len(parts) == 4

    for idx, part in enumerate(parts[:-1]):
        out = svc.stage_chunks([_chunk(idx, 4, part, name="样本名", desc="d")])
        assert out["complete"] is False
        assert out["chunks"][0]["received"] == list(range(idx + 1))
        assert out["chunks"][0]["missing"] == list(range(idx + 1, 4))

    done = svc.stage_chunks([_chunk(3, 4, parts[3])])
    assert done["complete"] is True
    assert len(done["files"]) == 1
    assembled = base64.b64decode(done["files"][0]["content_base64"])
    assert assembled == BLOB
    assert done["files"][0]["name"] == "样本名"          # 第一片带的元信息被保住
    assert done["files"][0]["desc"] == "d"
    assert done["files"][0]["sha256"] == hashlib.sha256(BLOB).hexdigest()


def test_chunk_pending_when_pieces_missing(svc) -> None:
    out = svc.stage_chunks([_chunk(1, 3, BLOB[:10])])
    assert out["complete"] is False
    assert out["chunks"][0]["missing"] == [0, 2]
    assert "继续传" in out["message"]


def test_chunk_index_out_of_range(svc) -> None:
    from erp_tools.core.errors import ParseError

    with pytest.raises(ParseError, match="超出"):
        svc.stage_chunks([_chunk(5, 3, BLOB[:10])])


def test_chunk_total_mismatch_clears_stale_stage(svc) -> None:
    svc.stage_chunks([_chunk(0, 2, BLOB[:10])])
    # 同名但 total 变了 → 上一次的残局要被清掉
    out = svc.stage_chunks([_chunk(0, 4, BLOB[:10])])
    assert out["chunks"][0]["received"] == [0]
    assert out["chunks"][0]["missing"] == [1, 2, 3]


def test_chunk_expected_size_mismatch_discards(svc) -> None:
    parts = [BLOB[i:i + 512] for i in range(0, len(BLOB), 512)]
    svc.stage_chunks([_chunk(0, 2, parts[0], expected_size=999)])
    out = svc.stage_chunks([_chunk(1, 2, parts[1])])
    assert out["complete"] is False
    assert "不符" in out["message"]
    # 暂存已清空：重传第 0 片时只看到 0
    again = svc.stage_chunks([_chunk(0, 2, parts[0])])
    assert again["chunks"][0]["received"] == [0]


def test_chunk_sha256_mismatch_discards(svc) -> None:
    parts = [BLOB[i:i + 1024] for i in range(0, len(BLOB), 1024)]
    svc.stage_chunks([_chunk(0, 2, parts[0], sha256="0" * 64)])
    out = svc.stage_chunks([_chunk(1, 2, parts[1])])
    assert out["complete"] is False
    assert "不符" in out["message"]


def test_stale_stage_expires(svc) -> None:
    import os
    import time

    svc.stage_chunks([_chunk(0, 2, BLOB[:10])])
    base = os.environ["ERP_ATTACH_STAGE_DIR"]
    old = time.time() - CHUNK_STAGE_TTL - 60
    for root, _dirs, names in os.walk(base):
        for name in names:
            if name.endswith(".part"):
                os.utime(os.path.join(root, name), (old, old))
    out = svc.stage_chunks([_chunk(0, 2, BLOB[:10])])
    assert out["chunks"][0]["received"] == [0]      # 旧的被清掉，只剩这一片


# ══════════════════ 上传前/后校验 ══════════════════
def test_upload_rejects_shorter_content_than_declared(svc) -> None:
    out = svc.upload("rid-1", [{
        "filename": "截断.zip",
        "content_base64": base64.b64encode(BLOB[:100]).decode(),
        "expected_size": len(BLOB),
    }])
    assert out["ok"] is False
    assert out["uploaded"] == []
    assert "截断" in out["errors"][0]["error"]
    assert svc.auth.client.uploads == []            # 一个字节都没往 ERP 发


def test_upload_ok_when_size_matches(svc) -> None:
    out = svc.upload("rid-1", [{"filename": "好.zip",
                                "content_base64": base64.b64encode(BLOB).decode(),
                                "expected_size": len(BLOB)}])
    assert out["ok"] is True
    assert out["size_ok"] is True
    assert out["uploaded"][0]["stored_size"] == len(BLOB)


def test_upload_flags_truncated_store(svc) -> None:
    """ERP 只存下半截时，必须报 ok=False（不能只看名字就说成功）。"""
    def _short(url: str):
        return BLOB[:100], "application/zip", url

    svc.fetch_file = _short      # type: ignore[method-assign]
    out = svc.upload("rid-1", [{"filename": "半截.zip",
                                "content_base64": base64.b64encode(BLOB).decode()}])
    assert out["ok"] is False
    assert out["size_ok"] is False
    assert out["size_mismatch"][0]["reason"] == "size"
    assert out["size_mismatch"][0]["stored"] == 100
    assert "不一致" in out["message"]


def test_upload_flags_sha256_mismatch(svc) -> None:
    out = svc.upload("rid-1", [{"filename": "坏哈希.zip",
                                "content_base64": base64.b64encode(BLOB).decode(),
                                "sha256": "0" * 64}])
    assert out["ok"] is False
    assert out["size_mismatch"][0]["reason"] == "sha256"
    assert out["size_mismatch"][0]["expected_sha256"] == "0" * 64


def test_upload_chunked_end_to_end(svc) -> None:
    """分片 -> 拼装 -> 上传，落库字节与原文件完全一致。"""
    parts = [BLOB[i:i + 512] for i in range(0, len(BLOB), 512)]
    last = {}
    for idx, part in enumerate(parts):
        last = svc.upload("rid-1", [_chunk(idx, len(parts), part,
                                           expected_size=len(BLOB),
                                           sha256=hashlib.sha256(BLOB).hexdigest())])
        if idx < len(parts) - 1:
            assert last["status"] == "chunk_pending"
    assert last["ok"] is True
    assert last["size_ok"] is True
    assert svc.auth.client.files["样本.zip"] == BLOB
    assert len(svc.auth.client.uploads) == 1        # 只往 ERP 发了一次


def test_decode_keeps_expected_size_and_sha256() -> None:
    att = decode_attachments([{"filename": "a.bin", "content_text": "x",
                               "expected_size": 1, "sha256": "AB"}])[0]
    assert att.expected_size == 1
    assert att.sha256 == "ab"
    assert att.as_dict()["expected_size"] == 1
