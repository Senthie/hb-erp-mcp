"""探查「客户报价」列表页（BaoJia/BJ_KHBJ/Index.aspx）的结构。

只读：默认只发 GET；加 `--search` 才会带默认日期区间做一次 btn_Search 回发（不写数据）。

```powershell
.venv\\Scripts\\python.exe scripts\\probe-bj-khbj.py
.venv\\Scripts\\python.exe scripts\\probe-bj-khbj.py --search
.venv\\Scripts\\python.exe scripts\\probe-bj-khbj.py --user 21146 --qxid 860
```

产物（tmp/bj-khbj/，含真实业务数据，别提交）：
* `index.html`      GET 到的原始页面
* `search.html`     btn_Search 回发后的页面（仅 --search）
* `page-probe.json` 控件清单 / 表格表头 / 分页标签
* `page-probe.txt`  给人看的中文摘要
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from erp_tools.core.aspnet import soup_of  # noqa: E402
from erp_tools.core.auth import AuthManager  # noqa: E402
from erp_tools.core.config import ErpConfig  # noqa: E402
from erp_tools.core.session import ErpClient  # noqa: E402
from erp_tools.tools.gridlist import button_payload, form_snapshot  # noqa: E402

PAGE_PATH = "BaoJia/BJ_KHBJ/Index.aspx"
OUT = ROOT / "tmp" / "bj-khbj"
HOST = os.environ.get("ERP_HOST", "http://192.168.1.199:8889")
STATE_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                "__LASTFOCUS", "__EVENTTARGET", "__EVENTARGUMENT")


def describe_controls(html: str) -> list[dict]:
    soup = soup_of(html)
    rows: list[dict] = []
    for el in soup.find_all(["input", "select", "textarea"]):
        name = el.get("name") or el.get("id") or ""
        if not name and el.get("type") != "image":
            continue
        item = {
            "control": name,
            "id": el.get("id") or "",
            "tag": el.name,
            "type": el.get("type") or "",
            "value": (el.get("value") or "")[:60] if el.name == "input" else "",
            "readonly": el.has_attr("readonly"),
            "disabled": el.has_attr("disabled"),
            "state": name in STATE_FIELDS,
        }
        if el.name == "select":
            item["options"] = [
                {"label": o.get_text(strip=True), "value": o.get("value") or ""}
                for o in el.find_all("option")
            ]
        if el.name == "textarea":
            item["value"] = (el.string or "")[:60]
        rows.append(item)
    return rows


def describe_tables(html: str) -> list[dict]:
    soup = soup_of(html)
    out: list[dict] = []
    for table in soup.find_all("table"):
        tid = table.get("id") or ""
        first = table.find("tr")
        cells = first.find_all(["td", "th"], recursive=False) if first else []
        headers = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
        body = table.find_all("tr")[1:4]
        out.append({
            "id": tid,
            "rows": len(table.find_all("tr")),
            "header_count": len(headers),
            "headers": headers[:60],
            "sample_cells": [
                [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all("td")]
                for tr in body
            ],
            "row_attrs": [
                {k: (v[:80] if isinstance(v, str) else v)
                 for k, v in tr.attrs.items() if k in ("onclick", "ondblclick", "class")}
                for tr in body
            ],
        })
    return out


def describe_divs(html: str) -> list[dict]:
    soup = soup_of(html)
    out = []
    for div in soup.find_all("div"):
        did = div.get("id")
        if not did:
            continue
        out.append({
            "id": did,
            "chars": len(div.get_text(strip=True)),
            "has_table": div.find("table") is not None,
            "style_hidden": "display:none" in (div.get("style") or "").replace(" ", ""),
        })
    return out


def labels(html: str) -> dict[str, str]:
    soup = soup_of(html)
    out = {}
    for el in soup.find_all(id=True):
        if str(el.get("id", "")).startswith("lbl"):
            text = " ".join(el.get_text(" ", strip=True).split())
            if text:
                out[el["id"]] = text[:80]
    return out


def probe(html: str, tag: str) -> dict:
    return {
        "tag": tag,
        "length": len(html),
        "has_viewstate": "__VIEWSTATE" in html,
        "controls": describe_controls(html),
        "tables": describe_tables(html),
        "divs": describe_divs(html),
        "labels": labels(html),
    }


def main() -> int:
    args = sys.argv[1:]
    user = "2069"
    qxid = "860"
    do_search = "--search" in args
    if "--user" in args:
        user = args[args.index("--user") + 1]
    if "--qxid" in args:
        qxid = args[args.index("--qxid") + 1]

    cfg = ErpConfig(host=HOST, username=user, cookie_dir=ROOT / "cookies")
    client = ErpClient(cfg)
    auth = AuthManager(client)
    if not auth.load_cookies():
        raise SystemExit(f"缺 cookies/{user}.json")
    if not auth.is_logged_in():
        print("⚠ is_logged_in() 返回 False（可能是探测逻辑误判，继续尝试业务页）")

    uid = (client.cookies.get("UserID") or "").split("=")[-1] or user
    url = f"/Admin/{PAGE_PATH}?QXID={qxid}&MenuTree=1&LoginUserID={uid}"
    resp = client.get(url)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(resp.text, encoding="utf-8")

    report = {"url": client.url(url), "status": resp.status_code,
              "final_url": resp.url, "pages": [probe(resp.text, "index")]}

    if do_search and "__VIEWSTATE" in resp.text:
        body = form_snapshot(resp.text)
        today = date.today()
        body.update({
            "txtKSRQ": today.replace(day=1).isoformat(),
            "txtJZRQ": today.isoformat(),
            "txtEvenPageDataSize": "500",
        })
        body.update(button_payload(resp.text, "btn_Search"))
        resp2 = client.post(url, data=body)
        (OUT / "search.html").write_text(resp2.text, encoding="utf-8")
        report["search"] = {"status": resp2.status_code, "length": len(resp2.text),
                            "pages": [probe(resp2.text, "search")]}

    (OUT / "page-probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"GET {report['url']}", f"  status={report['status']} "
             f"final={report['final_url']} len={len(resp.text)}"]
    for item in report["pages"] + [p for s in report.get("search", {}).get("pages", []) for p in [s]]:
        lines.append(f"--- {item['tag']} 控件 {len(item['controls'])} 个")
        for c in item["controls"]:
            if c["state"]:
                continue
            lines.append(
                f"  {c['control']:<28} {c['tag']:<8} {c['type']:<8} "
                f"id={c['id']:<24} ro={int(c['readonly'])} dis={int(c['disabled'])}"
                + (f" opts={len(c.get('options') or [])}" if c.get("options") else "")
            )
        lines.append(f"--- {item['tag']} 表 {len(item['tables'])} 个")
        for t in item["tables"]:
            lines.append(f"  table#{t['id'] or '(无 id)'} rows={t['rows']} "
                         f"cols={t['header_count']}")
            lines.append(f"    header={t['headers']}")
            for cells, attrs in zip(t["sample_cells"], t["row_attrs"]):
                lines.append(f"    row={cells} attrs={attrs}")
        lines.append(f"--- {item['tag']} labels: {item['labels']}")
        hidden = [d["id"] for d in item["divs"] if d["has_table"]]
        lines.append(f"--- {item['tag']} 含表格的 div: {hidden}")
    (OUT / "page-probe.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"ok -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
