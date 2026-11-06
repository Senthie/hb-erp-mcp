"""客户报价需求【列表】查询（BaoJia/BJ_KHBJXQ/Index.aspx）。

用法要点：
  * 服务端只支持「日期区间 + 两个关键字」；其余条件由 `--where` 在本地过滤；
  * 表单不是硬编码的：每次先 GET 页面，把它自己的整张表单快照下来再覆盖条件，
    ERP 加字段时不用改代码；
  * 分页：优先把页大小调大一次取全，必要时用 `__EVENTTARGET=btnNext` 续翻。
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Iterator

from ...core.aspnet import soup_of
from ...core.errors import ParseError, UpstreamError
from ..base import Option, Tool
from .filters import Condition, apply_conditions, parse_where
from .parser import ACTION_COLUMNS, QuoteListPage, parse_list_page

PAGE_PATH = "BaoJia/BJ_KHBJXQ/Index.aspx"
MAX_PAGES_GUARD = 200
#: 不指定 --out 时，控制台最多预览多少行
PREVIEW_ROWS = 20

#: 表单编号里编码了制单日期：BJXQ + YYMMDD + 流水号（可带 -NN 后缀）
BDBH_DATE_RE = re.compile(r"^[A-Za-z]{2,6}(\d{2})(\d{2})(\d{2})")


def date_windows_for_bdbh(bdbh: str) -> list[tuple[str, str]]:
    """从表单编号推出候选查询日期区间（先当天，再当月）。

    ERP 列表页必须带日期范围（空日期会被服务端忽略关键字），而单号本身就含
    制单日期，例如 BJXQ261105013 → 2026-11-05，所以可以精确定位。
    """
    m = BDBH_DATE_RE.match((bdbh or "").strip())
    if not m:
        return []
    yy, mm, dd = (int(x) for x in m.groups())
    try:
        day = date(2000 + yy, mm, dd)
    except ValueError:
        return []

    first = day.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    last = date.fromordinal(nxt.toordinal() - 1)

    windows = [(day.isoformat(), day.isoformat())]
    month = (first.isoformat(), last.isoformat())
    if month not in windows:
        windows.append(month)
    return windows


def _today() -> date:
    return date.today()


def default_date_range() -> tuple[str, str]:
    """默认查本月：1 号 ~ 今天（页签默认只给 7 天，容易漏数据）。"""
    today = _today()
    return today.replace(day=1).isoformat(), today.isoformat()


def form_snapshot(html: str) -> dict[str, str]:
    """把页面 form1 里的所有可提交控件状态快照成字典。

    跳过：`gridView$*`（每行勾选框）、image/submit/button（不参与普通提交）、
    未勾选的 checkbox。select 取当前选中项。
    """
    soup = soup_of(html)
    data: dict[str, str] = {}

    for el in soup.find_all("input"):
        name = el.get("name")
        if not name or name.startswith("gridView$"):
            continue
        if el.get("type") in ("image", "submit", "button", "reset", "file"):
            continue
        if el.get("type") == "checkbox" and not el.has_attr("checked"):
            continue
        data[name] = el.get("value") or ""

    for sel in soup.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        data[name] = (opt.get("value") or "") if opt is not None else ""

    return data


def field_codes(page_html: str, which: str = "DropField") -> dict[str, str]:
    """取关键字字段下拉的 中文名 -> 代码 映射（如 客户名称 -> KHMC）。"""
    soup = soup_of(page_html)
    sel = soup.find("select", {"name": which})
    if sel is None:
        return {}
    out = {}
    for opt in sel.find_all("option"):
        label = opt.get_text(strip=True)
        value = opt.get("value") or ""
        if label and value:
            out.setdefault(label, value)
    return out


class QuoteListTool(Tool):
    name = "query"
    summary = "查询客户报价需求列表（BJ_KHBJXQ/Index.aspx）→ 表格 / CSV / JSONL"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("from_date", "制单起始日期 YYYY-MM-DD（默认本月 1 号）", cli="--from"),
            Option("to_date", "制单结束日期 YYYY-MM-DD（默认今天）", cli="--to"),
            Option("key", "服务端关键字（配合 --field）", cli="--key"),
            Option("field", "关键字字段中文名，如 客户名称 / 产品名称 / 香港单号",
                   cli="--field"),
            Option("where", "本地过滤条件，逗号分隔：客户名称~ADG,报价状态=需求审批",
                   cli="--where"),
            Option("customer", "客户名称包含（等价 --where 客户名称~X）", cli="--customer"),
            Option("sales", "业务员包含（等价 --where 业务员~X）", cli="--sales"),
            Option("status", "报价状态包含（如 需求审批 / 报价审批）", cli="--status"),
            Option("columns", "只导出指定列，逗号分隔", cli="--columns"),
            Option("out", "导出 CSV 路径（.jsonl 结尾则写 JSONL）", cli="--out"),
            Option("jsonl", "额外写一份 JSONL（便于程序消费）", cli="--jsonl"),
            Option("limit", "最多返回多少条", cli="--limit"),
            Option("page_size", "每页记录数（默认 ERP_PAGE_SIZE_MAX）", cli="--page-size"),
            Option("max_pages", f"最多翻多少页（安全阀，默认 {MAX_PAGES_GUARD}）",
                   cli="--max-pages"),
            Option("dry_run", "只显示解析出的条件与命中数，不写文件", cli="--dry-run"),
        ]

    # ══════════════════ 页面 ══════════════════
    def page_url(self, qxid: str) -> str:
        uid = self.auth.user_id or ""
        url = f"/Admin/{PAGE_PATH}?QXID={qxid}&IsAdd=1"
        return f"{url}&LoginUserID={uid}" if uid else url

    def qxid(self) -> str:
        return self.cfg.qxid_quote_index or self.auth.qxid_for(PAGE_PATH)

    # ══════════════════ 查询 ══════════════════
    def _search(self, qxid: str, *, from_date: str, to_date: str, page_size: int,
                key: str = "", field: str = "") -> tuple[QuoteListPage, str]:
        url = self.page_url(qxid)
        resp = self.client.get(url)
        if resp.status_code != 200:
            raise UpstreamError(f"列表页打开失败（HTTP {resp.status_code}）", resp.status_code)
        if "__VIEWSTATE" not in resp.text:
            raise ParseError("列表页没有 __VIEWSTATE，可能被重定向到登录页")

        body = form_snapshot(resp.text)
        body.update({
            "txtKSRQ": from_date,
            "txtJZRQ": to_date,
            "txtEvenPageDataSize": str(page_size),
            "btn_Search.x": "5",
            "btn_Search.y": "1",
        })
        if key:
            body["txtKey"] = key
            if field:
                codes = field_codes(resp.text, "DropField")
                if field in codes:
                    body["DropField"] = codes[field]
                elif field in codes.values():
                    body["DropField"] = field
                else:
                    raise ParseError(
                        f"未知关键字字段 {field!r}；可用：{', '.join(codes) or '(页面无此下拉)'}")

        resp = self.client.post(url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(f"查询失败（HTTP {resp.status_code}）",
                                resp.status_code, resp.text[:300])
        page = parse_list_page(resp.text)
        if not page.headers:
            raise ParseError("查询结果里没有表格，页面结构可能又变了")
        return page, resp.text

    def _next_page(self, qxid: str, current_html: str, *, from_date: str, to_date: str,
                   page_size: int) -> tuple[QuoteListPage, str]:
        url = self.page_url(qxid)
        body = form_snapshot(current_html)
        body.update({
            "txtKSRQ": from_date,
            "txtJZRQ": to_date,
            "txtEvenPageDataSize": str(page_size),
            "__EVENTTARGET": "btnNext",
            "__EVENTARGUMENT": "",
        })
        resp = self.client.post(url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(
                f"翻页被服务端拒绝（HTTP {resp.status_code}）；可调大 --page-size 一次取全",
                resp.status_code, resp.text[:300])
        page = parse_list_page(resp.text)
        if not page.headers:
            raise UpstreamError("翻页返回的不是列表表格，翻页协议可能变了")
        return page, resp.text

    def collect(self, *, from_date: str | None = None, to_date: str | None = None,
                key: str = "", field: str = "", where: str | None = None,
                customer: str | None = None, sales: str | None = None,
                status: str | None = None, limit: int | None = None,
                page_size: int | None = None,
                max_pages: int = MAX_PAGES_GUARD) -> tuple[list[dict], list[str], QuoteListPage]:
        """执行查询并返回 (记录, 可用列, 首页分页信息)。"""
        d_from, d_to = default_date_range()
        from_date = from_date or d_from
        to_date = to_date or d_to
        size = int(page_size or self.cfg.page_size_max)
        qxid = self.qxid()

        conditions: list[Condition] = list(parse_where(where))
        if customer:
            conditions.append(Condition("客户名称", "~", customer))
        if sales:
            conditions.append(Condition("业务员", "~", sales))
        if status:
            conditions.append(Condition("报价状态", "~", status))

        self._log(f"[INFO] {from_date} ~ {to_date}  页大小={size}  QXID={qxid}")
        page, html = self._search(qxid, from_date=from_date, to_date=to_date,
                                  page_size=size, key=key, field=field)
        self._log(f"[INFO] 服务端命中 {page.total} 条 / {page.pages} 页")

        headers = list(page.headers)
        rows: list[dict] = list(page.rows)
        hop = 0
        while page.has_more_pages:
            hop += 1
            if hop > max_pages:
                raise UpstreamError(f"翻页超过 {max_pages} 页，已停止（可调大 --max-pages）")
            page, html = self._next_page(qxid, html, from_date=from_date,
                                         to_date=to_date, page_size=size)
            rows.extend(page.rows)
            self._log(f"[INFO] 第 {page.page}/{page.pages} 页累计 {len(rows)} 条")

        rows = apply_conditions(rows, conditions, headers + ["记录ID"])
        if conditions:
            self._log(f"[INFO] 本地条件过滤后 {len(rows)} 条")
        if limit:
            rows = rows[: int(limit)]
        return rows, headers, page

    # ══════════════════ 主流程 ══════════════════
    def find_by_bdbh(self, bdbh: str, *, page_size: int | None = None) -> dict | None:
        """按表单编号查一行（返回含 `记录ID` 的字典）；找不到返回 None。

        依次尝试：单号日期当天 → 当月。都查不到说明该单据不在库（可能已归档）。
        """
        target = (bdbh or "").strip()
        if not target:
            return None

        windows = date_windows_for_bdbh(target)
        if not windows:
            raise ParseError(f"无法从 {target!r} 推断日期，请改用 --id 或 --where")

        for from_date, to_date in windows:
            rows, _headers, _page = self.collect(
                from_date=from_date, to_date=to_date, key=target, field="表单编号",
                page_size=page_size)
            for row in rows:
                if (row.get("表单编号") or "").strip().upper() == target.upper():
                    return row
        return None

    def run(self, *, from_date: str | None = None, to_date: str | None = None,
            key: str = "", field: str = "", where: str | None = None,
            customer: str | None = None, sales: str | None = None,
            status: str | None = None, columns: str | None = None,
            out: str | None = None, jsonl: str | None = None,
            limit: int | None = None, page_size: int | None = None,
            max_pages: int = MAX_PAGES_GUARD, dry_run: bool = False) -> Iterator[dict]:
        rows, headers, page = self.collect(
            from_date=from_date, to_date=to_date, key=key, field=field, where=where,
            customer=customer, sales=sales, status=status, limit=limit,
            page_size=page_size, max_pages=max_pages)

        use_columns = self._pick_columns(columns, headers)
        if dry_run:
            self._log(f"[DRY-RUN] 命中 {len(rows)} 条；将导出列：{', '.join(use_columns)}")
            for row in rows[:5]:
                self._log("   样例：" + " | ".join(f"{c}={row.get(c, '')}" for c in use_columns[:6]))
            yield from rows
            return

        if out:
            path = Path(out)
            if path.suffix.lower() in (".jsonl", ".ndjson"):
                self._write_jsonl(path, rows)
            else:
                self._write_csv(path, rows, use_columns)
        if jsonl:
            self._write_jsonl(Path(jsonl), rows)

        if not rows:
            self._log("[WARN] 没有符合条件的记录")
        elif out or jsonl:
            self._log(f"[DONE] {len(rows)} 条" + (f" → {out}" if out else ""))
        else:
            self._preview(rows, use_columns)
            self._log(f"[DONE] {len(rows)} 条（未指定 --out，仅预览前 {PREVIEW_ROWS} 行）")
        yield from rows

    # ══════════════════ 输出 ══════════════════
    @staticmethod
    def _preview(rows: list[dict], columns: list[str], limit: int = PREVIEW_ROWS) -> None:
        from ...output.markdown import to_markdown_table

        preferred = ["表单编号", "客户名称", "业务员", "产品名称", "报价状态", "制单日期"]
        picked = [c for c in preferred if c in columns] or columns[:6]
        table = to_markdown_table(picked, [[r.get(c) for c in picked] for r in rows[:limit]],
                                  max_col_width=24)
        print(table)

    @staticmethod
    def _pick_columns(spec: str | None, headers: list[str]) -> list[str]:
        available = [h for h in headers if h not in ACTION_COLUMNS]
        if not spec:
            extra = ["记录ID"] if "记录ID" not in available else []
            return available + extra

        picked: list[str] = []
        for raw in spec.replace("，", ",").split(","):
            name = raw.strip()
            if not name:
                continue
            if name not in available and name != "记录ID":
                raise ParseError(f"没有列 {name!r}；可用：{', '.join(available)}")
            picked.append(name)
        return picked or available

    @staticmethod
    def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
        from ...output.csv import CsvSink

        sink = CsvSink(path, columns)
        try:
            for row in rows:
                sink.write(row)
        finally:
            sink.close()

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict]) -> None:
        from ...output.jsonl import JsonlSink

        sink = JsonlSink(path, key=None, resume=False)
        try:
            for row in rows:
                sink.write(row)
        finally:
            sink.close()

    @staticmethod
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)
