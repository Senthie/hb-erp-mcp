"""命令行入口：`erp <工具> [选项]`。"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import __version__
from .core.auth import AuthManager, ensure_authenticated
from .core.config import ErpConfig
from .core.errors import ErpError
from .core.session import ErpClient
from .tools.registry import build_tool, describe_tools


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ensure_console() -> None:
    """中文 Windows 控制台常见编码是 GBK；遇到无法编码的字符时用替换而不是崩掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


# ══════════════════ 配置 ══════════════════
def _build_config(args) -> ErpConfig:
    cfg = ErpConfig.from_env(getattr(args, "env_file", None), getattr(args, "config", None))
    kwargs = {}
    if args.host:
        kwargs["host"] = args.host
        kwargs["captcha_host"] = ""  # 让新 host 重新推导验证码端口
    if args.user:
        kwargs["username"] = args.user
    if args.password:
        kwargs["password"] = args.password
    if args.concurrency:
        kwargs["max_concurrency"] = args.concurrency
    if args.delay is not None:
        kwargs["request_delay"] = args.delay
    return ErpConfig(**{**cfg.__dict__, **kwargs}) if kwargs else cfg


def _build_auth(cfg: ErpConfig, *, interactive: bool = True) -> AuthManager:
    return AuthManager(ErpClient(cfg), interactive=interactive)


# ══════════════════ 子命令 ══════════════════
def cmd_login(args) -> int:
    cfg = _build_config(args)
    if not cfg.password:
        if not sys.stdin.isatty():
            _log("[ERROR] 缺少密码：设置 ERP_PASSWORD 或 .env")
            return 2
        object.__setattr__(cfg, "password", getpass.getpass(f"{cfg.username} 的密码："))

    auth = _build_auth(cfg)
    auth.ensure_login(force=args.force)
    _log(f"[OK] 登录成功，用户 ID = {auth.user_id}")
    try:
        mapping = auth.qxid_map(refresh=True)
        _log(f"[OK] 菜单探测到 {len(mapping)} 个页面")
        for path, qxid in sorted(mapping.items()):
            print(f"  {qxid:>6}  {path}")
    except ErpError as exc:
        _log(f"[WARN] QXID 探测失败：{exc}")
    return 0


def cmd_qxid(args) -> int:
    cfg = _build_config(args)
    auth = _build_auth(cfg)
    auth.ensure_login()
    mapping = auth.qxid_map(refresh=True)
    for key in ("baojia/bj_khbjxq/add.aspx", "renshi/rs_kqda_m/add.aspx"):
        if key not in mapping:
            _log(f"[WARN] 菜单里没有 {key}（权限不足？）")
    if args.json:
        print(json.dumps(mapping, ensure_ascii=False, indent=2))
    else:
        for path, qxid in sorted(mapping.items()):
            print(f"{qxid:>6}  {path}")
    return 0


def cmd_key(args) -> int:
    """向 erp-tools 网关换取 api-key。

    密码**只在本次请求里用一次**：既不进 `.env`，也不写任何文件。
    每个人都用**自己的** ERP 工号/密码换**自己的** key，服务端只存 key 的 sha256。
    """
    import requests

    server = (args.server or "").strip().rstrip("/")
    if not server:
        _log("[ERROR] 需要 --server，例如 --server https://erp-tools.lan:8443")
        return 2

    username = (args.user or "").strip()
    if not username:
        if not sys.stdin.isatty():
            _log("[ERROR] 缺少工号：加 -u <工号>")
            return 2
        username = input("ERP 工号：").strip()
    if not username:
        _log("[ERROR] 工号不能为空")
        return 2

    password = args.password or ""
    if not password:
        if not sys.stdin.isatty():
            _log("[ERROR] 没有可交互终端，无法输入密码；请在本机终端里运行（不建议用 --password，"
                 "它会把密码留在 shell 历史里）")
            return 2
        password = getpass.getpass(f"{username} 的 ERP 密码：")
    if not password:
        _log("[ERROR] 密码不能为空")
        return 2

    verify: object = True
    if args.insecure:
        verify = False
        _log("[WARN] --insecure：已关闭证书校验，仅限临时排查自签证书")
    elif args.cacert:
        verify = args.cacert

    try:
        resp = requests.post(
            f"{server}/v1/auth/login",
            json={"username": username, "password": password},
            verify=verify,
            timeout=args.timeout,
        )
    except requests.RequestException as exc:
        _log(f"[ERROR] 连不上 {server}：{exc}")
        return 2

    try:
        payload = resp.json()
    except ValueError:
        _log(f"[ERROR] 返回的不是 JSON（HTTP {resp.status_code}）：{resp.text[:200]}")
        return 1

    if resp.status_code != 200:
        _log(f"[ERROR] 登录失败（HTTP {resp.status_code}）："
             f"{payload.get('error')} {payload.get('message')}")
        hint = payload.get("hint")
        if hint:
            _log(f"         {hint}")
        return 1

    # key 打到 stdout（方便 `| clip` 或脚本捕获），说明打到 stderr
    print(payload["api_key"])
    _log(f"[OK] {payload.get('username')} / user_id={payload.get('user_id')} 已换到 api-key")
    pages = payload.get("erp_pages") or {}
    if pages:
        _log(f"[OK] 该账号可访问页面：{', '.join(sorted(pages))}")
    _log("提示：这把 key 会随 ERP 会话失效；换机器/换人请各自重新执行本命令。")
    return 0


# ══════════════════ 菜单 → 接口 对照 ══════════════════
def _menu_paths(root: str | None) -> tuple[Path, Path]:
    """返回 (菜单树快照, 生成的对照表) 两个路径。"""
    from .menu_catalog import MENU_DOC, MENU_TREE_JSON, find_repo_root

    base = Path(root).resolve() if root else find_repo_root()
    return base / MENU_TREE_JSON, base / MENU_DOC


