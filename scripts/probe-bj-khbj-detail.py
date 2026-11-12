"""探查「客户报价」详情页（BaoJia/BJ_KHBJ/Add.aspx）。

只读：只发 GET，不发任何 POST，不会写 ERP。

```powershell
.venv\\Scripts\\python.exe scripts\\probe-bj-khbj-detail.py                # 取列表第一条
.venv\\Scripts\\python.exe scripts\\probe-bj-khbj-detail.py --id <GUID>
.venv\\Scripts\\python.exe scripts\\probe-bj-khbj-detail.py --bdbh KHBJ261112003-01
.venv\\Scripts\\python.exe scripts\\probe-bj-khbj-detail.py --menu       # 只打印菜单 QXID
```

产物（tmp/bj-khbj/，含真实业务数据，别提交）：
* `menu-2069.json`   菜单里 路径 -> QXID
* `detail-<id>.html` 详情页原始 HTML
* `detail-probe.txt` 控件 / 表格 / 标签摘要
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from erp_tools.core.aspnet import soup_of  # noqa: E402
from erp_tools.core.auth import AuthManager, parse_menu_urls  # noqa: E402
from erp_tools.core.config import ErpConfig  # noqa: E402
from erp_tools.core.session import ErpClient  # noqa: E402
from erp_tools.tools.quote_price import PriceListTool  # noqa: E402

OUT = ROOT / "tmp" / "bj-khbj"
HOST = os.environ.get("ERP_HOST", "http://192.168.1.199:8889")
PAGE_PATH = "BaoJia/BJ_KHBJ/Add.aspx"
STATE_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                "__LASTFOCUS", "__EVENTTARGET", "__EVENTARGUMENT")


def describe_controls(html: str) -> list[dict]:
    soup = soup_of(html)
    rows: list[dict] = []
    for el in soup.find_all(["input", "select", "textarea"]):
        name = el.get("name") or el.get("id") or ""
        if not name:
            continue
        item = {
            "control": name,
            "id": el.get("id") or "",
            "tag": el.name,
            "type": el.get("type") or "",
            "value": (el.get("value") or "")[:50] if el.name == "input" else "",
            "readonly": el.has_attr("readonly"),
            "disabled": el.has_attr("disabled"),
            "state": name in STATE_FIELDS,
        }
        if el.name == "select":
            item["options"] = [(o.get("value") or "", o.get_text(strip=True)[:20])
                               for o in el.find_all("option")][:12]
        if el.name == "textarea":
            item["value"] = (el.string or "")[:50]
        rows.append(item)
    return rows


def describe_tables(html: str) -> list[dict]:
    soup = soup_of(html)
    out: list[dict] = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        first = next((tr for tr in trs if tr.find_all(["td", "th"], recursive=False)), None)
        cells = first.find_all(["td", "th"], recursive=False) if first else []
        headers = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
        out.append({
            "id": table.get("id") or "",
            "rows": len(trs),
            "cols": len(headers),
            "headers": headers[:70],
        })
    return out


def main() -> int:
    args = sys.argv[1:]
    user = "2069"
    record_id = ""
    bdbh = ""
    for flag, target in (("--user", "user"), ("--id", "id"), ("--bdbh", "bdbh")):
        if flag in args:
            value = args[args.index(flag) + 1]
            if target == "user":
                user = value
            elif target == "id":
                record_id = value
            else:
                bdbh = value

    cfg = ErpConfig(host=HOST, username=user, cookie_dir=ROOT / "cookies")
    client = ErpClient(cfg)
    auth = AuthManager(client)
    if not auth.load_cookies():
        raise SystemExit(f"缺 cookies/{user}.json")

    uid = auth.user_id or user
    resp = client.get(f"/Admin/indexAjax.ashx?FunName=GetMenuTree&Value1={uid}&Value2=",
                      headers={"X-Requested-With": "XMLHttpRequest",
                               "Referer": client.url("/Admin/index.html")})
    mapping = parse_menu_urls(resp.text)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"menu-{user}.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    baojia = {k: v for k, v in mapping.items() if "bj_khbj" in k}
    print("menu:", json.dumps(baojia, ensure_ascii=False))
    if "--menu" in args:
        return 0

    qxid = mapping.get(PAGE_PATH.lower()) or mapping.get("baojia/bj_khbj/add.aspx") or ""
    if not qxid:
        print("!! 菜单里没有 BJ_KHBJ/Add.aspx")
        return 1

    if not record_id:
        tool = PriceListTool(auth)
        if bdbh:
            rows = tool.find_by_bdbh(bdbh)
        else:
            rows, _h, _p = tool.collect(limit=1, page_size=5000)
        if not rows:
            raise SystemExit("列表里没有可用记录")
        record_id = rows[0]["记录ID"]
        print(f"picked {rows[0].get('表单编号')} -> {record_id}")

    url = f"/Admin/{PAGE_PATH}?QXID={qxid}&MenuTree=1&LoginUserID={uid}&id={record_id}&Option=3"
    detail = client.get(url)
    (OUT / f"detail-{record_id[:8]}.html").write_text(detail.text, encoding="utf-8")

    controls = describe_controls(detail.text)
    tables = describe_tables(detail.text)
    soup = soup_of(detail.text)
    labels = {el.get("id"): " ".join(el.get_text(" ", strip=True).split())[:60]
              for el in soup.find_all(id=True)
              if str(el.get("id", "")).startswith("lbl")}
    labels = {k: v for k, v in labels.items() if v}

    lines = [
        f"GET {client.url(url)}",
        f"  status={detail.status_code} len={len(detail.text)}",
        f"QXID={qxid}  record={record_id}",
        f"--- 控件 {len(controls)} 个（隐藏 __* 状态域不列）",
    ]
    for c in controls:
        if c["state"]:
            continue
        line = (f"  {c['control']:<30} {c['tag']:<8} {c['type']:<8} id={c['id']:<26}"
                f" ro={int(c['readonly'])} dis={int(c['disabled'])} value={c['value']!r}")
        if c.get("options"):
            line += f" opts={c['options']}"
        lines.append(line)
    lines.append(f"--- 表 {len(tables)} 个")
    for t in tables:
        lines.append(f"  table#{t['id'] or '(无)'} rows={t['rows']} cols={t['cols']}")
        if t["cols"]:
            lines.append(f"    {t['headers']}")
    lines.append(f"--- labels: {labels}")
    divs = []
    for div in soup.find_all("div"):
        did = div.get("id")
        if did:
            divs.append(f"{did}({len(div.get_text(strip=True))})")
    lines.append(f"--- divs: {divs}")
    (OUT / "detail-probe.txt").write_text("\n".join(lines), encoding="utf-8")
    print("ok ->", OUT / "detail-probe.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
