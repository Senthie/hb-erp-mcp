"""`erp init` 的配置生成逻辑（纯函数，方便测试）。

只有两种模式，各有自己的最小配置：

* ``cli`` —— 本机命令行直连 ERP：ERP 地址 + 工号（密码可留空，登录时再输入）。
* ``mcp`` —— 作为 MCP 网关：监听地址、存储、管理员，其余全部自动派生。

「需要先生成再回填」的项一律自动化，生成出来的 ``config.yaml`` 里只写 ``auto``：

* ``tls: auto``            —— 非回环监听时首次启动自动自签证书
* ``allow_hosts: auto``    —— 由 ``bind`` / ``public_host`` 推导
* ``register_secret: auto``—— 首次启动随机生成并落盘 ``<data>/register.secret``
* 存储为 PostgreSQL 时，DSN 直接由 host/port/user/password/db 拼好
* ``mcp.json``             —— 客户端配置模板；拿到 api-key 后
  ``erp key --write-mcp-json`` 会直接把真实 key 写进去，不用手抄
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

MODE_CLI = "cli"
MODE_MCP = "mcp"
MODES = (MODE_CLI, MODE_MCP)

STORAGE_SQLITE = "sqlite"
STORAGE_POSTGRES = "postgres"

#: server 段里「填 auto 就自动搞定」的键
AUTO = "auto"

PG_DEFAULTS = {"host": "127.0.0.1", "port": 5432, "user": "erp", "db": "erp"}


def cli_config(
    host: str,
    *,
    username: str = "",
    password: str = "",
    cookie_dir: str = "",
) -> dict[str, Any]:
    """CLI 模式的最小配置：3~4 个键，其它全走默认值。"""
    erp: dict[str, Any] = {"host": (host or "").strip()}
    if username.strip():
        erp["username"] = username.strip()
    if password:
        erp["password"] = password
    data: dict[str, Any] = {"mode": MODE_CLI, "erp": erp}
    if cookie_dir.strip():
        data["runtime"] = {"cookie_dir": cookie_dir.strip()}
    return data


def assemble_pg_dsn(
    *,
    host: str,
    port: int | str,
    user: str,
    password: str,
    db: str,
) -> str:
    """拼 PostgreSQL 连接串；密码里的 URL 特殊字符按 percent-encoding 处理。"""
    from urllib.parse import quote

    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{int(port)}/{db}"
    )


def derive_allow_hosts(bind: str, public_host: str = "") -> list[str]:
    """由 ``bind``（和可选的对外地址）推导 Host 白名单，省掉手填。

    团队模式下客户端实际用的 Host 就是「本机 IP:端口」或对外域名，这里直接列出来；
    回环监听不需要白名单（返回空 = SDK 用自己的本机保护）。
    ``0.0.0.0`` / ``::`` 这类通配地址不能直接当 Host 用，换成实际可访问的地址。
    """
    text = (bind or "").strip()
    port = ""
    if text.startswith("["):  # [::1]:8765
        host, _, rest = text.partition("]")
        host, port = host.lstrip("["), rest.lstrip(":")
    elif ":" in text:
        host, _, port = text.rpartition(":")
    else:
        host = text

    hosts: list[str] = []
    loopback = host in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1", "::")
    if host and not loopback:
        wildcard = host in ("0.0.0.0",)
        if not wildcard:
            hosts.append(f"{host}:{port}" if port else host)
        else:
            from ..apps.server.tls import local_ipv4

            for addr in local_ipv4():
                hosts.append(f"{addr}:{port}" if port else addr)
            if port:
                hosts += [f"127.0.0.1:{port}", f"localhost:{port}"]

    for item in (public_host or "").replace(";", ",").split(","):
        value = item.strip()
        if value and value not in hosts:
            hosts.append(value)
    return hosts


def mcp_config(
    *,
    erp_host: str,
    bind: str,
    storage: str = STORAGE_SQLITE,
    sqlite_path: str = "",
    pg: dict[str, Any] | None = None,
    pg_dsn: str = "",
    admin_users: list[str] | None = None,
    public_host: str = "",
    trust_proxy: bool | None = None,
    allow_plaintext: bool | None = None,
    concurrency: int = 6,
) -> dict[str, Any]:
    """MCP 模式配置：只留「必须由人决定的」，其余自动。

    * ``tls`` / ``allow_hosts`` / ``register_secret`` 一律写 ``auto``，
      由 `erp serve` 启动时按需生成或派生；
    * 存储只写 ``storage`` + 一个路径或一个 DSN，不再两套变量二选一；
    * 给了 ``public_host`` 说明前面有反向代理：默认信任 ``X-Forwarded-For``
      并把 TLS 交给代理（``allow_plaintext``）；需要自己签 TLS 时显式传 False。
    """
    has_proxy = bool((public_host or "").strip())
    server: dict[str, Any] = {
        "bind": (bind or "127.0.0.1:8765").strip(),
        "storage": STORAGE_POSTGRES if storage == STORAGE_POSTGRES else STORAGE_SQLITE,
        "tls": AUTO,
        "allow_hosts": AUTO,
        "register_secret": AUTO,
        "concurrency": max(1, int(concurrency)),
    }
    if server["storage"] == STORAGE_POSTGRES:
        dsn = (pg_dsn or "").strip()
        if not dsn:
            info = {**PG_DEFAULTS, **(pg or {})}
            dsn = assemble_pg_dsn(
                host=str(info["host"]),
                port=info["port"],
                user=str(info["user"]),
                password=str(info.get("password") or ""),
                db=str(info["db"]),
            )
        server["pg_dsn"] = dsn
    else:
        server["sqlite_path"] = (sqlite_path or "auth.db").strip()

    names = [name.strip().lower() for name in (admin_users or []) if str(name).strip()]
    if names:
        server["admin_users"] = names
    if (public_host or "").strip():
        server["public_host"] = public_host.strip()
    trust = has_proxy if trust_proxy is None else trust_proxy
    plain = has_proxy if allow_plaintext is None else allow_plaintext
    if trust:
        server["trust_proxy"] = True
    if plain:
        server["allow_plaintext"] = True
        server["tls"] = "off"  # TLS 由上游代理终止，自己不用签
    server["store_erp_password"] = True  # ERP 密码加密存库 → 会话过期自动重登

    return {"mode": MODE_MCP, "erp": {"host": (erp_host or "").strip()}, "server": server}


def server_base_url(server: dict[str, Any] | None = None) -> str:
    """由 `server` 段推导「用户该打开哪个地址」，避免再手填一遍。"""
    block = server or {}
    bind = str(block.get("bind") or "127.0.0.1:8765").strip()
    host_part, _, port = bind.rpartition(":")
    host = host_part.strip("[]") or "127.0.0.1"
    loopback = host in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")
    plaintext = bool(block.get("allow_plaintext"))
    scheme = "http" if (loopback or plaintext) else "https"

    public = str(block.get("public_host") or "").split(",")[0].strip()
    if public:
        if "://" in public:
            return public.rstrip("/")
        return f"{scheme}://{public}".rstrip("/")
    if loopback:
        return f"{scheme}://{host}:{port or 8765}"
    from ..apps.server.tls import local_ipv4

    ips = local_ipv4()
    shown = ips[0] if ips else host
    return f"{scheme}://{shown}:{port or 8765}"


def write_config(path: str | Path, data: dict[str, Any]) -> Path:
    """写 config.yaml：与已有内容**合并**（保留 qxid 等手工调过的段落）。"""
    import yaml

    from .settings import read_yaml

    p = Path(path).expanduser()
    merged = read_yaml(p)
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    try:  # 含 ERP 密码时只给当前用户读写
        p.chmod(0o600)
    except OSError:  # pragma: no cover - Windows 上可能不支持
        pass
    return p


def write_mcp_json(path: str | Path, base_url: str, api_key: str = "") -> Path:
    """写 MCP 客户端配置（VS Code / Claude 通用的 ``servers`` 结构）。

    ``api_key`` 留空时写占位符 —— 之后 ``erp key --write-mcp-json`` 会把真 key 填进去。
    """
    from ..apps.server.platform_routes import build_snippet

    snippet = build_snippet(base_url, api_key or "<你的 MCP token>", account="<你的平台账号>")
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    import json

    p.write_text(json.dumps(snippet["config"], ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


__all__ = [
    "AUTO",
    "MODE_CLI",
    "MODE_MCP",
    "MODES",
    "PG_DEFAULTS",
    "STORAGE_POSTGRES",
    "STORAGE_SQLITE",
    "assemble_pg_dsn",
    "cli_config",
    "derive_allow_hosts",
    "mcp_config",
    "server_base_url",
    "write_config",
    "write_mcp_json",
]
