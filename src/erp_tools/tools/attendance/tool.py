"""考勤查询（RenShi/RS_KQDA_M/Add.aspx）。

协议要点（实测）：
  * GET 页面 → 取 __VIEWSTATE，再用一整套隐藏字段做 POST 查询；
  * `txtBMMC_Data` 必须是**二次编码**后的 `%3Cr%3E%3Cc%3E%3C%2Fc%3E...`，
    直接提交 `<r><c></c></r>` 会被 ASP.NET 的 request validation 拦成 500；
  * `lblgVQXBH` 必须等于当前页面的 QXID；
  * 结果是 div#gViewDiv2 里的 table#gridView，`lblRowsCount/lblPageCount/lblCurrentPage`
    给出总数与分页。

翻页策略（两层）：
  1. 首选：搜索时把 `txtEvenPageDataSize` 直接设大（默认 5000），一次拿全
     —— 已实测单页可返回全部记录；
  2. 兜底：若仍有多页，用 `__EVENTTARGET=btnNext` 逐页回发；
     该分支在部分环境会返回 500，失败时明确抛错而不是静默截断。
"""

from __future__ import annotations

import sys
from typing import Iterator

from ...core.aspnet import build_postback, extract_viewstate
from ...core.auth import AuthManager, ensure_authenticated
from ...core.errors import ParseError, UpstreamError
from ..base import Option, Tool
from .parser import AttendanceTable, parse_attendance_table

PAGE_PATH = "RenShi/RS_KQDA_M/Add.aspx"

#: 浏览器提交时 txtBMMC_Data 是 encodeURIComponent 后的值，必须原样再提交
GRID_DATA_ENCODED = "%3Cr%3E%3Cc%3E%3C%2Fc%3E%3C%2Fr%3E"

BASE_PARAMS = {
    "txtBMMCSelect": "2",
    "txtBMMC_Input": "<--请选择-->",
    "txtBMMC_SelectedIndex": "-1",
    "txtBMMC_Data": GRID_DATA_ENCODED,
    "txtKey": "",
    "DropField": "BMMC",
    "txtDKYC": "正常+异常",
    "txtYGZT": "未离职",
    "txtSBSJ": "DKKSSD1",
    "txtKSTime": "",
    "txtJZTime": "",
    "txtXXSJ": "",
    "txtFZTJ": "-0-",
    "txtYGFZ": "-0-",
    "lblID": "",
    "lblPID": "",
    "lblYGID": "",
    "lblYGXM": "",
    "lblNum": "",
    "txtBCID": "",
    "txtBCMC": "",
    "txtZBSJ": "",
    "txtJBSJ": "",
    "txtDataLX": "",
    "txtBKLX": "",
    "txtQKRQ": "",
    "txtQJRQ": "",
    "txtRowIndex": "",
    "lblSelectDataLX": "",
    "txtSHR": "",
    "lblgVID": "",
    "lblgVSetQXID": "",
    "txtSelectBMMC": "",
    "txtSelectGKH": "",
    "txtSelectYGXM": "",
    "lblFIEID": "",
    "txtYBCSDataLX": "",
    "txtQXLXID": "",
    "txtRemark": "",
    "txtQKYY": "-0-",
    "txtQKSJ": "",
    "txtQKYY_1": "-0-",
    "txtQKSJ1": "",
    "txtQJXS": "",
    "chkSFZS": "on",
    "chkSFYC": "on",
    "Drop_SFYB": "0",
    "Drop_CD": "0",
    "Drop_ZT": "0",
    "Drop_WC": "0",
    "Drop_QJ": "0",
    "Drop_XJ": "0",
    "Drop_KG": "0",
    "txtBCLB": "0",
    "txtHQKQBCRQ": "",
    "txtSJCQZB": "",
    "txtSJCQJB": "",
    "txtYBCS": "",
    "txtQTCQXS": "",
    "__SCROLLPOSITIONX": "0",
    "__SCROLLPOSITIONY": "0",
    "__VIEWSTATEENCRYPTED": "",
    "__ASYNCPOST": "true",
    "btn_Search.x": "5",
    "btn_Search.y": "1",
}


def build_search_params(*, qxid: str, month: str, start_day: str = "01",
                        end_day: str = "31", page_size: int = 5000) -> dict[str, str]:
    params = dict(BASE_PARAMS)
    params.update({
        "ScriptManager1": "UpdatePanel1|btn_Search",
        "txtYF": month,
        "txtKSDay": start_day.zfill(2),
        "txtJZDay": end_day.zfill(2),
        "txtFXCX": "1",
        "txtEvenPageDataSize": str(page_size),
        "lblgVQXBH": qxid,
    })
    return params


