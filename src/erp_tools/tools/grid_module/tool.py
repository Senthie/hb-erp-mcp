"""列表 / 详情 / 子数据的工具实现（按 `ModuleSpec` 参数化）。

三种工具的职责：

* `ModuleListTool`   —— 列表查询 + 翻页 + 本地过滤（服务端只认日期区间和关键字）；
* `ModuleDetailTool` —— `Option=3` 只读详情页：表头字段 + 内联网格 + 文本域，
  并附带「这一单能点开哪些子页」；
* `ModuleNodeTool`   —— 打开弹窗子页并把它解析成「标签→值 + 网格」。

子页打开协议（实测）：先 GET 详情页拿快照 → 写入该 `Open*` 函数要求的
`lblSelect*`（实参取页面里真实出现的那次调用）→ 回发对应 `btnSelect*`
→ 响应里的 `showModalDialog('xxx.aspx?...')` 即子页 URL → GET 该 URL。
"""

from __future__ import annotations

import sys
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
from ..quote_index.tool import MAX_PAGES_GUARD, default_date_range
from ..quote_price.detail_parser import norm
from .parser import (
    detail_context,
    discover_nodes,
    fill_missing,
    node_payload,
    parse_detail,
    popup_url,
)
from .spec import ModuleSpec, get_module

#: 这些控件是「状态/结构」字段，事件验证失败多半是它们（空 options / disabled）
_STATE_SKIP_NOTE = "空 options 或 disabled 的控件不能回发（会触发事件验证 500）"


# ══════════════════ 公共 ══════════════════
class ModuleBase(Tool):
    """按模块键初始化，页面 URL/QXID 统一在这里拼。"""

    spec: ModuleSpec = None  # type: ignore[assignment]

    def __init__(self, auth, module: str = "") -> None:
        super().__init__(auth)
        self.spec = get_module(module) if module else self.spec

    # ---------- URL ----------
    def page_url(self, path: str, qxid: str) -> str:
        uid = self.auth.user_id or ""
        url = f"/Admin/{path}?QXID={qxid}&MenuTree=1"
        return f"{url}&LoginUserID={uid}" if uid else url

    def qxid_for(self, path: str, fallback: str) -> str:
        """QXID 解析：配置覆盖 > 本模块实测值 > 菜单探测。

        ⚠ 菜单探测在这里**不可靠**：同一路径会挂在多个菜单下
        （`BJ_KHBJ/Index.aspx` 既是「列表-客户报价」QXID=860，又是「列表-产品核价」
        QXID=1281；`SC_SCSGD/Index.aspx` 也是 740 / 983 两个菜单），
        `parse_menu_urls` 用 setdefault 只留第一个，于是会打开**别的菜单**的页面
        （症状：施工单列表变成 66 列、单号变成 `ZBSQ…` 制版申请）。
        所以内置实测值优先，只有它为空时才问菜单。
        """
        from ...core.auth import normalize_page_path
        from ...core.errors import ErpError

        override = getattr(self.cfg, f"qxid_{self.spec.key}_index", "") or ""
        if override:
            return str(override)
        if fallback:
            return str(fallback)
        try:
            mapping = self.auth.qxid_map()
        except ErpError:
            return ""
        return str(mapping.get(normalize_page_path(path)) or "")

    def list_qxid(self) -> str:
        return self.qxid_for(self.spec.list_path, self.spec.fallback_qxid)

    # ---------- 日志 ----------
    @staticmethod
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)


def _drop_invalid_controls(html: str, body: dict[str, str]) -> list[str]:
    """去掉「空 options 的 select」与 disabled 控件。

    这些控件浏览器根本不会提交；带上它们会让 ERP 的事件验证
    （`enableEventValidation="true"`）直接 500「回发或回调参数无效」——
    施工单详情页就是被 `txtBJBJLB/txtBJZYFS` 这两个空下拉卡住的。
    """
    from ...core.aspnet import soup_of

    soup = soup_of(html)
    dropped: list[str] = []
    for sel in soup.find_all("select"):
        name = sel.get("name")
        if name and (not sel.find_all("option") or sel.has_attr("disabled")):
            if body.pop(name, None) is not None:
                dropped.append(name)
    for el in soup.find_all("input"):
        name = el.get("name")
        if name and el.has_attr("disabled"):
            if body.pop(name, None) is not None:
                dropped.append(name)
    return dropped


