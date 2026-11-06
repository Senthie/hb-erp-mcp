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

    p = sub.add_parser("attendance", parents=[common], help="查询月度考勤")
    p.add_argument("--month", "-m", required=True, help="月份，如 2026-11")
    p.add_argument("--from", dest="start_day", default="01", help="起始日（默认 01）")
    p.add_argument("--to", dest="end_day", default="31", help="结束日（默认 31）")
    p.add_argument("--emp", dest="emp", action="append",
                   help="工卡号（可写 工号:密码），可重复；默认当前账号")
    p.add_argument("--out", help="Markdown 输出路径")
    p.add_argument("--jsonl", help="JSONL 输出路径")
    p.add_argument("--page-size", type=int, help="单页记录数（默认 ERP_PAGE_SIZE_MAX）")
    p.set_defaults(func=cmd_attendance)

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