def _load_menu_tree(root: str | None):
    """读 `docs/menu.tree.json`；没有就报一个能照着做的错。"""
    import json as _json

    from .core.menu import tree_to_items

    tree_path, _doc = _menu_paths(root)
    if not tree_path.is_file():
        raise ErpError(
            f"找不到菜单树快照 {tree_path}\n"
            f"  先抓一次：erp menu --capture（会写 {tree_path}）"
        )
    data = _json.loads(tree_path.read_text(encoding="utf-8"))
    return data, tree_to_items(data.get("tree"))


def _print_menu_tree(tree) -> None:
    """缩进打印菜单树，带 QXID 与接入标记。"""
    from .menu_catalog import PAGE_CAPABILITIES

    connected = {p for p, caps in PAGE_CAPABILITIES.items() if caps}
    pages = 0

    def render(nodes, prefix: str = "") -> None:
        nonlocal pages
        for index, node in enumerate(nodes):
            last = index == len(nodes) - 1
            branch = "└─ " if last else "├─ "
            tail = ""
            if node.is_page:
                pages += 1
                mark = "✅" if node.page_path in connected else "⬜"
                tail = f"  {node.qxid or '-':>6}  {mark}  {node.page_path}"
            print(f"{prefix}{branch}{node.label}{tail}")
            render(node.children, prefix + ("   " if last else "│  "))

    render(tree)
    print(f"\n菜单页 {pages} 个，已接入 {len(connected)} 个页面")


def cmd_menu(args) -> int:
    """抓菜单树 / 生成「菜单 → 接口」对照表。"""
    import time

    from . import menu_catalog as mc
    from .core.auth import MENU_AJAX_PATH
    from .core.menu import duplicate_paths, menu_pages, parse_menu_tree, qxid_map

    tree_path, doc_path = _menu_paths(args.root)

    # ── 抓快照 ──
    if args.capture:
        cfg = _build_config(args)
        auth = _build_auth(cfg)
        auth.ensure_login()
        uid = auth.user_id
        if not uid:
            _log("[ERROR] 拿不到用户 ID，无法抓菜单")
            return 2
        resp = auth.client.get(
            f"{MENU_AJAX_PATH}?FunName=GetMenuTree&Value1={uid}&Value2=",
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": auth.client.url("/Admin/index.html")},
        )
        tree = parse_menu_tree(resp.text)
        meta = {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "account": cfg.username,
            "login_user": uid,
            "source": f"GET {MENU_AJAX_PATH}?FunName=GetMenuTree&Value1={uid}&Value2=",
            "note": "菜单按账号权限渲染；换账号/权限变动后请重新 erp menu --capture",
            "tree": [item.to_dict() for item in tree],
        }
        tree_path.parent.mkdir(parents=True, exist_ok=True)
        tree_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        _log(f"[OK] 菜单树已写入 {tree_path}（{len(menu_pages(tree))} 个菜单页）")

    # ── 读快照 ──
    if not (args.write or args.check or args.tree or args.coverage):
        args.coverage = True
    meta, tree = None, None
    if args.write or args.check or args.tree or args.coverage:
        meta, tree = _load_menu_tree(args.root)

    if args.tree:
        _print_menu_tree(tree)

    if args.write:
        text = mc.render_markdown(tree, meta or {})
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(text, encoding="utf-8")
        _log(f"[OK] 已生成 {doc_path}")
        stats = mc.coverage(tree)
        _log(f"     {stats['modules']} 个模块 / {stats['pages']} 个菜单页 / "
             f"已接入 {stats['connected_pages']} 个页面 · {stats['capabilities']} 个接口")

    if args.check:
        text = mc.render_markdown(tree, meta or {})
        current = doc_path.read_text(encoding="utf-8") if doc_path.is_file() else ""
        if current != text:
            _log(f"[DRIFT] {doc_path} 与生成结果不一致，跑 `erp menu --write` 重新生成")
            return 1
        _log(f"[OK] {doc_path} 与代码一致")

    if args.coverage:
        stats = mc.coverage(tree)
        print(f"模块        {stats['modules']}")
        print(f"菜单页      {stats['pages']}（唯一路径 {stats['unique_paths']}）")
        print(f"已接入页面  {stats['connected_pages']}")
        print(f"未接入页面  {stats['unconnected']}")
        print(f"已实现接口  {stats['capabilities']}")
        print()
        print("按模块：")
        for row in mc.module_stats(tree):
            print(f"  {row['module']:<8} 菜单页 {row['pages']:>2}  已接入 {row['connected']:>2}"
                  f"  未接入 {row['missing']:>2}")
        dups = duplicate_paths(tree)
        if dups:
            print()
            print("⚠ 同一页面挂在多个菜单下（QXID 按菜单项区分）：")
            for path, qxids in dups.items():
                print(f"  {path}  →  {'/'.join(qxids)}")
        if not args.tree and not args.write:
            print()
            print(f"明细见 {doc_path}（跑 erp menu --write 生成）")
    return 0


def cmd_tools(args) -> int:
    for item in describe_tools():
        print(f"{item['name']:<12} {item['summary']}")
        for opt in item["options"]:
            flag = opt["cli"] or f"--{opt['name']}"
            req = " (必填)" if opt["required"] else ""
            default = "" if opt["default"] in (None, "") else f" 默认={opt['default']}"
            print(f"    {flag:<16} {opt['help']}{req}{default}")
        print()
    return 0