# ══════════════════ 列表 ══════════════════
class ModuleListTool(ModuleBase):
    """列表查询：服务端只支持日期区间 + 关键字，其余在本地过滤。"""

    name = "list"
    summary = "查询列表页（施工单 / 采购订单 / 产品核价）→ 表格 / CSV / JSONL"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("module", "模块：production(生产施工单) / purchase(采购订单) / costing(产品核价)",
                   cli="--module"),
            Option("from_date", "日期区间起（默认本月 1 号）", cli="--from"),
            Option("to_date", "日期区间止（默认今天）", cli="--to"),
            Option("date_type", "日期区间作用于哪个字段（模块不同可选值不同）", cli="--date-type"),
            Option("key", "服务端关键字（配合 --field）", cli="--key"),
            Option("field", "关键字字段中文名", cli="--field"),
            Option("key1", "第二个服务端关键字（配合 --field1）", cli="--key1"),
            Option("field1", "第二个关键字字段中文名", cli="--field1"),
            Option("where", "本地过滤，逗号分隔：客户名称~ADG,生产单号=BDBH", cli="--where"),
            Option("filters", "服务端下拉过滤，如 工单状态=已审批,报废状态=未报废", cli="--filter"),
            Option("columns", "只导出指定列，逗号分隔", cli="--columns"),
            Option("out", "导出 CSV 路径（.jsonl 结尾则写 JSONL）", cli="--out"),
            Option("jsonl", "额外写一份 JSONL", cli="--jsonl"),
            Option("limit", "最多返回多少条", cli="--limit"),
            Option("page_size", "每页记录数（默认 ERP_PAGE_SIZE_MAX）", cli="--page-size"),
            Option("max_pages", f"最多翻多少页（安全阀，默认 {MAX_PAGES_GUARD}）",
                   cli="--max-pages"),
            Option("dry_run", "只显示解析出的条件与命中数，不写文件", cli="--dry-run"),
        ]

    # ---------- 过滤参数 ----------
    def parse_filters(self, filters) -> dict[str, str]:
        if not filters:
            return {}
        items: list[tuple[str, str]] = []
        if isinstance(filters, dict):
            items = [(str(k), str(v)) for k, v in filters.items()]
        else:
            for raw in filters:
                key, sep, value = str(raw).partition("=")
                if not sep:
                    raise ParseError(f"过滤条件 {raw!r} 缺少 '='，形如 工单状态=已审批")
                items.append((key.strip(), value.strip()))
        out: dict[str, str] = {}
        for key, value in items:
            control = self.spec.control_for(key)
            if not control:
                raise ParseError(
                    f"{self.spec.label} 不支持过滤条件 {key!r}；可用："
                    + ", ".join(sorted(set(self.spec.filter_aliases) | set(self.spec.filter_controls)))
                )
            out[control] = value
        return out

    def filter_catalog(self) -> dict[str, str]:
        """给 MCP 用的下拉目录：控件名 -> 中文说明。"""
        return dict(self.spec.filter_controls)

    # ---------- 查询 ----------
    def _search(self, *, from_date: str, to_date: str, page_size: int,
                date_type: str = "", key: str = "", field: str = "",
                key1: str = "", field1: str = "",
                filters: dict[str, str] | None = None):
        url = self.page_url(self.spec.list_path, self.list_qxid())
        resp = self.client.get(url)
        if resp.status_code != 200:
            raise UpstreamError(f"列表页打开失败（HTTP {resp.status_code}）", resp.status_code)
        if "__VIEWSTATE" not in resp.text:
            raise ParseError("列表页没有 __VIEWSTATE，可能被重定向到登录页")
        html = resp.text

        body = form_snapshot(html)
        body.update({
            "txtKSRQ": from_date,
            "txtJZRQ": to_date,
            "txtEvenPageDataSize": str(page_size),
        })
        body.update(button_payload(html, "btn_Search"))
        if date_type and self.spec.date_control:
            value = self.spec.resolve_date_field(date_type)
            body[self.spec.date_control] = resolve_select_value(
                html, self.spec.date_control, value)
        for control, value in (("txtKey", key), ("txtKey1", key1)):
            if value:
                body[control] = value
        for which, name in (("DropField", field), ("DropField1", field1)):
            if not name:
                continue
            codes = field_codes(html, which)
            if name in codes:
                body[which] = codes[name]
            elif name in codes.values():
                body[which] = name
            else:
                raise ParseError(
                    f"未知关键字字段 {name!r}；可用：{', '.join(codes) or '(页面无此下拉)'}")
        for control, wanted in (filters or {}).items():
            body[control] = resolve_select_value(html, control, wanted)

        _drop_invalid_controls(html, body)
        resp = self.client.post(url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(f"查询失败（HTTP {resp.status_code}）",
                                resp.status_code, resp.text[:300])
        page = self.parse_page(resp.text)
        if not page.headers:
            raise ParseError("查询结果里没有表格，页面结构可能变了")
        return page, resp.text

    def parse_page(self, html: str):
        from ..gridlist import parse_grid_page

        return parse_grid_page(html)

    def _next_page(self, current_html: str, *, from_date: str, to_date: str,
                   page_size: int, date_type: str = "",
                   filters: dict[str, str] | None = None):
        url = self.page_url(self.spec.list_path, self.list_qxid())
        body = form_snapshot(current_html)
        body.update({
            "txtKSRQ": from_date,
            "txtJZRQ": to_date,
            "txtEvenPageDataSize": str(page_size),
            "__EVENTTARGET": "btnNext",
            "__EVENTARGUMENT": "",
        })
        if date_type and self.spec.date_control:
            body[self.spec.date_control] = resolve_select_value(
                current_html, self.spec.date_control,
                self.spec.resolve_date_field(date_type))
        for control, wanted in (filters or {}).items():
            body[control] = resolve_select_value(current_html, control, wanted)
        _drop_invalid_controls(current_html, body)
        resp = self.client.post(url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(
                f"翻页被服务端拒绝（HTTP {resp.status_code}）；可调大 --page-size 一次取全",
                resp.status_code, resp.text[:300])
        page = self.parse_page(resp.text)
        if not page.headers:
            raise UpstreamError("翻页返回的不是列表表格，翻页协议可能变了")
        return page, resp.text

    def collect(self, *, from_date: str | None = None, to_date: str | None = None,
                date_type: str = "", key: str = "", field: str = "",
                key1: str = "", field1: str = "", where: str | None = None,
                filters=None, limit: int | None = None,
                page_size: int | None = None,
                max_pages: int = MAX_PAGES_GUARD):
        """执行查询，返回 (记录, 可用列, 首页分页信息)。"""
        d_from, d_to = default_date_range()
        from_date = from_date or d_from
        to_date = to_date or d_to
        size = int(page_size or self.cfg.page_size_max)
        extra = self.parse_filters(filters)
        date_type = date_type or (
            next(iter(self.spec.date_fields), "") if self.spec.date_control else "")

        conditions: list[Condition] = list(parse_where(where))
        self._log(f"[INFO] {self.spec.label} {from_date} ~ {to_date} "
                  f"日期字段={date_type or '(固定制单日期)'} 页大小={size}"
                  + (f" 过滤={extra}" if extra else ""))
        page, html = self._search(from_date=from_date, to_date=to_date, page_size=size,
                                  date_type=date_type, key=key, field=field,
                                  key1=key1, field1=field1, filters=extra)
        self._log(f"[INFO] 服务端命中 {page.total} 行 / {page.pages} 页")

        headers = list(page.headers)
        rows: list[dict] = list(page.rows)
        hop = 0
        while page.has_more_pages:
            hop += 1
            if hop > max_pages:
                raise UpstreamError(f"翻页超过 {max_pages} 页，已停止（可调大 --max-pages）")
            page, html = self._next_page(html, from_date=from_date, to_date=to_date,
                                         page_size=size, date_type=date_type,
                                         filters=extra)
            rows.extend(page.rows)

        rows = apply_conditions(rows, conditions, headers + ["记录ID"])
        if limit:
            rows = rows[: int(limit)]
        return rows, headers, page

    def run(self, **opts) -> Iterator[dict]:
        rows, headers, _page = self.collect(
            from_date=opts.get("from_date"), to_date=opts.get("to_date"),
            date_type=opts.get("date_type") or "", key=opts.get("key") or "",
            field=opts.get("field") or "", key1=opts.get("key1") or "",
            field1=opts.get("field1") or "", where=opts.get("where"),
            filters=opts.get("filters"), limit=opts.get("limit"),
            page_size=opts.get("page_size"),
        )
        yield from self._emit(rows, headers, columns=opts.get("columns"),
                              out=opts.get("out"), jsonl=opts.get("jsonl"),
                              dry_run=bool(opts.get("dry_run")))

    # ---------- 输出 ----------
    @staticmethod
    def pick_columns(spec: str | None, headers: list[str]) -> list[str]:
        from ..gridlist import ACTION_COLUMNS

        available = [h for h in headers if h not in ACTION_COLUMNS]
        if not spec:
            return available + ([] if "记录ID" in available else ["记录ID"])
        picked: list[str] = []
        for raw in str(spec).replace("，", ",").split(","):
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

        use_columns = self.pick_columns(columns, headers)
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
            self._log(f"[DONE] {len(rows)} 行（未指定 --out，仅预览前 20 行）")
        yield from rows

    def _preview(self, rows: list[dict], columns: list[str], limit: int = 20) -> None:
        from ...output.markdown import to_markdown_table

        preferred = ["表单编号", "生产单号", "订单编号", "产品名称", "客户名称",
                     "工单状态", "材料名称", "供应商名称", "报价状态", "制单日期"]
        picked = [c for c in preferred if c in columns][:6] or columns[:6]
        table = to_markdown_table(
            picked, [[r.get(c) for c in picked] for r in rows[:limit]], max_col_width=24)
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

    # ---------- 单号反查 ----------
    def find_by_number(self, number: str, *, page_size: int | None = None) -> list[dict]:
        """按单号（生产单号 / 表单编号）在近一年里找对应行。"""
        target = (number or "").strip()
        if not target:
            return []
        column = self.spec.number_column or "表单编号"
        for candidate in _candidate_ranges(target):
            rows, _headers, _page = self.collect(
                from_date=candidate[0], to_date=candidate[1],
                key=target, field=column, page_size=page_size,
            )
            if rows:
                return rows
        return []


def _candidate_ranges(number: str) -> list[tuple[str, str]]:
    """单号里的日期不一定等于制单日期，按「当天 → 当月 → 本月 → 近一年」逐级放宽。"""
    import re
    from datetime import date, timedelta

    today = date.today()
    out: list[tuple[str, str]] = []
    m = re.search(r"(\d{6})", number or "")
    if m:
        raw = m.group(1)
        try:
            day = date(2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6]))
        except ValueError:
            day = None
        if day is not None:
            out.append((day.isoformat(), day.isoformat()))
            month_start = day.replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            out.append((month_start.isoformat(), month_end.isoformat()))
    out.append((today.replace(day=1).isoformat(), today.isoformat()))
    out.append(((today - timedelta(days=365)).isoformat(), today.isoformat()))

    seen: set[tuple[str, str]] = set()
    uniq: list[tuple[str, str]] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


