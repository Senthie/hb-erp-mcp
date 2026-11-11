"""服务端配置（与 ERP 客户端配置分开）。

ERP 配置沿用 `ErpConfig`（host / 账号 / cookie 目录 / 并发）；
这里只负责「网关自己」的参数。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ...core.paths import app_data_dir

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
    #: api-key / 审计库
    db_path: Path = field(default_factory=lambda: app_data_dir() / "auth.db")
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

        for item in self.allow_hosts:
            if "/" in item or " " in item:
                problems.append(f"--allow-host 取值非法：{item!r}（示例：erp-tools.lan:8765）")
        return problems

    # ---------- 构造 ----------
    @classmethod
    def from_env(cls) -> "ServerConfig":
        return cls(
            bind=_env("ERPSRV_BIND", DEFAULT_BIND),
            db_path=Path(_env("ERPSRV_DB") or (app_data_dir() / "auth.db")),
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
        if getattr(args, "db", None):
            cfg.db_path = Path(args.db)
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
            "db": str(self.db_path),
            "global_concurrency": str(self.global_concurrency),
            "tls": "on" if self.tls_enabled else ("plaintext(behind proxy)" if self.allow_plaintext else "off"),
            "allow_hosts": ",".join(self.allow_hosts) or "(回环模式，无需配置)",
            "trust_proxy": "on" if self.trust_proxy else "off",
        }


__all__ = ["DEFAULT_BIND", "ServerConfig"]