class AttendanceTool(Tool):
    name = "attendance"
    summary = "查询员工月度考勤（RS_KQDA_M）→ 结构化行 + Markdown"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("month", "查询月份，如 2026-11", required=True, cli="--month"),
            Option("start_day", "起始日（默认 01）", default="01", cli="--from"),
            Option("end_day", "结束日（默认 31）", default="31", cli="--to"),
            Option("users", "工卡号列表；为空则用当前登录账号", default=None, cli="--emp"),
            Option("out", "Markdown 输出路径", cli="--out"),
            Option("jsonl", "JSONL 输出路径", cli="--jsonl"),
            Option("page_size", "单页记录数（默认取 ERP_PAGE_SIZE_MAX）",
                   default=None, cli="--page-size"),
        ]

    # ---------- 单用户 ----------
    def _auth_for(self, username: str | None, password: str | None) -> AuthManager:
        cfg = self.cfg
        if (not username or username == cfg.username) and not password:
            return self.auth
        sub = ensure_authenticated(
            cfg.replace(username=username or cfg.username, password=password or cfg.password),
            interactive=getattr(self.auth, "interactive", True),
        )
        return sub

    def page_url(self, auth: AuthManager, qxid: str) -> str:
        uid = auth.user_id or ""
        url = f"/Admin/{PAGE_PATH}?QXID={qxid}&MenuTree=1"
        return f"{url}&LoginUserID={uid}" if uid else url

    def _open_page(self, auth: AuthManager, qxid: str) -> tuple[str, dict[str, str]]:
        resp = auth.client.get(self.page_url(auth, qxid))
        if resp.status_code != 200:
            raise UpstreamError(f"考勤页打开失败（HTTP {resp.status_code}）", resp.status_code)
        vs = extract_viewstate(resp.text)
        if "__VIEWSTATE" not in vs:
            raise ParseError("考勤页缺少 __VIEWSTATE，可能被重定向到登录页")
        return resp.text, vs

    def _search(self, auth: AuthManager, qxid: str, params: dict[str, str],
                viewstate: dict[str, str]) -> tuple[AttendanceTable, str]:
        body = dict(params)
        body.update(viewstate)
        body["ScriptManager1"] = "UpdatePanel1|btn_Search"

        resp = auth.client.post(self.page_url(auth, qxid), data=body)
        if resp.status_code >= 500:
            raise UpstreamError("考勤查询失败（HTTP %s）" % resp.status_code,
                                resp.status_code, resp.text[:300])
        table = parse_attendance_table(resp.text)
        if not table.headers:
            raise ParseError("考勤结果里没有表格，页面结构可能又变了")
        return table, resp.text

    def _next_page(self, auth: AuthManager, qxid: str, params: dict[str, str],
                   current_html: str) -> tuple[AttendanceTable, str]:
        """兜底翻页：__EVENTTARGET=btnNext 全量回发。"""
        body = build_postback(params, extract_viewstate(current_html),
                              event_target="btnNext")
        body["ScriptManager1"] = "UpdatePanel1|btnNext"
        body.pop("btn_Search.x", None)
        body.pop("btn_Search.y", None)
        resp = auth.client.post(self.page_url(auth, qxid), data=body)
        if resp.status_code >= 500:
            raise UpstreamError(
                "btnNext 翻页被服务端拒绝（HTTP %s）；请调大 --page-size 一次取全"
                % resp.status_code, resp.status_code, resp.text[:300])
        table = parse_attendance_table(resp.text)
        if not table.headers:
            raise UpstreamError("btnNext 翻页返回的不是考勤表格，翻页协议可能变了")
        return table, resp.text

    # ---------- 主流程 ----------
    def query_user(self, username: str | None, password: str | None, *, month: str,
                   start_day: str, end_day: str, page_size: int) -> Iterator[dict]:
        auth = self._auth_for(username, password)
        qxid = self.cfg.qxid_attendance or auth.qxid_for(PAGE_PATH)

        html, vs = self._open_page(auth, qxid)
        params = build_search_params(qxid=qxid, month=month, start_day=start_day,
                                     end_day=end_day, page_size=page_size)
        table, html = self._search(auth, qxid, params, vs)

        who = username or self.cfg.username
        self._log(f"[INFO] {who} {month}: {table.total} 条 / {table.pages} 页")

        for row in table.rows:
            yield {"_user": who, "_month": month, "_page": table.page, **row}

        hop = 0
        while table.has_more_pages:
            hop += 1
            if hop > 200:
                raise UpstreamError("翻页次数异常（>200），终止以避免打爆服务端")
            table, html = self._next_page(auth, qxid, params, html)
            self._log(f"[INFO] 翻到第 {table.page}/{table.pages} 页（{len(table.rows)} 行）")
            for row in table.rows:
                yield {"_user": who, "_month": month, "_page": table.page, **row}


    def run(self, *, month: str, start_day: str = "01", end_day: str = "31",
            users: list[str] | None = None, out: str | None = None,
            jsonl: str | None = None, page_size: int | None = None) -> Iterator[dict]:
        size = int(page_size or self.cfg.page_size_max)
        targets = users or [self.cfg.username or None]

        collected: list[dict] = []
        sink = None
        if jsonl:
            from ...output.jsonl import JsonlSink

            sink = JsonlSink(jsonl, key=None, resume=False)

        for username in targets:
            user = username.split(":", 1)[0] if username else None
            pwd = username.split(":", 1)[1] if username and ":" in username else None
            try:
                for rec in self.query_user(user, pwd, month=month, start_day=start_day,
                                           end_day=end_day, page_size=size):
                    if sink is not None:
                        sink.write(rec)
                    collected.append(rec)
                    yield rec
            except Exception as exc:  # noqa: BLE001
                self._log(f"[ERROR] {user or self.cfg.username}：{exc}")

        if sink is not None:
            sink.close()
        if out:
            from ...output.markdown import to_markdown_table

            if collected:
                headers = [k for k in collected[0].keys() if not k.startswith("_")]
                rows = [[rec.get(h) for h in headers] for rec in collected]
                text = to_markdown_table(headers, rows)
            else:
                text = "（无考勤数据）"
            from pathlib import Path

            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(text, encoding="utf-8")
            self._log(f"[DONE] 已写出 Markdown：{out}（{len(collected)} 行）")

    @staticmethod
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)


__all__ = ["AttendanceTool", "build_search_params", "PAGE_PATH", "GRID_DATA_ENCODED"]