# ══════════════════ 详情 ══════════════════
class ModuleDetailTool(ModuleBase):
    """`Option=3` 只读详情：字段 + 内联网格 + 可点子页清单。"""

    name = "detail"
    summary = "读取详情页（生产施工单 / 采购订单）→ 字段 + 内联网格 + 子页清单"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("module", "模块：production / purchase", cli="--module"),
            Option("record_id", "记录 ID（列表的 记录ID）", cli="--id"),
            Option("number", "单号（生产单号 / 表单编号），与 --id 二选一", cli="--no"),
            Option("include_texts", "是否返回备注类文本域（默认 true）", cli="--texts"),
            Option("raw", "附加原始 HTML（调试用，很大）", cli="--raw"),
        ]

    def open_record(self, record_id: str) -> tuple[str, str]:
        """GET 详情页并自校验记录 ID（ERP 偶尔会串记录）。"""
        qxid = self.qxid_for(self.spec.detail_path, self.spec.fallback_qxid)
        for _attempt in range(2):
            url = self.page_url(self.spec.detail_path, qxid) + f"&id={record_id}&Option=3"
            resp = self.client.get(url)
            if resp.status_code >= 500:
                raise UpstreamError(f"详情页打开失败（HTTP {resp.status_code}）",
                                    resp.status_code, resp.text[:300])
            if "没有权限" in resp.text or "已过期" in resp.text:
                raise ParseError("详情页被拒绝：当前账号没有该单据权限，或会话已过期")
            parsed = parse_detail(resp.text, record_id=record_id)
            if parsed["self_check"] or not parsed["page_record_id"]:
                return resp.text, url
        return resp.text, url

    def resolve_records(self, *, record_id: str = "", number: str = "") -> list[dict]:
        if record_id:
            return [{"记录ID": record_id.strip()}]
        if not number:
            raise ParseError("record_id 与 number 至少给一个")
        rows = ModuleListTool(self.auth, self.spec.key).find_by_number(number)
        if not rows:
            raise ParseError(
                f"单号 {number} 查不到（可能已归档，或当前账号无该单据权限）")
        return rows

    def fetch_one(self, record_id: str, *, include_texts: bool = True,
                  raw: bool = False) -> dict:
        html, url = self.open_record(record_id)
        parsed = parse_detail(html, record_id=record_id)
        nodes = discover_nodes(html, self.spec.nodes)
        result: dict = {
            "module": self.spec.key,
            "label": self.spec.label,
            "record_id": parsed["record_id"],
            "page_record_id": parsed["page_record_id"],
            "self_check": parsed["self_check"],
            "fields": parsed["fields"],
            "grids": parsed["grids"],
            "nodes": [
                {"node": n.node, "label": n.label, "page": n.page, "button": n.button}
                for n in nodes
            ],
            "_url": url,
        }
        if include_texts:
            result["texts"] = parsed["texts"]
        if raw:
            result["_html"] = html
        return result

    def run(self, **opts) -> Iterator[dict]:
        rows = self.resolve_records(record_id=opts.get("record_id") or "",
                                    number=opts.get("number") or "")
        for row in rows:
            yield self.fetch_one(
                row["记录ID"],
                include_texts=bool(opts.get("include_texts", True)),
                raw=bool(opts.get("raw")),
            )


