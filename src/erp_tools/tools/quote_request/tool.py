"""报价需求采集（BJ_KHBJXQ/Add.aspx）。

输入：CSV（orders.csv）或 id 列表
输出：逐条记录（可直接写入 JSONL）

过程：登录态 → 探测 QXID（BaoJia/BJ_KHBJXQ/Add.aspx）→ 并发拉取 → 解析。
"""

from __future__ import annotations

import csv
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue
from typing import Iterator
from urllib.parse import urlencode

from ...core.aspnet import assert_not_request_validation_error
from ...core.errors import ErpError
from ..base import Option, Tool
from .parser import parse_quote_page

PAGE_PATH = "BaoJia/BJ_KHBJXQ/Add.aspx"
#: Option=3 = 查看模式（只读打开已存在的报价需求单）
VIEW_OPTION = "3"


class QuoteRequestTool(Tool):
    name = "quote"
    summary = "采集客户报价需求单（BJ_KHBJXQ/Add.aspx）→ ERP 订单需求 JSON"

    @staticmethod
    def options() -> list[Option]:
        return [
            Option("csv", "输入 CSV（列：id 或 记录ID；可带制单人）", cli="--csv"),
            Option("ids", "直接给出记录 id 列表", cli="--id"),
            Option("bdbh", "按表单编号取详情（如 BJXQ261105013，自动反查记录ID）",
                   cli="--bdbh"),
            Option("out", "输出 JSONL 路径（不填则只返回内存结果）", cli="--out"),
            Option("limit", "只处理前 N 条（抽样验证用）", default=None, cli="--limit"),
            Option("workers", "并发线程数，默认取 ERP_MAX_CONCURRENCY",
                   default=None, cli="--workers"),
            Option("resume", "跳过输出文件里已存在的记录（--no-resume 关闭）",
                   default=None, cli="--no-resume"),
        ]

    # ---------- 页面地址 ----------
    def page_url(self, record_id: str) -> str:
        qxid = self.auth.cfg.qxid_quote or self.auth.qxid_for(PAGE_PATH)
        uid = self.auth.user_id or ""
        params = {"id": record_id, "Option": VIEW_OPTION, "QXID": qxid, "MenuTree": "1"}
        if uid:
            params["LoginUserID"] = uid
        return f"/Admin/{PAGE_PATH}?{urlencode(params)}"

    def fetch_one(self, client, record_id: str, fallback_ywy: str | None = None) -> dict:
        resp = client.get(self.page_url(record_id))
        if resp.status_code != 200:
            raise ErpError(f"HTTP {resp.status_code}")
        assert_not_request_validation_error(resp.text)
        data = parse_quote_page(resp.text, fallback_ywy=fallback_ywy, record_id=record_id)
        if not data.get("BDBH"):
            raise ErpError("页面里没有 txtBDBH，可能不是有效的报价需求单或会话失效")
        return data

    # ---------- 主流程 ----------
    def run(self, *, csv: str | Path | None = None, ids: list[str] | None = None,
            bdbh: list[str] | None = None,
            out: str | Path | None = None, limit: int | None = None,
            workers: int | None = None, resume: bool = True,
            progress_every: int = 100) -> Iterator[dict]:
        rows = self._load_rows(csv, ids)
        if bdbh:
            resolved = self._resolve_bdbh(bdbh)
            known = {str(r["id"]) for r in rows}
            for rec_id in resolved:
                if rec_id not in known:
                    rows.append({"id": rec_id, "creator": None})
                    known.add(rec_id)
        if limit:
            rows = rows[: int(limit)]
        if not rows:
            return

        n_workers = int(workers or self.cfg.max_concurrency)
        n_workers = max(1, min(n_workers, len(rows)))

        # 断点续跑：先看输出文件里已完成的 id
        done: set[str] = set()
        if out and resume and Path(out).is_file():
            from ...output.jsonl import read_jsonl

            done = {str(r.get("id")) for r in read_jsonl(out) if r.get("id")}
            self._log(f"[INFO] 断点续跑：输出里已有 {len(done)} 条，将跳过")

        todo = [r for r in rows if str(r["id"]) not in done]
        if not todo:
            self._log("[INFO] 没有需要处理的记录")
            return

        self._log(f"[INFO] 共 {len(rows)} 条，待处理 {len(todo)} 条，并发 {n_workers}")
        qxid = self.auth.cfg.qxid_quote or self.auth.qxid_for(PAGE_PATH)
        self._log(f"[INFO] 报价需求页 QXID = {qxid}")

        sink = None
        if out:
            from ...output.jsonl import JsonlSink

            sink = JsonlSink(out, key="id", resume=True)

        clients: Queue = Queue()
        for _ in range(n_workers):
            clients.put(self.client.spawn())

        lock = threading.Lock()
        stats = {"ok": 0, "failed": 0}
        total = len(todo)

        def work(row: dict) -> dict | None:
            client = clients.get()
            try:
                record = self.fetch_one(client, row["id"], row.get("creator"))
            except Exception as exc:  # noqa: BLE001
                with lock:
                    stats["failed"] += 1
                    self._log(f"[ERROR] {row['id']}：{exc}")
                return None
            finally:
                clients.put(client)

            if sink is not None:
                sink.write(record)
            with lock:
                stats["ok"] += 1
                done_n = stats["ok"] + stats["failed"]
                if done_n % progress_every == 0 or done_n == total:
                    self._log(f"[进度] {done_n}/{total} 成功={stats['ok']} 失败={stats['failed']}")
            return record

        try:
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = [pool.submit(work, row) for row in todo]
                for fut in as_completed(futures):
                    record = fut.result()
                    if record is not None:
                        yield record
        finally:
            if sink is not None:
                sink.close()
            self._log(f"[DONE] 成功 {stats['ok']} / 失败 {stats['failed']} / 共 {total}")

    # ---------- 输入 ----------
    def _resolve_bdbh(self, codes: list[str]) -> list[str]:
        """表单编号 -> 记录 ID（借用列表页查询，单号里含日期不需要额外参数）。"""
        from ..quote_index import QuoteListTool

        finder = QuoteListTool(self.auth)
        ids: list[str] = []
        for raw in codes:
            for code in str(raw).replace("，", ",").split(","):
                code = code.strip()
                if not code:
                    continue
                row = finder.find_by_bdbh(code)
                rec_id = (row or {}).get("记录ID")
                if rec_id:
                    ids.append(str(rec_id))
                    self._log(f"[INFO] {code} → {rec_id}")
                else:
                    self._log(f"[WARN] 表单编号 {code} 在 ERP 里查不到（可能已归档或不属于当前账号）")
        return ids

    @staticmethod
    def _load_rows(csv_path: str | Path | None, ids: list[str] | None) -> list[dict]:
        rows: list[dict] = []
        if ids:
            if isinstance(ids, str):
                ids = [ids]
            rows.extend({"id": str(i).strip(), "creator": None} for i in ids if str(i).strip())
        if csv_path:
            p = Path(csv_path)
            if not p.is_file():
                raise FileNotFoundError(f"找不到 CSV：{p}")
            with p.open("r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    # 兼容两种来源：旧 orders.csv 用 `id`，`erp query` 导出的 CSV 用 `记录ID`
                    rec_id = (row.get("id") or row.get("记录ID") or "").strip()
                    if not rec_id:
                        continue
                    rows.append({
                        "id": rec_id,
                        "creator": (row.get("制单人") or "").strip() or None,
                        "hk_no": (row.get("香港单号") or "").strip(),
                        "form_no": (row.get("表单编号") or "").strip(),
                    })
        return rows

    @staticmethod
    def _log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)
