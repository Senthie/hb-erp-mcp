"""抓一次 `Add_CPZL.aspx`（产品明细子页），核对 `quote_create/fields.py` 的注册表。

只读：全程只发 GET，不发任何 POST，不会写 ERP。

```powershell
.venv\\Scripts\\python.exe scripts\\probe-quote-fields.py            # 默认选最近一单
.venv\\Scripts\\python.exe scripts\\probe-quote-fields.py BJXQ261110003
```

产物落在 `tmp/quote-fields/`（已被 .gitignore 忽略，含真实客户数据，别提交）：
* `subpage_new.html`  —— `ID=-1`（空白行）的原始 HTML
* `subpage_edit.html` —— `ID=<cpid>`（已有行）的原始 HTML
* `controls.json`     —— 每个控件的 name / 类型 / 值 / 是否 CSS 隐藏 / 下拉选项

退出码：0=注册表完全覆盖页面；1=有未覆盖或有已失效的字段（会打印差异）。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from erp_tools.core.aspnet import soup_of  # noqa: E402
from erp_tools.core.auth import AuthManager  # noqa: E402
from erp_tools.core.config import ErpConfig  # noqa: E402
from erp_tools.core.htmlutil import get_input_value  # noqa: E402
from erp_tools.core.session import ErpClient  # noqa: E402
from erp_tools.tools.quote_create import (  # noqa: E402
    CONTROL_MAP,
    PRODUCT_FIELDS,
    STATE_FIELDS,
    UPLOAD_CONTROLS,
    VISIBLE_CONTROLS,
    QuoteCreateService,
    parse_product_controls,
)
from erp_tools.tools.quote_index.tool import QuoteListTool  # noqa: E402

OUT = ROOT / "tmp" / "quote-fields"
HOST = os.environ.get("ERP_HOST", "http://192.168.1.199:8889")
USER = os.environ.get("ERP_USERNAME", "21146")


def _pick_record(auth: AuthManager, bdbh: str) -> str:
    index = QuoteListTool(auth)
    if bdbh:
        row = index.find_by_bdbh(bdbh)
        if not row:
            raise SystemExit(f"找不到单据 {bdbh}")
        return row["记录ID"]
    rows, _headers, _page = index.collect(limit=20)
    for row in rows:
        if row.get("记录ID"):
            return row["记录ID"]
    raise SystemExit("最近 20 条里没有可用单据")


def main() -> int:
    bdbh = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = ErpConfig(host=HOST, username=USER, cookie_dir=ROOT / "cookies",
                    qxid_quote="873", qxid_quote_index="876")
    client = ErpClient(cfg)
    auth = AuthManager(client)
    if not auth.load_cookies() or not auth.is_logged_in():
        raise SystemExit("没有可用会话：先 erp login，或复制一份 cookies/<工号>.json")

    svc = QuoteCreateService(auth)
    record_id = _pick_record(auth, bdbh)
    html, _url = svc.open_record(record_id)
    soup = soup_of(html)
    details = svc.list_details(record_id)["details"]
    cpid = details[0]["cpid"] if details else "-1"

    OUT.mkdir(parents=True, exist_ok=True)
    live: set[str] = set()
    for tag, row_id in (("new", "-1"), ("edit", cpid)):
        url = svc._sub_page_url(
            record_id, get_input_value(soup, "txtKHID") or "",
            get_input_value(soup, "txtZKL"), cpid=row_id,
        )
        resp = client.get(url)
        (OUT / f"subpage_{tag}.html").write_text(resp.text, encoding="utf-8")
        controls = parse_product_controls(resp.text)
        live |= {c.control for c in controls}
        (OUT / f"controls_{tag}.json").write_text(
            json.dumps([c.__dict__ for c in controls], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    registry = set(CONTROL_MAP)
    missing = sorted(live - registry - set(STATE_FIELDS))
    extra = sorted(registry - live)
    css_hidden = sum(
        1 for c in parse_product_controls((OUT / "subpage_edit.html").read_text("utf-8"))
        if c.css_hidden
    )

    print(f"record_id={record_id} cpid={cpid}")
    print(f"页面控件={len(live)}（CSS 隐藏 {css_hidden}，首屏可见 {len(VISIBLE_CONTROLS)}）")
    print(f"注册表={len(PRODUCT_FIELDS)} 可上传(可见且可写)={len(UPLOAD_CONTROLS)}")
    print(f"未覆盖={missing or '无'}")
    print(f"已失效={extra or '无'}")

    leaked = sorted(UPLOAD_CONTROLS - VISIBLE_CONTROLS)
    if leaked:
        print(f"⚠ 可上传集合里混进了不可见字段：{leaked}")
        return 1
    print(f"产物：{OUT}")
    return 0 if not missing and not extra else 1


if __name__ == "__main__":
    raise SystemExit(main())
