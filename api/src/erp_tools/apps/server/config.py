"""服务端配置（与 ERP 客户端配置分开）。

ERP 配置沿用 `ErpConfig`（host / 账号 / cookie 目录 / 并发）；
这里只负责「网关自己」的参数。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ...core.paths import app_data_dir
from ...runtime.pg_store import mask_dsn

DEFAULT_BIND = "127.0.0.1:8765"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _split_hosts(raw: str) -> list[str]:
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


@dataclass
class ServerConfig:
    #: `host:port`；host 为 `0.0.0.0` 时视为「团队模式」，必须配 TLS + allow-hosts
    bind: str = DEFAULT_BIND
    #: **生产存储**：PostgreSQL 连接串（`postgresql://user:pass@host:5432/db`）
    pg_dsn: str = ""
    #: **仅本地/测试**：显式给出时改用 SQLite 文件（`--db` / `ERPSRV_DB`）。
    #: 默认 None —— 不配就只走 PostgreSQL，避免「以为在用 PG，其实写进了本地文件」。
    db_path: Path | None = None
    #: 服务注册接口的共享密钥；留空表示注册接口不校验密钥（仅靠 IP 限流）
    register_secret: str = ""
    #: 是否开放平台账号自助注册（关掉后只能由管理员在库里建号）
    allow_signup: bool = True
    #: 管理员用户名白名单（逗号分隔）：这些账号注册/登录时自动获得 admin 角色
    admin_users: list[str] = field(default_factory=list)

    # ---------- 角色 ----------
    def is_admin_username(self, username: str) -> bool:
        name = (username or "").strip().lower()
        return bool(name) and name in {item.lower() for item in self.admin_users}
    #: 全局 ERP 并发上限（实测 4~8 饱和，默认 6）
    global_concurrency: int = 6
    tls_cert: str = ""
    tls_key: str = ""
    #: 团队模式下必须显式给出的 Host 白名单（防 DNS rebinding）
    allow_hosts: list[str] = field(default_factory=list)
    #: 是否信任 `X-Forwarded-For`（放在反向代理后面时才打开）
    trust_proxy: bool = False
    #: 显式允许「非回环 + 明文 HTTP」。**只给 TLS 由上游代理终止的场景用**
    #: （docker compose：nginx 持证书，应用只在内网 docker 网上跑 HTTP）。
    allow_plaintext: bool = False

    # ---------- 存储 ----------
    @property
    def store_backend(self) -> str:
        return "sqlite" if self.db_path is not None else "postgresql"

    @property
    def data_dir(self) -> Path:
        """运行时文件目录（`proxy.secret`、cookie 缓存等）。

        SQLite 模式下与库文件同目录；PostgreSQL 模式下用统一的数据目录。
        """
        if self.db_path is not None:
            return self.db_path.expanduser().resolve().parent
        return app_data_dir()

    # ---------- 解析 ----------
    @property
    def host(self) -> str:
        return self._split_bind()[0]

    @property
    def port(self) -> int:
        return self._split_bind()[1]

    def _split_bind(self) -> tuple[str, int]:
        text = (self.bind or DEFAULT_BIND).strip()
        if text.startswith("["):  # [::1]:8765
            host, _, rest = text.partition("]")
            port = rest.lstrip(":")
            return host.lstrip("["), int(port or 8765)
        if ":" in text:
            host, _, port = text.rpartition(":")
            return host or "127.0.0.1", int(port or 8765)
        return text, 8765

    @property
    def is_loopback(self) -> bool:
        host = self.host.lower()
        return host in _LOOPBACK_HOSTS or host.startswith("127.")

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    # ---------- 校验 ----------
    def validate(self) -> list[str]:
        problems: list[str] = []

        if not self.is_loopback:
            if not self.tls_enabled and not self.allow_plaintext:
                problems.append(
                    f"--bind {self.bind} 不是回环地址，必须同时提供 --tls-cert / --tls-key；"
                    "否则 api-key 会以明文在内网传输。"
                    "若 TLS 已由上游反向代理（nginx/ingress）终止，"
                    "请显式加 --allow-plaintext（或 ERPSRV_ALLOW_PLAINTEXT=1）"
                )
            if not self.allow_hosts:
                problems.append(
                    f"--bind {self.bind} 不是回环地址，必须显式给出 --allow-host（防 DNS rebinding），"
                    "例如 --allow-host erp-tools.lan:8765"
                )

        if bool(self.tls_cert) != bool(self.tls_key):
            problems.append("--tls-cert 与 --tls-key 必须成对出现")

        if self.global_concurrency < 1:
            problems.append("--global-concurrency 必须 >= 1")

        if not self.pg_dsn and self.db_path is None:
            problems.append(
                "未配置数据库：请设置 ERPSRV_PG_DSN（PostgreSQL 连接串，"
                "如 postgresql://erp:secret@127.0.0.1:5432/erp），"
                "或显式加 --db <sqlite 文件路径>（仅本地开发/测试）"
            )
        elif self.pg_dsn and not self.pg_dsn.lower().startswith(
            ("postgresql://", "postgres://")
        ):
            problems.append(
                "ERPSRV_PG_DSN 必须是 postgresql:// 或 postgres:// 开头的连接串"
            )

        for item in self.allow_hosts:
            if "/" in item or " " in item:
                problems.append(f"--allow-host 取值非法：{item!r}（示例：erp-tools.lan:8765）")
        return problems

    # ---------- 构造 ----------
    @classmethod
    def from_env(cls) -> "ServerConfig":
        sqlite_env = _env("ERPSRV_DB")
        return cls(
            bind=_env("ERPSRV_BIND", DEFAULT_BIND),
            pg_dsn=_env("ERPSRV_PG_DSN"),
            db_path=Path(sqlite_env) if sqlite_env else None,
            register_secret=_env("ERPSRV_REGISTER_SECRET"),
            allow_signup=_env("ERPSRV_ALLOW_SIGNUP", "1").lower() not in (
                "0", "false", "no", "off"
            ),
            admin_users=_split_hosts(_env("ERPSRV_ADMIN_USERS")),
            global_concurrency=_env_int("ERPSRV_CONCURRENCY", 6),
            tls_cert=_env("ERPSRV_TLS_CERT"),
            tls_key=_env("ERPSRV_TLS_KEY"),
            allow_hosts=_split_hosts(_env("ERPSRV_ALLOW_HOST")),
            trust_proxy=_env("ERPSRV_TRUST_PROXY").lower() in ("1", "true", "yes", "on"),
            allow_plaintext=_env("ERPSRV_ALLOW_PLAINTEXT").lower() in ("1", "true", "yes", "on"),
        )

    @classmethod
    def from_args(cls, args) -> "ServerConfig":
        cfg = cls.from_env()
        if getattr(args, "bind", None):
            cfg.bind = str(args.bind).strip()
        if getattr(args, "pg_dsn", None):
            cfg.pg_dsn = str(args.pg_dsn).strip()
            # 显式给 PG 时以 PG 为准：命令行 --db 不再生效
            cfg.db_path = None
        if getattr(args, "db", None):
            cfg.db_path = Path(args.db)
        if getattr(args, "register_secret", None):
            cfg.register_secret = str(args.register_secret).strip()
        if getattr(args, "no_signup", False):
            cfg.allow_signup = False
        if getattr(args, "admin_user", None):
            cfg.admin_users = [str(name).strip().lower() for name in args.admin_user
                               if str(name).strip()]
        if getattr(args, "global_concurrency", None):
            cfg.global_concurrency = int(args.global_concurrency)
        if getattr(args, "tls_cert", None):
            cfg.tls_cert = str(args.tls_cert).strip()
        if getattr(args, "tls_key", None):
            cfg.tls_key = str(args.tls_key).strip()
        if getattr(args, "allow_host", None):
            cfg.allow_hosts = [str(h).strip() for h in args.allow_host if str(h).strip()]
        if getattr(args, "trust_proxy", False):
            cfg.trust_proxy = True
        if getattr(args, "allow_plaintext", False):
            cfg.allow_plaintext = True
        return cfg

    def describe(self) -> dict[str, str]:
        return {
            "bind": self.bind,
            "store": (f"postgresql({mask_dsn(self.pg_dsn)})"
                      if self.db_path is None else f"sqlite({self.db_path})"),
            "global_concurrency": str(self.global_concurrency),
            "tls": "on" if self.tls_enabled else ("plaintext(behind proxy)" if self.allow_plaintext else "off"),
            "allow_hosts": ",".join(self.allow_hosts) or "(回环模式，无需配置)",
            "trust_proxy": "on" if self.trust_proxy else "off",
            "register_secret": "on" if self.register_secret else "off",
            "signup": "on" if self.allow_signup else "off",
            "admin_users": ",".join(self.admin_users) or "(未配置)",
        }


__all__ = ["DEFAULT_BIND", "ServerConfig"]