def cmd_config(args) -> int:
    from .core.paths import describe_paths
    from .output.markdown import display_width

    def line(key: str, value: str) -> str:
        return key + " " * max(1, 12 - display_width(key)) + value

    cfg = _build_config(args)
    for key, value in describe_paths().items():
        print(line(key, value))
    print(line("账号", cfg.username or "(未设置)"))
    print(line("ERP 地址", cfg.host))
    print(line("验证码", cfg.captcha_host))
    print(line("并发上限", str(cfg.max_concurrency)))
    problems = cfg.validate()
    if problems:
        print()
        for item in problems:
            print(f"[WARN] {item}")
    return 0


def cmd_doctor(args) -> int:
    """连通性 / 配置自检：端口、登录页、验证码、cookie。"""
    import socket
    import time as _time
    from urllib.parse import urlsplit

    from .core.auth import AuthManager
    from .core.paths import describe_paths
    from .output.markdown import display_width

    def line(key: str, value: str) -> str:
        return key + " " * max(1, 12 - display_width(key)) + value

    cfg = _build_config(args)
    print("== 配置 ==")
    for key, value in describe_paths().items():
        print(line(key, value))
    print(line("ERP 主站", cfg.host))
    print(line("验证码", cfg.captcha_host))
    print(line("账号", cfg.username or "(未设置)"))
    print(line("密码", "***" if cfg.password else "(未设置)"))

    problems = cfg.validate()
    if problems:
        print("\n== 配置疑点 ==")
        for item in problems:
            print(f"  [WARN] {item}")

    print("\n== 连通性 ==")
    ok = True

    def tcp(label: str, url: str) -> None:
        nonlocal ok
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        started = _time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=4):
                pass
            ms = (_time.perf_counter() - started) * 1000
            print(f"  [OK]   TCP {host}:{port}（{label}） {ms:.0f} ms")
        except OSError as exc:
            ok = False
            print(f"  [FAIL] TCP {host}:{port}（{label}） {exc}")

    tcp("ERP 主站", cfg.host)
    tcp("验证码服务", cfg.captcha_host)

    client = ErpClient(cfg)
    auth = AuthManager(client, interactive=False)
    try:
        resp = client.get("/Admin/Login.aspx?JudgeUrl=1&time=1")
        mark = "[OK]  " if resp.status_code == 200 else "[FAIL]"
        ok = ok and resp.status_code == 200
        print(f"  {mark} GET 登录页  HTTP {resp.status_code}  {len(resp.text)} 字节")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [FAIL] GET 登录页  {exc}")

    try:
        import requests

        img = requests.get(f"{cfg.captcha_host.rstrip('/')}/ValidateCode.aspx",
                           headers={"User-Agent": client.session.headers["User-Agent"],
                                    "Connection": "close"},
                           timeout=(5, 30))
        jpeg = img.content[:2] == b"\xff\xd8"
        mark = "[OK]  " if img.status_code == 200 and jpeg else "[FAIL]"
        ok = ok and mark == "[OK]  "
        print(f"  {mark} GET 验证码  HTTP {img.status_code}  {len(img.content)} 字节"
              f"{'  JPEG' if jpeg else ''}")
        if img.status_code == 200 and jpeg:
            code = auth._try_ocr(img.content)
            if code:
                print(f"  [OK]   验证码自动识别：{code}")
            else:
                print("  [WARN] 验证码自动识别不可用（将需人工输入）")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"  [FAIL] GET 验证码  {exc}")

    print("\n== 登录态 ==")
    cookie_file = auth.cookie_file
    if cookie_file.is_file():
        auth.load_cookies()
        if auth.is_logged_in():
            print(f"  [OK]   cookie 有效（用户 ID {auth.user_id or '未知'}）：{cookie_file}")
        else:
            print(f"  -      cookie 已过期，需重新登录：{cookie_file}")
    else:
        print(f"  -      还没有 cookie 缓存：{cookie_file}")

    print()
    if not ok:
        print("结论：有端点访问失败，检查 ERP 地址/端口/防火墙（上面标 [FAIL] 的那项）。")
    elif problems:
        print("结论：网络正常；上面是配置提示，登录失败时优先核对 host 与账号密码。")
    else:
        print("结论：一切正常，登录与验证码识别均已就绪（无需人工干预）。")
    return 0 if ok else 1


def cmd_setup(args) -> int:
    """首次配置向导：写 config.yaml（并可选立即登录缓存 cookie）。"""
    from .core.paths import app_data_dir, preferred_config_path
    from .core.settings import update_yaml

    cfg = _build_config(args)
    target = Path(args.config) if args.config else preferred_config_path()
    print("═" * 56)
    print(" erp-tools 配置向导        （直接回车沿用括号内默认值）")
    print("═" * 56)
    print(f" 配置文件将写入：{target}")
    print(f" 数据目录      ：{app_data_dir()}")
    print()

    try:
        host = input(f"ERP 地址  [{cfg.host}]: ").strip() or cfg.host
        user = input(f"工号     [{cfg.username or ''}]: ").strip() or cfg.username
        password = getpass.getpass("密码     [留空则不修改]: ").strip()
    except EOFError:
        _log("[ERROR] 当前不是交互式终端，无法运行向导；可直接编辑 config.yaml")
        return 2

    erp_section: dict[str, str] = {"host": host}
    if user:
        erp_section["username"] = user
    if password:
        erp_section["password"] = password
    update_yaml(target, {"erp": erp_section})
    _log(f"[OK] 已写入 {target}")

    if not password:
        _log("[提示] 未修改密码；如需要可编辑 config.yaml 或使用 --password")
        return 0

    if input("现在就登录并缓存 cookie？[Y/n]: ").strip().lower() in ("", "y", "yes"):
        new_cfg = ErpConfig(**{**cfg.__dict__, **{
            "host": host, "captcha_host": "", "username": user, "password": password,
        }})
        auth = _build_auth(new_cfg)
        auth.ensure_login(force=True)
        _log(f"[OK] 登录成功，cookie 已缓存到 {new_cfg.cookie_dir}")
    return 0