# ══════════════════ 子数据 ══════════════════
class ModuleNodeTool(ModuleBase):
    """打开弹窗子页（`Add_BJFL.aspx` 这类）并解析成字段 + 网格。"""

    name = "node"
    summary = "读取详情页里「点开才有」的子数据页面 → 字段 + 网格"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("module", "模块：production / purchase", cli="--module"),
            Option("record_id", "记录 ID", cli="--id"),
            Option("node", "节点名；list=列出全部节点", cli="--node"),
            Option("out", "输出 JSON 路径", cli="--out"),
        ]

    # ---------- 节点解析 ----------
    def resolve_node(self, name: str) -> str:
        raw = (name or "").strip()
        if not raw:
            raise ParseError("node 必填；可用 node=list 查看全部节点")
        low = raw.lower()
        if low in self.spec.nodes:
            return low
        alias = self.spec.node_aliases.get(raw)
        if alias:
            return alias
        for node, (page, _button, _label) in self.spec.nodes.items():
            if page.lower() == low or page.lower().startswith(low):
                return node
        raise ParseError(
            f"{self.spec.label} 没有节点 {raw!r}；可用：{', '.join(self.spec.nodes)}")

    def catalog(self, record_id: str = "") -> list[dict]:
        """列出节点；给了 record_id 就顺便标出「这一单能不能点开」。"""
        available: dict[str, dict] = {}
        if record_id:
            html, _url = ModuleDetailTool(self.auth, self.spec.key).open_record(record_id)
            available = {n.node: {"page": n.page, "button": n.button, "function": n.function}
                         for n in discover_nodes(html, self.spec.nodes)}
        rows = []
        for node, (page, button, label) in self.spec.nodes.items():
            entry = {"node": node, "label": label, "page": page, "button": button}
            if record_id:
                live = available.get(node)
                entry["available"] = live is not None
                if live:
                    entry["function"] = live["function"]
            rows.append(entry)
        return rows

    # ---------- 打开 ----------
    def fetch(self, record_id: str, node: str) -> dict:
        from ...core.aspnet import soup_of

        detail_tool = ModuleDetailTool(self.auth, self.spec.key)
        html, detail_url = detail_tool.open_record(record_id)
        entries = {n.node: n for n in discover_nodes(html, self.spec.nodes)}
        entry = entries.get(node)
        if entry is None:
            raise ParseError(
                f"这一单没有可点开的 {node} 子页（页面里找不到打开它的按钮 "
                f"{self.spec.nodes.get(node, ('', '', ''))[1]!r}）")

        body = form_snapshot(html)
        payload = node_payload(entry)
        filled = fill_missing(payload, detail_context(html, record_id))
        body.update(filled)
        body.update(button_payload(html, entry.button))
        body["__EVENTTARGET"] = ""
        body["__EVENTARGUMENT"] = ""
        dropped = _drop_invalid_controls(html, body)

        resp = self.client.post(detail_url, data=body)
        if resp.status_code >= 500:
            raise UpstreamError(
                f"子页入口回发失败（HTTP {resp.status_code}）：{_STATE_SKIP_NOTE}",
                resp.status_code, resp.text[:300])
        target = popup_url(resp.text)
        if not target:
            raise ParseError(
                f"回发后没有拿到子页 URL（{entry.function}→{entry.button}）。"
                f"{self.spec.label}的只读页不一定会给这个按钮渲染弹窗："
                "有些子页（如 费项/其他费用）只对有数量档数据的单据出现。"
                "可先用 node=list 看哪些节点这一单确实能打开。")

        sub_url = self._absolute_sub_url(detail_url, target)
        sub = self.client.get(sub_url)
        if sub.status_code >= 500:
            raise UpstreamError(f"子页打开失败（HTTP {sub.status_code}）",
                                sub.status_code, sub.text[:300])

        parsed = parse_detail(sub.text)
        sub_soup = soup_of(sub.text)
        return {
            "module": self.spec.key,
            "label": self.spec.label,
            "record_id": record_id,
            "node": node,
            "node_label": entry.label,
            "page": entry.page,
            "url": sub_url,
            "fields": parsed["fields"],
            "grids": parsed["grids"],
            "texts": parsed["texts"],
            "dropped_controls": dropped,
            "title": norm(sub_soup.title.get_text(" ", strip=True)
                          if sub_soup.title is not None else ""),
        }

    @staticmethod
    def _absolute_sub_url(detail_url: str, target: str) -> str:
        """把弹窗里的相对路径解析成 /Admin 下的绝对路径。"""
        from posixpath import dirname, join, normpath

        if target.startswith(("http://", "https://")):
            return target
        base = detail_url.split("?", 1)[0]          # /Admin/ShengChan/SC_SCSGD/Add.aspx
        if target.startswith("/"):
            return target
        return normpath(join(dirname(base), target))

    def run(self, **opts) -> Iterator[dict]:
        module = self.spec.key
        record_id = (opts.get("record_id") or "").strip()
        node = (opts.get("node") or "").strip()
        if not record_id:
            raise ParseError("record_id 必填")
        if node.lower() in ("list", "ls", "nodes"):
            for row in self.catalog(record_id):
                yield row
            return
        yield self.fetch(record_id, self.resolve_node(node))
