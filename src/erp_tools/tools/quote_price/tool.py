"""客户报价【列表】查询（BaoJia/BJ_KHBJ/Index.aspx）。

与「客户报价需求」列表（BJ_KHBJXQ）的区别：
  * 本页一行是**一个报价产品行**（56 列），同一表单编号会有多行（`-01`、`-02`…）；
  * 服务端过滤更丰富：日期字段可选（制单/交货/离厂）、审批状态、报废状态、
    确认情况、签订单、报价分类、客户属性、模板…都是服务端 `<select>`；
  * 关键字仍是两套 `txtKey/DropField` + `txtKey1/DropField1`（各 28 个字段）。

实测（2026-11，QXID=860）：
  * `txtEvenPageDataSize=500` 能一页取全（89 条 → `lblPageCount=1`）；
  * 翻页按钮是 `<a href="javascript:__doPostBack('btnNext','')">`，与需求列表页一致；
  * 下拉的值有的就是显示文字（`txtCheck`=全部/未全审批），有的是代码
    （`txtZDorJH`=ZDRQ/KHJHRQ/LCRQ、`txt_SFBF`=0/1/2），所以统一走
    「先 GET 页面拿真实选项 → 用 label 或 code 反查提交值」，不做硬编码。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterator

from ...core.errors import ParseError, UpstreamError
from ..base import Option, Tool
from ..gridlist import (
    button_payload,
    field_codes,
    form_snapshot,
    resolve_select_value,
    select_choices,
)
from ..quote_index.filters import Condition, apply_conditions, parse_where
from ..quote_index.tool import MAX_PAGES_GUARD, PREVIEW_ROWS, default_date_range
from .detail_parser import parse_price_detail
from .parser import (
    ADD_PAGE_PATH,
    DEFAULT_QXID,
    PAGE_PATH,
    PriceListPage,
    parse_price_list_page,
)

#: 日期区间作用在哪个日期字段上：中文名 -> 提交值
DATE_FIELDS = {
    "制单日期": "ZDRQ",
    "交货日期": "KHJHRQ",
    "离厂日期": "LCRQ",
}

#: 常用下拉的「友好别名 -> 控件名」，`filters` 参数里可以用中文键
SELECT_ALIASES = {
    "审批": "txtCheck",
    "审批状态": "txtCheck",
    "报废": "txt_SFBF",
    "报废状态": "txt_SFBF",
    "确认": "txtQDQK",
    "确认情况": "txtQDQK",
    "签订单": "txtXKHDD",
    "合同": "txtXKHDD",
    "查询方式": "txtFXCX",
    "客户属性": "txtKHSX",
    "模板": "txtMBF",
    "预计报价月份": "txtBetRQ",
    "报价分类": "txtCFWZ",
}

#: 服务端支持过滤的控件（`filters` 的合法键）
FILTER_CONTROLS = (
    "txtCheck", "txt_SFBF", "txtQDQK", "txtXKHDD", "txtFXCX",
    "txtKHSX", "txtMBF", "txtBetRQ", "txtCFWZ",
)


class PriceListTool(Tool):
    name = "price"
    summary = "查询客户报价列表（BJ_KHBJ/Index.aspx，一行=一个报价产品行）→ 表格 / CSV / JSONL"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("from_date", "日期区间起（默认本月 1 号）", cli="--from"),
            Option("to_date", "日期区间止（默认今天）", cli="--to"),
            Option("date_type", "日期区间作用于哪个字段：制单日期/交货日期/离厂日期",
                   default="制单日期", cli="--date-type"),
            Option("key", "服务端关键字（配合 --field）", cli="--key"),
            Option("field", "关键字字段中文名，如 客户名称 / 表单编号 / 产品名称", cli="--field"),
            Option("key1", "第二个服务端关键字（配合 --field1）", cli="--key1"),
            Option("field1", "第二个关键字字段中文名", cli="--field1"),
            Option("where", "本地过滤，逗号分隔：客户名称~ADG,报价状态=已审批", cli="--where"),
            Option("customer", "客户名称包含（等价 --where 客户名称~X）", cli="--customer"),
            Option("sales", "业务员包含（等价 --where 业务员~X）", cli="--sales"),
            Option("status", "报价状态包含（如 编辑中 / 已审批）", cli="--status"),
            Option("filters", "服务端下拉过滤，如 审批状态=已全审批,报废状态=未报废", cli="--filter"),
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
        url = f"/Admin/{PAGE_PATH}?QXID={qxid}&MenuTree=1"
        return f"{url}&LoginUserID={uid}" if uid else url

    def qxid(self) -> str:
        return self.cfg.qxid_price_index or self.auth.qxid_for(PAGE_PATH)

    # ══════════════════ 过滤参数 ══════════════════
    @staticmethod
    def parse_filters(filters) -> dict[str, str]:
        """把 `filters` 归一成 {控件名: 用户给的值}。

        允许三种写法：`{"审批状态": "已全审批"}`、`{"txtCheck": "已全审批"}`、
        或者列表 `["审批状态=已全审批", "报废状态=未报废"]`。
        """
        if not filters:
            return {}
        items: list[tuple[str, str]] = []
        if isinstance(filters, dict):
            items = [(str(k), str(v)) for k, v in filters.items()]
        else:
            for raw in filters:
                key, sep, value = str(raw).partition("=")
                if not sep:
                    raise ParseError(f"过滤条件 {raw!r} 缺少 '='，形如 审批状态=已全审批")
                items.append((key.strip(), value.strip()))

        out: dict[str, str] = {}
        for key, value in items:
            key = key.strip()
            control = key if key in FILTER_CONTROLS else SELECT_ALIASES.get(key)
            if not control:
                raise ParseError(
                    f"不支持的过滤条件 {key!r}；可用："
                    + ", ".join(sorted(SELECT_ALIASES))
                    + " 或控件名 " + ", ".join(FILTER_CONTROLS)
                )
            out[control] = value
        return out

    def _apply_filters(self, body: dict[str, str], html: str,
                       filters: dict[str, str]) -> None:
        for control, wanted in filters.items():
            body[control] = resolve_select_value(html, control, wanted)

    # ══════════════════ 查询 ══════════════════
    def _search(self, qxid: str, *, from_date: str, to_date: str, page_size: int,
                date_type: str = "", key: str = "", field: str = "",
                key1: str = "", field1: str = "",
                filters: dict[str, str] | None = None) -> tuple[PriceListPage, str]:
        url = self.page_url(qxid)
        resp = self.client.get(url)
        if resp.status_code != 200:
            raise UpstreamError(f"列表页打开失败（HTTP {resp.status_code}）",
                                resp.status_code)
        if "__VIEWSTATE" not in resp.text:
            raise ParseError("列表页没有 __VIEWSTATE，可能被重定向到登录页")
        html = resp.text

        body = form_snapshot(html)
        body.update({
            "txtKSRQ": from_date,
            "txtJZRQ": to_date,
            "txtEvenPageDataSize": str(page_size),
        })
        # 本页的「搜索」是 <input type="submit" name="btn_Search" value="搜索">，
        # 必须把 name/value 一起发回去，否则 ERP 走默认绑定（条件全被忽略）。
        body.update(button_payload(html, "btn_Search"))
        if date_type:
            body["txtZDorJH"] = resolve_select_value(html, "txtZDorJH", date_type)
        for control, value, which in (("txtKey", key, "DropField"),
                                      ("txtKey1", key1, "DropField1")):
            if not value:
                continue
            body[control] = value
        for which, field in (("DropField", field), ("DropField1", field1)):
            if not field:
                continue
            codes = field_codes(html, which)
            if field in codes:
                body[which] = codes[field]
            elif field in codes.values():
                body[which] = field
            else:
                raise ParseError(
                    f"未知关键字字段 {field!r}；可用：{', '.join(codes) or '(页面无此下拉)'}")
        self._apply_filters(body, html, filters or {})

        resp = self.client.post(url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(f"查询失败（HTTP {resp.status_code}）",
                                resp.status_code, resp.text[:300])
        page = parse_price_list_page(resp.text)
        if not page.headers:
            raise ParseError("查询结果里没有表格，页面结构可能变了")
        return page, resp.text

    def _next_page(self, qxid: str, current_html: str, *, from_date: str, to_date: str,
                   page_size: int, date_type: str = "",
                   filters: dict[str, str] | None = None) -> tuple[PriceListPage, str]:
        url = self.page_url(qxid)
        body = form_snapshot(current_html)
        body.update({
            "txtKSRQ": from_date,
            "txtJZRQ": to_date,
            "txtEvenPageDataSize": str(page_size),
            "__EVENTTARGET": "btnNext",
            "__EVENTARGUMENT": "",
        })
        if date_type:
            body["txtZDorJH"] = resolve_select_value(current_html, "txtZDorJH", date_type)
        self._apply_filters(body, current_html, filters or {})

        resp = self.client.post(url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(
                f"翻页被服务端拒绝（HTTP {resp.status_code}）；可调大 --page-size 一次取全",
                resp.status_code, resp.text[:300])
        page = parse_price_list_page(resp.text)
        if not page.headers:
            raise UpstreamError("翻页返回的不是列表表格，翻页协议可能变了")
        return page, resp.text

    def collect(self, *, from_date: str | None = None, to_date: str | None = None,
                date_type: str = "制单日期", key: str = "", field: str = "",
                key1: str = "", field1: str = "", where: str | None = None,
                customer: str | None = None, sales: str | None = None,
                status: str | None = None, filters=None,
                limit: int | None = None, page_size: int | None = None,
                max_pages: int = MAX_PAGES_GUARD
                ) -> tuple[list[dict], list[str], PriceListPage]:
        """执行查询并返回 (记录, 可用列, 首页分页信息)。"""
        d_from, d_to = default_date_range()
        from_date = from_date or d_from
        to_date = to_date or d_to
        size = int(page_size or self.cfg.page_size_max)
        qxid = self.qxid()
        extra = self.parse_filters(filters)

        conditions: list[Condition] = list(parse_where(where))
        if customer:
            conditions.append(Condition("客户名称", "~", customer))
        if sales:
            conditions.append(Condition("业务员", "~", sales))
        if status:
            conditions.append(Condition("报价状态", "~", status))

        self._log(f"[INFO] {date_type} {from_date} ~ {to_date}  页大小={size}  QXID={qxid}"
                  + (f"  过滤={extra}" if extra else ""))
        page, html = self._search(qxid, from_date=from_date, to_date=to_date,
                                  page_size=size, date_type=date_type, key=key,
                                  field=field, key1=key1, field1=field1, filters=extra)
        self._log(f"[INFO] 服务端命中 {page.total} 行 / {page.pages} 页")

        headers = list(page.headers)
        rows: list[dict] = list(page.rows)
        hop = 0
        while page.has_more_pages:
            hop += 1
            if hop > max_pages:
                raise UpstreamError(f"翻页超过 {max_pages} 页，已停止（可调大 --max-pages）")
            page, html = self._next_page(qxid, html, from_date=from_date,
                                         to_date=to_date, page_size=size,
                                         date_type=date_type, filters=extra)
            rows.extend(page.rows)
            self._log(f"[INFO] 第 {page.page}/{page.pages} 页累计 {len(rows)} 行")

        rows = apply_conditions(rows, conditions, headers + ["记录ID"])
        if conditions:
            self._log(f"[INFO] 本地条件过滤后 {len(rows)} 行")
        if limit:
            rows = rows[: int(limit)]
        return rows, headers, page

    # ══════════════════ 主流程 ══════════════════
    def find_by_bdbh(self, bdbh: str, *, page_size: int | None = None) -> list[dict]:
        """按表单编号取该单的**所有产品行**（一个编号可能有多行 `-01/-02`）。

        单号自带制单日期（KHBJ + YYMMDD + 流水），所以先按当天、再按当月查。
        """
        target = (bdbh or "").strip()
        if not target:
            return []

        return self._search_bdbh(target, page_size=page_size, prefix=False)

    def find_by_bdbh_prefix(self, prefix: str, *,
                            page_size: int | None = None) -> list[dict]:
        """按表单编号**前缀**取（如 `KHBJ261112003` 命中 `-01`、`-02` 各行）。"""
        target = (prefix or "").strip()
        if not target:
            return []
        return self._search_bdbh(target, page_size=page_size, prefix=True)

    def _search_bdbh(self, target: str, *, page_size: int | None,
                     prefix: bool) -> list[dict]:
        """按单号查行：先试单号里编码的日期，再退到本月、近一年。

        `KHBJ` + YYMMDD 看着像制单日期，但**实测有不一致的**（如 `KHBJ261016002-01`
        的制单日期在 11 月），只按单号推日期会查不到 → 加两个兜底窗口。
        """
        from ..quote_index import date_windows_for_bdbh

        windows = date_windows_for_bdbh(target)
        windows.append(default_date_range())
        today = date.today()
        windows.append(((today - timedelta(days=365)).isoformat(), today.isoformat()))

        seen: set[tuple[str, str]] = set()
        upper = target.upper()
        for from_date, to_date in windows:
            if (from_date, to_date) in seen:
                continue
            seen.add((from_date, to_date))
            rows, _headers, _page = self.collect(
                from_date=from_date, to_date=to_date, key=target, field="表单编号",
                date_type="制单日期", page_size=page_size)
            if prefix:
                hits = [row for row in rows
                        if (row.get("表单编号") or "").strip().upper().startswith(upper)]
            else:
                hits = [row for row in rows
                        if (row.get("表单编号") or "").strip().upper() == upper]
            if hits:
                return hits
        return []

    def run(self, *, from_date: str | None = None, to_date: str | None = None,
            date_type: str = "制单日期", key: str = "", field: str = "",
            key1: str = "", field1: str = "", where: str | None = None,
            customer: str | None = None, sales: str | None = None,
            status: str | None = None, filters=None, columns: str | None = None,
            out: str | None = None, jsonl: str | None = None,
            limit: int | None = None, page_size: int | None = None,
            max_pages: int = MAX_PAGES_GUARD, dry_run: bool = False) -> Iterator[dict]:
        rows, headers, _page = self.collect(
            from_date=from_date, to_date=to_date, date_type=date_type, key=key,
            field=field, key1=key1, field1=field1, where=where, customer=customer,
            sales=sales, status=status, filters=filters, limit=limit,
            page_size=page_size, max_pages=max_pages)
        yield from self._emit(rows, headers, columns=columns, out=out, jsonl=jsonl,
                              dry_run=dry_run)

    # ══════════════════ 输出 ══════════════════
    @staticmethod
    def _pick_columns(spec: str | None, headers: list[str]) -> list[str]:
        from ..gridlist import ACTION_COLUMNS

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

    def _emit(self, rows: list[dict], headers: list[str], *, columns: str | None = None,
              out: str | None = None, jsonl: str | None = None,
              dry_run: bool = False) -> Iterator[dict]:
        from pathlib import Path

        use_columns = self._pick_columns(columns, headers)
        if dry_run:
            self._log(f"[DRY-RUN] 命中 {len(rows)} 行；将导出列：{', '.join(use_columns)}")
            for row in rows[:5]:
                self._log("   样例：" + " | ".join(
                    f"{c}={row.get(c, '')}" for c in use_columns[:6]))
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
            self._log(f"[DONE] {len(rows)} 行" + (f" → {out}" if out else ""))
        else:
            self._preview(rows, use_columns)
            self._log(f"[DONE] {len(rows)} 行（未指定 --out，仅预览前 {PREVIEW_ROWS} 行）")
        yield from rows

    @staticmethod
    def _preview(rows: list[dict], columns: list[str],
                 limit: int = PREVIEW_ROWS) -> None:
        from ...output.markdown import to_markdown_table

        preferred = ["表单编号", "客户名称", "产品名称", "报价状态", "业务员", "制单日期"]
        picked = [c for c in preferred if c in columns] or columns[:6]
        table = to_markdown_table(picked, [[r.get(c) for c in picked] for r in rows[:limit]],
                                  max_col_width=24)
        print(table)

    @staticmethod
    def _write_csv(path, rows: list[dict], columns: list[str]) -> None:
        from ...output.csv import CsvSink

        sink = CsvSink(path, columns)
        try:
            for row in rows:
                sink.write(row)
        finally:
            sink.close()

    @staticmethod
    def _write_jsonl(path, rows: list[dict]) -> None:
        from ...output.jsonl import JsonlSink

        sink = JsonlSink(path, key=None, resume=False)
        try:
            for row in rows:
                sink.write(row)
        finally:
            sink.close()

    @staticmethod
    def _log(msg: str) -> None:
        import sys

        print(msg, file=sys.stderr, flush=True)


class PriceDetailTool(Tool):
    """客户报价【详情】：按记录 ID（或表单编号 + 行号）取一个报价产品行的完整信息。"""

    name = "price-detail"
    summary = "客户报价【详情】（BJ_KHBJ/Add.aspx，一个报价产品行）→ JSON / JSONL"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("record_id", "记录 ID（列表页的 记录ID / GUID）", cli="--id"),
            Option("bdbh", "表单编号，如 KHBJ261112003-01（可只给 -01 前缀）", cli="--bdbh"),
            Option("row", "配合 --bdbh：取第几行（1 起）；不给且命中多行会报错", cli="--row"),
            Option("out", "输出 JSON / JSONL 路径", cli="--out"),
            Option("no_description", "不输出产品描述长文本", cli="--no-description"),
        ]

    # ══════════════════ 页面 ══════════════════
    def qxid(self) -> str:
        return self.cfg.qxid_price or self.auth.qxid_for(ADD_PAGE_PATH)

    def page_url(self, qxid: str, record_id: str) -> str:
        uid = self.auth.user_id or ""
        url = f"/Admin/{ADD_PAGE_PATH}?QXID={qxid}&MenuTree=1&id={record_id}&Option=3"
        return f"{url}&LoginUserID={uid}" if uid else url

    # ══════════════════ 取详情 ══════════════════
    #: 详情页里记录自己的 ID（`<input name="lblID" value="<记录ID>">`）
    _LBL_ID_RE = re.compile(r'name="lblID"[^>]*?value="([0-9a-fA-F-]{36})"')

    @classmethod
    def record_id_of(cls, html: str) -> str:
        m = cls._LBL_ID_RE.search(html)
        return (m.group(1) if m else "").lower()

    def open_record(self, record_id: str) -> tuple[str, str]:
        """GET 详情页；发现 ERP 回的是**别的记录**时重试一次。

        ERP 的表单页带“当前打开记录”这一层会话状态：同一 Cookie 下刚看过 A，
        紧接着按 `id=B` GET，偶尔仍会返回 A 的页面（实测约 1/10）。
        因此这里用页面内的 `lblID` 自校验，不一致就再 GET 一次。
        """
        qxid = self.qxid()
        url = self.page_url(qxid, record_id)
        want = (record_id or "").strip().lower()
        last = ""
        for _attempt in range(2):
            resp = self.client.get(url)
            if resp.status_code != 200:
                raise UpstreamError(f"详情页打开失败（HTTP {resp.status_code}）",
                                    resp.status_code)
            if "__VIEWSTATE" not in resp.text:
                raise ParseError("详情页没有 __VIEWSTATE，可能被重定向到登录页")
            actual = self.record_id_of(resp.text)
            if not actual or actual == want:
                return resp.text, url
            last = actual
            self._log(f"[WARN] 详情页返回了别的记录（{last}），重试一次")
        raise UpstreamError(f"详情页取不到指定的记录：请求 {want}，页面里是 {last}")

    def fetch_one(self, record_id: str, *, include_description: bool = True) -> dict:
        html, url = self.open_record(record_id)
        detail = parse_price_detail(html, record_id=record_id)
        data = detail.to_dict()
        data["url"] = url
        if not include_description:
            data["description"] = ""
            data["description_chars"] = len(detail.description)
        return data

    def resolve_records(self, *, record_id: str | None = None, bdbh: str | None = None,
                        row: int | None = None) -> list[dict]:
        """把 record_id / bdbh(+row) 解析成列表页的行（含 `记录ID`）。"""
        if record_id:
            return [{"记录ID": record_id.strip()}]
        target = (bdbh or "").strip()
        if not target:
            raise ParseError("record_id 与 bdbh 至少给一个")

        rows = PriceListTool(self.auth).find_by_bdbh(target)
        if not rows:
            # 用户可能给的是不带行号的单号前缀
            rows = PriceListTool(self.auth).find_by_bdbh_prefix(target)
        if not rows:
            raise ParseError(f"表单编号 {target} 查不到（可能已归档，或当前账号无权限）")
        if row is not None:
            index = int(row) - 1
            if index < 0 or index >= len(rows):
                raise ParseError(f"row={row} 超出范围：该单共 {len(rows)} 行")
            return [rows[index]]
        return rows

    def run(self, *, record_id: str | None = None, bdbh: str | None = None,
            row: int | None = None, out: str | None = None,
            no_description: bool = False) -> Iterator[dict]:
        import json
        from pathlib import Path

        rows = self.resolve_records(record_id=record_id, bdbh=bdbh, row=row)
        collected: list[dict] = []
        for item in rows:
            rid = item.get("记录ID") or ""
            if not rid:
                continue
            self._log(f"[INFO] {item.get('表单编号', '')} {rid}")
            collected.append(self.fetch_one(rid, include_description=not no_description))

        if out:
            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() in (".jsonl", ".ndjson"):
                with path.open("w", encoding="utf-8") as fh:
                    for record in collected:
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            else:
                path.write_text(json.dumps(collected, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            self._log(f"[DONE] {len(collected)} 条 → {out}")
        yield from collected

    @staticmethod
    def _log(msg: str) -> None:
        import sys

        print(msg, file=sys.stderr, flush=True)



#: 客户报价子数据节点目录：node → (回发按钮, 子页, 中文说明)
NODE_CATALOG: dict[str, tuple[str, str, str]] = {
    "quantities": ("btnSelectCPSLZL", "Add_CPSL.aspx", "数量档（数量/单价/金额/折扣率/折后价）"),
    "part": ("btnSelectBJZL", "Add_BJZL.aspx", "单个部件印刷资料"),
    "part_materials": ("btnSelectBJFL", "Add_BJFL.aspx", "部件已选辅料"),
    "part_process": ("btnSelectBJGXZL", "Add_BJGXZL.aspx", "部件工序（27 列）"),
    "part_machines": ("btnSelectBJJTZL", "Add_BJJTZL.aspx", "部件机台（30 列 + 机台参数）"),
    "product_check": ("btnSelectCPSHZL", "Add_CPSHZL.aspx", "送货/审核资料（28 列）"),
    "fees": ("btnBrowFXMX", "Brow_FXMX.aspx", "每个数量档的费项明细"),
    "other_fees": ("btnSelectQTFX", "Add_QTFX.aspx", "其他费用项目"),
    "cp": ("btnSelectCPZL", "Add_CPZL.aspx", "产品资料（整张产品表单）"),
    "cp_process": ("btnSelectCPGXZL", "Add_CPGXZL.aspx", "产品级工序（29 列）"),
    "cp_materials": ("btnSelectCPFL", "Add_CPFL.aspx", "产品级辅料（8 列）"),
    "feedback": ("btnSelectKHBJXQFK", "Add_KHBJXQFK.aspx", "客户报价需求反馈正文"),
    "sgkb": ("BtnOpenSGKBT", "Add_SGKB.aspx", "手工开版资料附件"),
    "split_price": ("btn_CPJEXSMS", "KHBJ_SetJEXSMS.aspx", "拆分价格设置（两棵分配树）"),
}

#: 需要部件 ID 的节点
NODE_NEEDS_BJID = frozenset({"part", "part_materials", "part_process",
                             "part_machines", "product_check"})
#: 需要数量档 ID 的节点（`-1` 表示汇总）
NODE_NEEDS_CPSLID = frozenset({"fees", "other_fees"})

#: 中文/简称别名 → node
NODE_ALIASES: dict[str, str] = {
    "数量": "quantities", "数量档": "quantities", "数量明细": "quantities",
    "部件": "part", "部件资料": "part",
    "部件辅料": "part_materials", "辅料": "part_materials", "已选材料": "part_materials",
    "费项": "fees", "费项明细": "fees", "成本明细": "fees",
    "其他费项": "other_fees", "其他费用": "other_fees",
    "产品": "cp", "产品资料": "cp",
    "产品工序": "cp_process", "工序": "cp_process",
    "产品辅料": "cp_materials",
    "部件工序": "part_process",
    "部件机台": "part_machines", "机台": "part_machines",
    "反馈": "feedback", "需求反馈": "feedback",
    "开版": "sgkb", "手工开版": "sgkb",
    "拆分价格": "split_price", "单价模式设置": "split_price",
    "送货资料": "product_check", "审核资料": "product_check", "产品审核": "product_check",
}

#: 面板中文名 → PANEL_TARGETS 的键
PANEL_ALIASES: dict[str, str] = {
    "产品描述": "description", "材料清单": "material_list",
    "手工工序": "manual_process", "修改日志": "change_log",
    "工序清单": "process_list", "部件列表": "part_list",
    "运费清单": "freight", "单价模式": "price_mode",
}


class PriceNodeTool(Tool):
    """客户报价详情页里「点开才有的子数据」。

    子页不能直接 GET，必须先向 `Add.aspx` 发一次 POST：把 GUID 写进 `lblSelect*`
    隐藏字段 + 回发对应的 `btnSelect*` 按钮，响应里的 `showModalDialog('xxx.aspx?...')`
    才是真正的子页 URL。

    另外 8 个「折叠面板」是整页回发：`__EVENTTARGET=Lbtn_Xxx`，内容渲染在
    `div#Pan_gridView*` 里（见 `node_parser.PANEL_TARGETS`）。
    """

    name = "price-node"
    summary = "客户报价子数据（数量档/部件/工序/辅料/费项/反馈/开版/面板…）→ JSON"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("record_id", "记录 ID（列表的 记录ID）", cli="--id"),
            Option("node", "子数据节点；list=列出可用节点；all=全部拉一遍", cli="--node"),
            Option("cpid", "产品明细 ID；不传用详情页里的值", cli="--cpid"),
            Option("bjid", "部件 ID；部件级节点用，不传给第一个部件", cli="--bjid"),
            Option("index", "取第几个部件（1 起），配合部件级节点", cli="--index"),
            Option("cpslid", "数量档 ID（默认：fees=-1 汇总、other_fees=第一档）", cli="--cpslid"),
            Option("out", "输出 JSON 路径", cli="--out"),
        ]

    # ══════════════════ 节点名归一 ══════════════════
    @staticmethod
    def resolve_node(name: str) -> tuple[str, str]:
        """节点名 → (kind, key)。kind ∈ {'sub', 'panel', 'all', 'list'}。"""
        from .node_parser import PANEL_TARGETS

        raw = (name or "").strip()
        if not raw:
            raise ParseError("node 必填；可用 node=list 查看全部节点")
        low = raw.lower()
        if low in ("list", "ls", "nodes"):
            return "list", ""
        if low in ("all", "全部", "整棵树"):
            return "all", ""

        # 折叠面板：panel:xxx / panel_xxx / Lbtn_Xxx / 中文名
        panel_key = ""
        for prefix in ("panel:", "面板:", "panel_"):
            if raw.startswith(prefix):
                panel_key = raw[len(prefix):]
                break
        if panel_key:
            if panel_key in PANEL_TARGETS:
                return "panel", PANEL_TARGETS[panel_key]
            raise ParseError(f"未知面板 {raw!r}；可用：{', '.join(PANEL_TARGETS)}")
        if low in PANEL_TARGETS:
            return "panel", PANEL_TARGETS[low]
        by_target = {target.upper(): target for target in PANEL_TARGETS.values()}
        if raw.upper() in by_target:
            return "panel", by_target[raw.upper()]
        if raw in PANEL_ALIASES:
            return "panel", PANEL_TARGETS[PANEL_ALIASES[raw]]

        node = low if low in NODE_CATALOG else NODE_ALIASES.get(raw, "")
        if not node:
            known = " / ".join(NODE_CATALOG)
            raise ParseError(f"未知节点 {raw!r}；可用：{known}；面板用 panel:<名称>")
        return "sub", node

    @staticmethod
    def catalog() -> list[dict]:
        """列出全部可用节点（含面板），给 `node=list` 和 MCP 文档用。"""
        from .node_parser import PANEL_TARGETS

        rows = [{"node": key, "kind": "sub", "page": page, "description": desc}
                for key, (_button, page, desc) in NODE_CATALOG.items()]
        rows += [{"node": f"panel:{key}", "kind": "panel", "page": target,
                  "description": f"折叠面板 {target}"}
                 for key, target in PANEL_TARGETS.items()]
        return rows

    # ══════════════════ 父页 ══════════════════
    def _parent(self, record_id: str):
        """GET 一次详情页，解析出 cpid / 第一个 bjid / 第一个 cpslid。"""
        from .detail_parser import parse_price_detail

        html, url = PriceDetailTool(self.auth).open_record(record_id)
        detail = parse_price_detail(html, record_id=record_id)
        return html, url, detail

    @staticmethod
    def _ctx(record_id: str, detail, *, cpid: str = "", bjid: str = "",
             cpslid: str = "") -> dict[str, str]:
        parts = [p for p in getattr(detail, "parts", []) if p.get("bjid")]
        quants = [q for q in getattr(detail, "quantities", []) if q.get("cpslid")]
        return {
            "record_id": record_id,
            "cpid": cpid or detail.cpid,
            "bjid": bjid or (parts[0]["bjid"] if parts else ""),
            "cpslid": cpslid or (quants[0]["cpslid"] if quants else "-1"),
        }

    @staticmethod
    def _node_fields(node: str, ctx: dict[str, str]) -> dict[str, str]:
        """每个子页要回发的 `lblSelect*` 隐藏字段（照抄页面里 Open* 函数的入参）。"""
        rid, cpid, bjid, cpslid = (ctx["record_id"], ctx["cpid"],
                                   ctx["bjid"], ctx["cpslid"])
        if node == "quantities":            # OpenInputCPSLZLData('-1', PID, CPID)
            return {"lblSelectCPSLZL": "-1", "lblSelectCPZLPID": rid,
                    "lblSelectCPZLID": cpid}
        if node == "part":                  # OpenInputBJZLData(ID, PID)
            return {"lblSelectBJZLID": bjid, "lblSelectBJZLPID": rid}
        if node == "part_materials":        # OpenInputBJFLData(PID, BJID)
            return {"lblSelectCPZLPID": rid, "lblSelectBJZLID": bjid}
        if node == "part_process":          # OpenInputBJGXZLData(PID, BJID, GXLB, TYSC, ZSJID)
            return {"lblSelectCPZLPID": rid, "lblSelectBJZLID": bjid,
                    "lblSelectBJGXLB": "", "txtTYSC": "0", "lblZSJID": ""}
        if node == "part_machines":         # OpenInputBJJTZLData(PID, BJID)
            return {"lblSelectCPZLPID": rid, "lblSelectBJZLID": bjid}
        if node == "product_check":         # OpenInputCPSHZLData(ID, CPID, PID)
            return {"lblSelectBJZLID": bjid, "lblSelectCPZLID": cpid,
                    "lblSelectCPZLPID": rid}
        if node == "fees":                  # OpenBrowFXMX(PID, CPID, TZZLID, CPSLID, CPSL)
            return {"lblSelectCPZLPID": rid, "lblSelectCPZLID": cpid,
                    "lblSelectTZCPZLID": "-1", "lblSelectCPSLZL": "-1",
                    "lblSelectBJZLID": "1000"}
        if node == "other_fees":            # OpenInputQTFXData(ID(=CPSLID), PID, CPID)
            return {"lblSelectCPSLZL": cpslid, "lblSelectCPZLPID": rid,
                    "lblSelectCPZLID": cpid}
        if node == "cp":                    # OpenInputCPZLData(ID, PID, '0')
            return {"lblSelectCPZLID": cpid, "lblSelectCPZLPID": rid}
        if node == "cp_process":            # OpenInputCPGXZLData(CPID, PID, ZSJID)
            return {"lblSelectCPZLID": cpid, "lblSelectCPZLPID": rid, "lblZSJID": ""}
        if node == "cp_materials":          # OpenInputCPFLData(CPID, PID)
            return {"lblSelectCPZLID": cpid, "lblSelectCPZLPID": rid}
        if node == "feedback":              # OpenInputKHBJXQFKData(PID, CPID)
            return {"lblSelectCPZLPID": rid, "lblSelectCPZLID": cpid}
        if node == "sgkb":                  # OpenSGKBT(CPID, PID)
            return {"lblSelectCPZLPID": rid, "lblSelectCPZLID": cpid}
        if node == "split_price":           # OpenSetCPJEXSMS(ID)
            return {"lblSelectCPZLID": cpid}
        raise ParseError(f"未知节点 {node!r}")

    def _sub_page(self, parent_html: str, parent_url: str, node: str,
                  ctx: dict[str, str]) -> tuple[str, str]:
        """回发按钮 → 取 showModalDialog 里的子页 URL → GET。"""
        button = NODE_CATALOG[node][0]
        body = form_snapshot(parent_html)
        body.update(self._node_fields(node, ctx))
        body[button] = ""
        resp = self.client.post(parent_url, data=body)
        m = re.search(r"showModalDialog\(\s*'([^']+)'", resp.text)
        if m is None:
            raise UpstreamError(f"{node} 子页没有弹出（页面结构可能变了，"
                                f"或该行没有这项数据）", resp.status_code)
        sub = m.group(1).replace("&amp;", "&")
        for prefix in ("/Admin/BaoJia/BJ_KHBJ/", "/Admin/"):
            page = self.client.get(prefix + sub)
            if page.status_code == 200 and "__VIEWSTATE" in page.text:
                return page.text, self.client.url(prefix + sub)
        raise UpstreamError(f"{node} 子页打不开：{sub[:120]}")

    def _panel_page(self, parent_html: str, parent_url: str, target: str) -> str:
        """折叠面板：整页回发（`__EVENTTARGET`），返回整页 HTML。"""
        body = form_snapshot(parent_html)
        body.update({"__EVENTTARGET": target, "__EVENTARGUMENT": ""})
        resp = self.client.post(parent_url, data=body)
        if resp.status_code != 200 or "__VIEWSTATE" not in resp.text:
            raise UpstreamError(f"面板 {target} 回发失败（HTTP {resp.status_code}）",
                                resp.status_code)
        return resp.text

    # ══════════════════ 取数据 ══════════════════
    @staticmethod
    def _run_parser(node: str, html: str) -> dict:
        from . import node_parser

        payload = node_parser.NODE_PARSERS[node](html)
        if isinstance(payload, list):
            return {"records": payload, "count": len(payload)}
        result = dict(payload)
        if isinstance(result.get("records"), list):
            result.setdefault("count", len(result["records"]))
        return result

    def fetch(self, record_id: str, node: str, *, cpid: str = "", bjid: str = "",
              cpslid: str = "") -> dict:
        from .detail_parser import parse_price_detail

        kind, key = self.resolve_node(node)
        if kind == "list":
            rows = self.catalog()
            return {"nodes": rows, "count": len(rows)}
        if kind == "all":
            return self.fetch_all(record_id, cpid=cpid, bjid=bjid, cpslid=cpslid)

        parent_html, parent_url, detail = self._parent(record_id)
        ctx = self._ctx(record_id, detail, cpid=cpid, bjid=bjid, cpslid=cpslid)
        if kind == "panel":
            from .node_parser import parse_panel_node

            page = self._panel_page(parent_html, parent_url, key)
            data = parse_panel_node(page)
            return {"node": f"panel:{key}", "target": key, **data,
                    "count": len(data["grids"])}

        sub_html, page_url = self._sub_page(parent_html, parent_url, key, ctx)
        result = self._run_parser(key, sub_html)
        if key == "part":
            # 部件页是个**空白编辑表单**（只回 txtID），真实值在父页的部件明细表里
            row = next((p for p in detail.parts if p.get("bjid") == ctx["bjid"]), {})
            result["detail_row"] = row
            if not result.get("fields", {}).get("部件名称"):
                result["note"] = ("部件页在只读态不渲染字段值（只回 txtID）；"
                                  "部件内容请看 detail_row / erp_price_detail.parts")
        return {"node": key, "url": page_url, **result}

    def fetch_all(self, record_id: str, *, cpid: str = "", bjid: str = "",
                  cpslid: str = "") -> dict:
        """整棵树：1 次详情 GET + 14 次子页 POST + 8 次面板回发（≈ 2.5 MB）。"""
        from .node_parser import PANEL_TARGETS

        parent_html, parent_url, detail = self._parent(record_id)
        ctx = self._ctx(record_id, detail, cpid=cpid, bjid=bjid, cpslid=cpslid)
        nodes: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for node in NODE_CATALOG:
            try:
                sub_html, _url = self._sub_page(parent_html, parent_url, node, ctx)
                nodes[node] = self._run_parser(node, sub_html)
            except Exception as exc:      # noqa: BLE001 —— 单点失败不影响其它节点
                errors[node] = f"{type(exc).__name__}: {exc}"
        for key, target in PANEL_TARGETS.items():
            try:
                nodes[f"panel:{key}"] = self._run_parser_for_panel(
                    self._panel_page(parent_html, parent_url, target))
            except Exception as exc:      # noqa: BLE001
                errors[f"panel:{key}"] = f"{type(exc).__name__}: {exc}"
        return {"node": "all", "ids": ctx, "nodes": nodes, "errors": errors,
                "count": len(nodes)}

    @staticmethod
    def _run_parser_for_panel(html: str) -> dict:
        from .node_parser import parse_panel_node

        data = parse_panel_node(html)
        return {**data, "count": len(data["grids"])}

    # ══════════════════ 主流程 ══════════════════
    def run(self, *, record_id: str | None = None, node: str = "",
            cpid: str | None = None, bjid: str | None = None,
            index: int | None = None, cpslid: str | None = None,
            out: str | None = None) -> Iterator[dict]:
        import json
        from pathlib import Path

        rid = (record_id or "").strip()
        if not rid:
            raise ParseError("record_id 必填")
        if index is not None:
            detail = self._parent(rid)[2]
            parts = [p for p in detail.parts if p.get("bjid")]
            pos = int(index) - 1
            if pos < 0 or pos >= len(parts):
                raise ParseError(f"index={index} 超出范围：该单共 {len(parts)} 个部件")
            bjid = bjid or parts[pos]["bjid"]

        result = self.fetch(rid, node, cpid=(cpid or ""), bjid=(bjid or ""),
                            cpslid=(cpslid or ""))
        if out:
            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            self._log(f"[DONE] → {out}")
        yield result

    @staticmethod
    def _log(msg: str) -> None:
        import sys

        print(msg, file=sys.stderr, flush=True)


__all__ = [
    "PriceListTool",
    "PriceDetailTool",
    "PriceNodeTool",
    "NODE_CATALOG",
    "NODE_ALIASES",
    "PANEL_ALIASES",
    "PAGE_PATH",
    "DEFAULT_QXID",
    "DATE_FIELDS",
    "SELECT_ALIASES",
    "FILTER_CONTROLS",
    "select_choices",
]