def cmd_quote(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool("quote", auth)

    count = 0
    for _record in tool.run(
        csv=args.csv,
        ids=args.id,
        bdbh=args.bdbh,
        out=args.out,
        limit=args.limit,
        workers=args.workers,
        resume=not args.no_resume,
        progress_every=args.progress_every,
    ):
        count += 1
    _log(f"[DONE] 采集完成：{count} 条")
    return 0


def cmd_query(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool("query", auth)

    count = 0
    for _row in tool.run(
        from_date=args.from_date,
        to_date=args.to_date,
        key=args.key or "",
        field=args.field or "",
        where=args.where,
        customer=args.customer,
        sales=args.sales,
        status=args.status,
        columns=args.columns,
        out=args.out,
        jsonl=args.jsonl,
        limit=args.limit,
        page_size=args.page_size,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
    ):
        count += 1
    _log(f"[DONE] 返回 {count} 条")
    return 0


def cmd_quote_attachment(args) -> int:
    """报价需求附件：--list（默认）/ 上传 / 下载。"""
    from .tools.attachments import decode_attachments
    from .tools.quote_create.service import QuoteCreateService

    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    svc = QuoteCreateService(auth)
    record_id = (args.id or "").strip()

    if args.download:
        data = svc.download_attachment(record_id, name=args.download,
                                       out_dir=args.out_dir or "")
        _log(f"[DONE] {data['名称']} ({data['size']} 字节) → "
             f"{data.get('path') or 'base64（见 JSON）'}")
        if not args.out_dir:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    items = []
    if args.file:
        items.extend(decode_attachments(args.file))
    if args.text:
        items.extend(decode_attachments([{"filename": args.name or "note.txt",
                                          "content_text": args.text,
                                          "name": args.name or "",
                                          "desc": args.desc or "",
                                          "remark": args.remark or ""}]))
    if items:
        if args.name and len(items) == 1:
            items[0].name = args.name
        for item in items:
            item.desc = item.desc or (args.desc or "")
            item.remark = item.remark or (args.remark or "")
        result = svc.upload_attachments(record_id, items)
        for item in result.get("uploaded") or []:
            _log(f"  [OK]   {item['name']}  ({item['size']} 字节)")
        for item in result.get("errors") or []:
            _log(f"  [FAIL] {item['filename']}: {item['error']}")
        _log(f"[DONE] 上传 {len(result.get('uploaded') or [])} 个，"
             f"回读{'成功' if result.get('verified') else '未通过'}")
        if not result.get("ok"):
            return 1

    listing = svc.list_attachments(record_id, with_urls=args.urls)
    _log(f"[INFO] 当前 {listing['count']} 个附件")
    for row in listing["files"]:
        line = f"  {row['排序号']:>2s} {row['名称']}"
        if row.get("描述"):
            line += f" | {row['描述']}"
        if row.get("url"):
            line += f"\n       {row['url']}"
        _log(line)
    if args.out_dir and listing["files"]:
        _log("[INFO] 已指定 --out-dir，逐个下载…")
        for row in listing["files"]:
            svc.download_attachment(record_id, file_id=row["文件ID"],
                                    out_dir=args.out_dir)
    return 0


def cmd_price(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool("price", auth)

    count = 0
    for _row in tool.run(
        from_date=args.from_date,
        to_date=args.to_date,
        date_type=args.date_type,
        key=args.key or "",
        field=args.field or "",
        key1=args.key1 or "",
        field1=args.field1 or "",
        where=args.where,
        customer=args.customer,
        sales=args.sales,
        status=args.status,
        filters=args.filter,
        columns=args.columns,
        out=args.out,
        jsonl=args.jsonl,
        limit=args.limit,
        page_size=args.page_size,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
    ):
        count += 1
    _log(f"[DONE] 返回 {count} 行")
    return 0


def cmd_price_detail(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool("price-detail", auth)

    count = 0
    for _record in tool.run(
        record_id=args.id,
        bdbh=args.bdbh,
        row=args.row,
        out=args.out,
        no_description=args.no_description,
    ):
        count += 1
    _log(f"[DONE] 取到 {count} 条详情")
    return 0


def cmd_price_node(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool("price-node", auth)

    count = 0
    for _record in tool.run(
        record_id=args.id,
        node=args.node,
        cpid=args.cpid,
        bjid=args.bjid,
        index=args.index,
        cpslid=args.cpslid,
        out=args.out,
    ):
        count += 1
    _log(f"[DONE] {count} 个节点")
    return 0


def cmd_attendance(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool("attendance", auth)

    count = 0
    for _row in tool.run(
        month=args.month,
        start_day=args.start_day,
        end_day=args.end_day,
        users=args.emp,
        out=args.out,
        jsonl=args.jsonl,
        page_size=args.page_size,
    ):
        count += 1
    _log(f"[DONE] 共 {count} 行")
    return 0


# ══════════════════ 三个模块（施工单 / 采购订单 / 产品核价） ══════════════════
def cmd_pages(args) -> int:
    """打印「数据页面清单」：列表页 / 详情页 / 每个弹窗子页。"""
    from .tools.grid_module import MODULES, get_module

    specs = [get_module(args.module)] if args.module else list(MODULES.values())
    for spec in specs:
        print(f"\n=== {spec.label}（{spec.key}）")
        print(f"  列表 {spec.list_path}  QXID={spec.fallback_qxid}")
        print(f"  详情 {spec.detail_path or '(无)'}")
        print(f"  {spec.row_note}")
        if spec.filter_controls:
            print("  服务端过滤：" + "、".join(
                f"{v}({k})" for k, v in spec.filter_controls.items()))
        if spec.date_fields:
            print("  日期字段：" + "、".join(
                f"{k}={v}" for k, v in spec.date_fields.items()))
        if spec.nodes:
            print(f"  弹窗子页 {len(spec.nodes)} 个：")
            for node, (page, button, label) in spec.nodes.items():
                print(f"    {node:18s} {page:28s} {button:18s} {label}")
        else:
            print("  弹窗子页：无（数据在详情页的内联网格里）")
    return 0


def cmd_grid_list(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool(args.command, auth)

    count = 0
    for _row in tool.run(
        from_date=args.from_date,
        to_date=args.to_date,
        date_type=getattr(args, "date_type", "") or "",
        key=args.key or "",
        field=args.field or "",
        key1=getattr(args, "key1", "") or "",
        field1=getattr(args, "field1", "") or "",
        where=args.where,
        filters=args.filter,
        columns=args.columns,
        out=args.out,
        jsonl=getattr(args, "jsonl", None),
        limit=args.limit,
        page_size=args.page_size,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
    ):
        count += 1
    _log(f"[DONE] 返回 {count} 行")
    return 0


def _write_json(path_str: str, data) -> None:
    """写 JSON（.jsonl 结尾写单行）。"""
    import json as _json
    from pathlib import Path

    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        path.write_text(_json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        path.write_text(_json.dumps(data, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")


def cmd_grid_detail(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool(args.command, auth)

    rows = tool.resolve_records(record_id=args.id or "", number=args.no or "")
    data = tool.fetch_one(rows[0]["记录ID"], include_texts=not args.no_texts,
                          raw=args.raw)
    if args.out:
        _write_json(args.out, data)
        _log(f"[DONE] → {args.out}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_grid_node(args) -> int:
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool(args.command, auth)

    if args.node.lower() in ("list", "ls", "nodes"):
        nodes = tool.catalog(args.id)
        print(json.dumps({"nodes": nodes}, ensure_ascii=False, indent=2, default=str))
        return 0
    data = tool.fetch(args.id, tool.resolve_node(args.node))
    if args.out:
        _write_json(args.out, data)
        _log(f"[DONE] → {args.out}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_grid_attachment(args) -> int:
    """客户订单附件：列表页「表单编号」左击调出的文件。"""
    cfg = _build_config(args)
    auth = ensure_authenticated(cfg, interactive=sys.stdin.isatty())
    tool = build_tool(args.command, auth)

    rows = tool.attachments(
        record_id=args.id or "", number=args.no or "",
        from_date=args.from_date, to_date=args.to_date,
        date_type=getattr(args, "date_type", "") or "",
        where=args.where, filters=args.filter, limit=args.limit,
        page_size=args.page_size, out_dir=args.out_dir or "",
    )
    if not rows:
        _log("[WARN] 没找到附件（该区间内没有带附件的单据）")
    for row in rows:
        state = row.get("路径") or row.get("url") or row.get("note", "")
        _log(f"  {row.get('表单编号', ''):14s} {row.get('类型', ''):6s} {state}")
    _log(f"[DONE] {len(rows)} 个附件")
    if args.out:
        _write_json(args.out, rows)
        _log(f"[DONE] → {args.out}")
    return 0



# ══════════════════ 服务模式 ══════════════════
def cmd_serve(args) -> int:
    """启动 api-key 网关（认证 + 未来的 MCP Streamable HTTP 挂载点）。"""
    try:
        import uvicorn
    except ImportError:
        _log("[ERROR] 缺少服务端依赖：请执行 `uv sync --extra server`")
        return 2

    from .apps.server.app import build_state, create_app
    from .apps.server.config import ServerConfig

    server_cfg = ServerConfig.from_args(args)
    problems = server_cfg.validate()
    if problems:
        for item in problems:
            _log(f"[ERROR] {item}")
        return 2

    erp_cfg = _build_config(args)
    if (erp_cfg.password or "").strip():
        _log("[WARN] 检测到 ERP_PASSWORD 已配置：**服务端不会使用它**。"
             "每个使用者自己调 POST /v1/auth/login（或 `erp key`）传工号密码换 api-key，"
             "服务端只存 api-key 的 sha256。建议把密码从 .env / 环境变量里删掉。")
    state = build_state(server_cfg, erp_cfg)
    app = create_app(state)

    host, port = server_cfg.host, server_cfg.port
    _log(f"[INFO] 监听 {host}:{port}  TLS={'on' if server_cfg.tls_enabled else 'off'}  "
         f"全局并发={server_cfg.global_concurrency}")
    _log(f"[INFO] api-key 库：{server_cfg.db_path}")
    _log(f"[INFO] cookie 目录：{erp_cfg.cookie_dir}")
    _log("[INFO] 回环模式，仅本机可访问" if server_cfg.is_loopback else
         f"[INFO] 团队模式，允许 Host：{', '.join(server_cfg.allow_hosts)}")
    if not server_cfg.tls_enabled and not server_cfg.is_loopback:
        _log("[WARN] 已开 --allow-plaintext：本进程说 HTTP，"
             "api-key 的加密完全依赖上游反向代理，请确认代理已启用 TLS")
    if server_cfg.trust_proxy:
        _log("[INFO] 信任 X-Forwarded-For（仅当前面确实有反向代理时打开）")

    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_certfile=server_cfg.tls_cert or None,
        ssl_keyfile=server_cfg.tls_key or None,
        log_level="info",
    )
    return 0


# ══════════════════ 参数解析 ══════════════════
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", help="覆盖 ERP 地址，如 http://14.12.0.172:8889")
    common.add_argument("--user", "-u", help="ERP 工号")
    common.add_argument("--password", "-p", help="ERP 密码")
    common.add_argument("--env-file", default=None,
                        help="旧版 .env 路径（默认自动查找，优先级低于 config.yaml）")
    common.add_argument("--config", default=None,
                        help="config.yaml 路径（默认自动查找）")
    common.add_argument("--concurrency", type=int, help="全局并发上限")
    common.add_argument("--delay", type=float, help="全局最小请求间隔（秒）")

    parser = argparse.ArgumentParser(prog="erp", description="华博 ERP 工具聚合")
    parser.add_argument("--version", action="version",
                        version=f"erp-tools {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", parents=[common], help="登录并缓存 cookie / 探测 QXID")
    p.add_argument("--force", action="store_true", help="强制重新登录")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("qxid", parents=[common], help="探测并打印 页面->QXID 映射")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(func=cmd_qxid)

    p = sub.add_parser("tools", parents=[common], help="列出可用工具与选项")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("menu", parents=[common],
                       help="抓菜单树 / 生成「菜单 → 接口」对照表")
    p.add_argument("--capture", action="store_true",
                   help="重新抓 ERP 左侧菜单树 → docs/menu.tree.json")
    p.add_argument("--tree", action="store_true", help="打印菜单树（带 QXID 与接入标记）")
    p.add_argument("--coverage", action="store_true", help="只看覆盖率统计")
    p.add_argument("--write", action="store_true", help="生成 docs/erp-menu.md")
    p.add_argument("--check", action="store_true",
                   help="校验 docs/erp-menu.md 与代码一致（不一致退出码 1）")
    p.add_argument("--root", default=None, help="仓库根目录（默认当前目录）")
    p.set_defaults(func=cmd_menu)

    p = sub.add_parser("key", help="向网关换取 api-key（密码只用于这一次请求）")
    p.add_argument("--server", required=True,
                   help="网关地址，如 https://erp-tools.lan:8443")
    p.add_argument("--user", "-u", default=None, help="ERP 工号（缺省时交互输入）")
    p.add_argument("--password", "-p", default=None,
                   help="ERP 密码（建议省略，用交互输入以免留在 shell 历史里）")
    p.add_argument("--cacert", default=None,
                   help="自签证书的 CA 路径（不装系统信任时用它校验）")
    p.add_argument("--insecure", action="store_true",
                   help="跳过 TLS 证书校验（仅临时排查）")
    p.add_argument("--timeout", type=float, default=30.0, help="请求超时秒数，默认 30")
    p.set_defaults(func=cmd_key)

    p = sub.add_parser("config", parents=[common], help="显示配置来源与数据目录")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("doctor", parents=[common], help="自检：端口、登录页、验证码、cookie")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("setup", parents=[common], help="首次配置向导（写 .env + 缓存 cookie）")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("quote", parents=[common], help="采集客户报价需求单")
    p.add_argument("--csv", help="输入 CSV（列：id 或 记录ID；可带制单人）")
    p.add_argument("--id", action="append", help="直接指定记录 id，可重复")
    p.add_argument("--bdbh", action="append",
                   help="按表单编号取详情，可重复或逗号分隔（如 BJXQ261105013）")
    p.add_argument("--out", help="输出 JSONL 路径")
    p.add_argument("--limit", type=int, help="只处理前 N 条")
    p.add_argument("--workers", type=int, help="并发线程数（默认 ERP_MAX_CONCURRENCY）")
    p.add_argument("--no-resume", action="store_true", help="忽略已完成的记录，全部重跑")
    p.add_argument("--progress-every", type=int, default=100)
    p.set_defaults(func=cmd_quote)

    p = sub.add_parser("query", parents=[common],
                       help="查询客户报价需求列表（可导出 CSV）")
    p.add_argument("--from", dest="from_date", help="制单起始日期 YYYY-MM-DD（默认本月1号）")
    p.add_argument("--to", dest="to_date", help="制单结束日期 YYYY-MM-DD（默认今天）")
    p.add_argument("--key", help="服务端关键字")
    p.add_argument("--field", help="关键字字段中文名：客户名称/产品名称/香港单号/表单编号…")
    p.add_argument("--where", help="本地过滤：客户名称~ADG,报价状态=需求审批,制单人!=李东员")
    p.add_argument("--customer", help="客户名称包含")
    p.add_argument("--sales", help="业务员包含")
    p.add_argument("--status", help="报价状态包含")
    p.add_argument("--columns", help="只导出这些列，逗号分隔")
    p.add_argument("--out", help="导出 CSV（.jsonl 结尾则写 JSONL）")
    p.add_argument("--jsonl", help="额外写一份 JSONL")
    p.add_argument("--limit", type=int, help="最多返回多少条")
    p.add_argument("--page-size", type=int, dest="page_size",
                   help="每页记录数（默认 ERP_PAGE_SIZE_MAX）")
    p.add_argument("--max-pages", type=int, default=200, dest="max_pages")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="只看条件和命中数，不写文件")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("quote-attachment", parents=[common],
                       help="报价需求附件：列表 / 上传 / 下载")
    p.add_argument("--id", required=True, dest="id",
                   help="报价需求记录 ID（单据必须先保存）")
    p.add_argument("--file", action="append",
                   help="要上传的本地文件，可重复或逗号分隔；也可用 --text 传文本")
    p.add_argument("--text", help="直接作为附件的文本内容（配合 --name）")
    p.add_argument("--name", help="ERP 里的附件名称（默认用文件名）")
    p.add_argument("--desc", help="附件描述", default="")
    p.add_argument("--remark", help="备注", default="")
    p.add_argument("--urls", action="store_true", help="列表时一并取下载直链")
    p.add_argument("--download", help="按附件名称（或文件ID）下载一个附件")
    p.add_argument("--out-dir", dest="out_dir", help="下载到目录（不传则输出 base64）")
    p.set_defaults(func=cmd_quote_attachment)

    p = sub.add_parser("price", parents=[common],
                       help="查询客户报价列表（BJ_KHBJ；一行=一个报价产品行）")
    p.add_argument("--from", dest="from_date", help="日期区间起 YYYY-MM-DD（默认本月1号）")
    p.add_argument("--to", dest="to_date", help="日期区间止 YYYY-MM-DD（默认今天）")
    p.add_argument("--date-type", dest="date_type", default="制单日期",
                   help="日期区间作用于：制单日期（默认）/交货日期/离厂日期")
    p.add_argument("--key", help="服务端关键字")
    p.add_argument("--field", help="关键字字段中文名：客户名称/表单编号/产品名称…")
    p.add_argument("--key1", help="第二个服务端关键字")
    p.add_argument("--field1", help="第二个关键字字段中文名")
    p.add_argument("--where", help="本地过滤：客户名称~ADG,报价状态=已审批")
    p.add_argument("--customer", help="客户名称包含")
    p.add_argument("--sales", help="业务员包含")
    p.add_argument("--status", help="报价状态包含")
    p.add_argument("--filter", action="append",
                   help="服务端下拉过滤，如 审批状态=已全审批（可重复）")
    p.add_argument("--columns", help="只导出这些列，逗号分隔")
    p.add_argument("--out", help="导出 CSV（.jsonl 结尾则写 JSONL）")
    p.add_argument("--jsonl", help="额外写一份 JSONL")
    p.add_argument("--limit", type=int, help="最多返回多少条")
    p.add_argument("--page-size", type=int, dest="page_size",
                   help="每页记录数（默认 ERP_PAGE_SIZE_MAX）")
    p.add_argument("--max-pages", type=int, default=200, dest="max_pages")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="只看条件和命中数，不写文件")
    p.set_defaults(func=cmd_price)

    p = sub.add_parser("price-detail", parents=[common],
                       help="客户报价【详情】（一个报价产品行）→ JSON")
    p.add_argument("--id", dest="id", help="记录 ID（列表页的 记录ID）")
    p.add_argument("--bdbh", help="表单编号，如 KHBJ261112003-01；也可只给前缀 KHBJ261112003")
    p.add_argument("--row", type=int, help="配合 --bdbh：取第几行（1 起）")
    p.add_argument("--out", help="输出 JSON（.jsonl 结尾则写 JSONL）")
    p.add_argument("--no-description", action="store_true", dest="no_description",
                   help="不输出产品描述长文本")
    p.set_defaults(func=cmd_price_detail)

    p = sub.add_parser("price-node", parents=[common],
                       help="客户报价子数据（数量档/部件/工序/辅料/费项/反馈/开版/折叠面板…）")
    p.add_argument("--id", dest="id", required=True, help="记录 ID（列表的 记录ID）")
    p.add_argument("--node", required=True,
                   help="quantities/part/part_materials/part_process/part_machines/"
                        "fees/other_fees/cp/cp_process/cp_materials/feedback/sgkb/"
                        "split_price/product_check；panel:<名称>=折叠面板；"
                        "list=列出全部节点；all=整棵树")
    p.add_argument("--cpid", help="产品明细 ID（不传用详情页里的）")
    p.add_argument("--bjid", help="部件 ID（部件级节点）")
    p.add_argument("--index", type=int, help="取第几个部件（1 起）")
    p.add_argument("--cpslid", help="数量档 ID（默认 fees=-1 汇总、other_fees=第一档）")
    p.add_argument("--out", help="输出 JSON 路径")
    p.set_defaults(func=cmd_price_node)

    # ---------- 三个新模块：产品核价 / 生产施工单 / 采购订单 ----------
    p = sub.add_parser("pages", parents=[common],
                       help="打印数据页面清单（列表/详情/弹窗子页）")
    p.add_argument("--module", default=None,
                   help="模块：costing / production / purchase；留空=全部")
    p.set_defaults(func=cmd_pages)

    def _add_list_args(parser, *, date_type: str | None) -> None:
        parser.add_argument("--from", dest="from_date", help="日期区间起 YYYY-MM-DD（默认本月1号）")
        parser.add_argument("--to", dest="to_date", help="日期区间止 YYYY-MM-DD（默认今天）")
        if date_type is not None:
            parser.add_argument("--date-type", dest="date_type", default=date_type,
                                help=f"日期区间作用于：{date_type}（默认）等")
        parser.add_argument("--key", help="服务端关键字")
        parser.add_argument("--field", help="关键字字段中文名")
        parser.add_argument("--key1", help="第二个服务端关键字")
        parser.add_argument("--field1", help="第二个关键字字段中文名")
        parser.add_argument("--where", help="本地过滤：生产单号~SGD2611,工单状态=已审批")
        parser.add_argument("--filter", action="append",
                            help="服务端下拉过滤，如 工单状态=已审批（可重复）")
        parser.add_argument("--columns", help="只导出这些列，逗号分隔")
        parser.add_argument("--out", help="导出 CSV（.jsonl 结尾则写 JSONL）")
        parser.add_argument("--jsonl", help="额外写一份 JSONL")
        parser.add_argument("--limit", type=int, help="最多返回多少条")
        parser.add_argument("--page-size", type=int, dest="page_size")
        parser.add_argument("--max-pages", type=int, default=200, dest="max_pages")
        parser.add_argument("--dry-run", action="store_true", dest="dry_run")
        parser.set_defaults(func=cmd_grid_list)

    p = sub.add_parser("costing-list", parents=[common],
                       help="查询产品核价列表（BJ_KHBJ，QXID=1281；一行=一个核价产品行）")
    _add_list_args(p, date_type="制单日期")

    p = sub.add_parser("production-list", parents=[common],
                       help="查询生产施工单列表（SC_SCSGD/Index.aspx）")
    _add_list_args(p, date_type="制单日期")

    p = sub.add_parser("purchase-list", parents=[common],
                       help="查询采购订单列表（CG_CGDD/Index.aspx；一行=一条材料明细）")
    _add_list_args(p, date_type=None)

    p = sub.add_parser("sales-order-list", parents=[common],
                       help="查询客户订单列表（DD_KHDD/Index.aspx，QXID=687）")
    _add_list_args(p, date_type="制单日期")

    p = sub.add_parser("sales-order-attachment", parents=[common],
                       help="客户订单附件（列表页「表单编号」左击调出的文件）")
    p.add_argument("--id", help="记录 ID（列表的 记录ID）")
    p.add_argument("--no", help="表单编号，如 KHDD26110042")
    p.add_argument("--from", dest="from_date", help="日期区间起 YYYY-MM-DD（默认本月1号）")
    p.add_argument("--to", dest="to_date", help="日期区间止 YYYY-MM-DD（默认今天）")
    p.add_argument("--date-type", dest="date_type", default="制单日期",
                   help="日期区间作用于：制单日期（默认）/ 订单日期 / 离厂日期 / 交货日期")
    p.add_argument("--where", help="本地过滤，如 表单编号~KHDD2611")
    p.add_argument("--filter", action="append", help="服务端下拉过滤，如 订单状态=编辑中")
    p.add_argument("--limit", type=int, help="最多处理多少条")
    p.add_argument("--page-size", type=int, dest="page_size")
    p.add_argument("--out-dir", dest="out_dir", help="下载到这个目录（不传则只返回链接）")
    p.add_argument("--out", help="把结果清单写成 JSON")
    p.set_defaults(func=cmd_grid_attachment)

    for name, label in (("production", "生产施工单"), ("purchase", "采购订单"),
                        ("sales-order", "客户订单")):
        p = sub.add_parser(f"{name}-detail", parents=[common],
                           help=f"{label}【详情】→ JSON")
        p.add_argument("--id", dest="id", help="记录 ID（列表的 记录ID）")
        p.add_argument("--no", dest="no", help="单号（生产单号 / 表单编号）")
        p.add_argument("--out", help="输出 JSON 路径")
        p.add_argument("--no-texts", action="store_true", dest="no_texts",
                       help="不返回备注类文本域")
        p.add_argument("--raw", action="store_true", help="附加原始 HTML（调试用）")
        p.set_defaults(func=cmd_grid_detail)

        p = sub.add_parser(f"{name}-node", parents=[common],
                           help=f"{label}子数据（弹窗子页）→ JSON")
        p.add_argument("--id", dest="id", required=True, help="记录 ID")
        p.add_argument("--node", required=True, help="节点名；list=列出全部节点")
        p.add_argument("--out", help="输出 JSON 路径")
        p.set_defaults(func=cmd_grid_node)

    p = sub.add_parser("attendance", parents=[common], help="查询月度考勤")
    p.add_argument("--month", "-m", required=True, help="月份，如 2026-11")
    p.add_argument("--from", dest="start_day", default="01", help="起始日（01-30，默认 01）")
    p.add_argument("--to", dest="end_day", default="30",
                   help="结束日（01-30，默认 30；ERP 页面没有 31，传 31 只会回 1 号一行）")
    p.add_argument("--emp", dest="emp", action="append",
                   help="工卡号（可写 工号:密码），可重复；默认当前账号")
    p.add_argument("--out", help="Markdown 输出路径")
    p.add_argument("--jsonl", help="JSONL 输出路径")
    p.add_argument("--page-size", type=int, help="单页记录数（默认 ERP_PAGE_SIZE_MAX）")
    p.set_defaults(func=cmd_attendance)

    p = sub.add_parser("serve", parents=[common],
                       help="启动 api-key 网关（MCP Streamable HTTP）")
    p.add_argument("--bind", default=None,
                   help="监听地址，默认 127.0.0.1:8765；非回环地址必须配 --tls-cert/--tls-key，"
                        "或显式 --allow-plaintext（TLS 由上游代理终止时）")
    p.add_argument("--db", default=None, help="api-key 库路径，默认 <数据目录>/auth.db")
    p.add_argument("--global-concurrency", type=int, dest="global_concurrency",
                   default=None, help="全局 ERP 并发上限，默认 6（实测 4~8 饱和）")
    p.add_argument("--tls-cert", dest="tls_cert", default=None, help="TLS 证书（团队模式必填）")
    p.add_argument("--tls-key", dest="tls_key", default=None, help="TLS 私钥（团队模式必填）")
    p.add_argument("--allow-host", dest="allow_host", action="append",
                   help="允许的 Host，可重复（团队模式必填，如 erp-tools.lan:8765）")
    p.add_argument("--trust-proxy", dest="trust_proxy", action="store_true",
                   help="信任 X-Forwarded-For（放在反向代理后面时打开）")
    p.add_argument("--allow-plaintext", dest="allow_plaintext", action="store_true",
                   help="显式允许非回环 + 明文 HTTP（仅当 TLS 由上游反向代理终止，如 docker compose + nginx）")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ErpError as exc:
        _log(f"[ERROR] {exc}")
        return 1
    except KeyboardInterrupt:
        _log("\n[中断] 已停止（已完成的结果不受影响）")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
