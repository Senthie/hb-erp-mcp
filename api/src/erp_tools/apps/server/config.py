"""服务端配置（MCP 模式）。只从 config.yaml 的 `server` 段读取。

配置由 `erp init --mode mcp` 一次生成，不再需要手工拼一堆环境变量：

```yaml
mode: mcp
erp:
  host: http://192.168.1.199:8889
server:
  bind: 0.0.0.0:8765
  storage: postgres          # postgres | sqlite
  pg_dsn: "postgresql://erp:***@db:5432/erp"
  admin_users: [root]
  tls: auto                  # auto = 非回环监听时自动签发自签证书；off = 不加密
  allow_hosts: auto          # auto = 从 bind / public_host 派生
  register_secret: auto      # auto = 首次启动随机生成并落盘
```

三个 `auto` 就是「生成物不回填」的实现：证书、Host 白名单、注册密钥都在启动时
自行生成或派生；命令行参数仍可临时覆盖。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from ...core.paths import app_data_dir
from ...runtime.pg_store import mask_dsn

DEFAULT_BIND = "127.0.0.1:8765"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}

#: `server` 段里表示「启动时自动搞定」的取值
AUTO = "auto"
#: 自动生成的注册密钥文件名（与 proxy.secret 同级）
SECRET_NAME = "register.secret"
#: 自动签发的证书目录名（在数据目录下）
TLS_DIR = "tls"


def _text(value: object) -> str:
    return str(value or "").strip()


def _split_hosts(raw: str | list | None) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in ("1", "true", "yes", "on")


@dataclass
class ServerConfig:
    #: `host:port`；host 为 `0.0.0.0` 时视为「团队模式」，需要 TLS 或显式 allow_plaintext
    bind: str = DEFAULT_BIND
    #: **生产存储**：PostgreSQL 连接串（`postgresql://user:pass@host:5432/db`）
    pg_dsn: str = ""
    #: **本地/测试**：SQLite 文件路径（`storage: sqlite` 时才有）
    db_path: Path | None = None
    #: 服务注册接口的共享密钥；`auto` 在启动时展开成随机值，空串表示不校验
    register_secret: str = ""
    #: 是否开放平台账号自助注册
    allow_signup: bool = True
    #: 管理员用户名白名单：这些账号注册/登录时自动获得 admin 角色
    admin_users: list[str] = field(default_factory=list)
    #: 全局 ERP 并发上限（实测 4~8 饱和，默认 6）
    global_concurrency: int = 6
    tls_cert: str = ""
    tls_key: str = ""
    #: `auto`（启动时自签）/ `off` / `manual`（给了具体证书路径）
    tls_mode: str = "off"
    #: Host 白名单（防 DNS rebinding）；空列表 + `auto` 时由 bind 派生
    allow_hosts: list[str] = field(default_factory=list)
    #: 对外访问地址（反向代理 / docker 场景），参与白名单与证书 SAN
    public_host: str = ""
    #: 是否信任 `X-Forwarded-For`（放在反向代理后面时才打开）
    trust_proxy: bool = False
    #: 显式允许「非回环 + 明文 HTTP」（TLS 由上游代理终止时）
    allow_plaintext: bool = False
    #: 启动期自动配置产生的说明（自动签发的证书、生成的密钥等），给日志用
    notes: list[str] = field(default_factory=list)

    # ---------- 角色 ----------
    def is_admin_username(self, username: str) -> bool:
        name = (username or "").strip().lower()
        return bool(name) and name in {item.lower() for item in self.admin_users}

    # ---------- 存储 ----------
    @property
    def store_backend(self) -> str:
        return "sqlite" if self.db_path is not None else "postgresql"

    @property
    def data_dir(self) -> Path:
        """运行时文件目录（`proxy.secret`、证书、注册密钥、cookie 缓存等）。"""
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

    # ---------- 自动派生 ----------
    def derive_allow_hosts(self) -> list[str]:
        """`allow_hosts: auto` 时由 bind / public_host 推导，不需要手填。"""
        if self.allow_hosts:
            return self.allow_hosts
        from ...core.provision import derive_allow_hosts

        return derive_allow_hosts(self.bind, self.public_host)

    def load_register_secret(self) -> str:
        """`register_secret: auto`：从数据目录读取，没有就生成并落盘。"""
        if self.register_secret != AUTO:
            return self.register_secret
        path = self.data_dir / SECRET_NAME
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    self.register_secret = value
                    return value
            value = secrets.token_urlsafe(24)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value + "\n", encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:  # pragma: no cover - Windows
                pass
            self.register_secret = value
            self.notes.append(f"已生成注册共享密钥：{path}")
            return value
        except OSError as exc:  # pragma: no cover - 权限问题
            self.notes.append(f"生成注册密钥失败（{path}）：{exc}")
            self.register_secret = ""
            return ""

    def ensure_tls(self) -> None:
        """`tls: auto`：非回环监听时自动签发自签证书；回环监听不需要 TLS。"""
        if self.tls_mode != AUTO:
            return
        if self.is_loopback:
            self.tls_mode = "off"
            return
        from .tls import TlsUnavailable, ensure_self_signed

        cert_dir = self.data_dir / TLS_DIR
        try:
            crt, key = ensure_self_signed(cert_dir, self.derive_allow_hosts() + [self.host])
        except TlsUnavailable as exc:
            self.notes.append(str(exc))
            return  # tls_mode 保持 auto：validate() 会据此给出可操作的报错
        except OSError as exc:  # pragma: no cover - 权限问题
            self.notes.append(f"自签证书失败（{cert_dir}）：{exc}")
            return
        self.tls_cert, self.tls_key = str(crt), str(key)
        self.tls_mode = "manual"
        self.notes.append(f"已自动签发自签证书：{crt}")

    def resolve(self) -> "ServerConfig":
        """启动前把 `auto` 项一次性展开（幂等）。"""
        self.allow_hosts = self.derive_allow_hosts()
        self.ensure_tls()
        self.load_register_secret()
        return self

    # ---------- 校验 ----------
    def validate(self) -> list[str]:
        problems: list[str] = []

        if not self.is_loopback:
            if not self.tls_enabled and not self.allow_plaintext:
                if self.tls_mode == AUTO:
                    problems.append(
                        f"bind {self.bind} 不是回环地址：`tls: auto` 没能自动签发证书"
                        f"（{'；'.join(self.notes) or '缺少 cryptography 或数据目录不可写'}）。"
                        "补上依赖后重启，或用 --tls-cert/--tls-key 指定现成证书；"
                        "若 TLS 已由 nginx 等反向代理终止，请加 --allow-plaintext"
                    )
                else:
                    problems.append(
                        f"bind {self.bind} 不是回环地址：团队模式必须启用 TLS。"
                        "config.yaml 里把 `tls` 写成 `auto` 让服务首次启动时自签证书，"
                        "或用 --tls-cert/--tls-key 指定现成证书；"
                        "若 TLS 已由 nginx 等反向代理终止，请加 --allow-plaintext"
                    )
            if not self.allow_hosts:
                problems.append(
                    f"bind {self.bind} 不是回环地址：需要 Host 白名单（防 DNS rebinding）。"
                    "config.yaml 里写 `allow_hosts: auto` 即可自动派生，"
                    "或用 --allow-host erp-tools.lan:8765 显式给出"
                )

        if bool(self.tls_cert) != bool(self.tls_key):
            problems.append("--tls-cert 与 --tls-key 必须成对出现")

        if self.global_concurrency < 1:
            problems.append("--global-concurrency 必须 >= 1")

        if not self.pg_dsn and self.db_path is None:
            problems.append(
                "没有存储：先运行 `erp init --mode mcp` 生成 config.yaml"
                "（storage: sqlite 适合单机试跑，postgres 适合生产），"
                "或临时加 --db <sqlite 文件> / --pg-dsn <连接串>"
            )
        elif self.pg_dsn and not self.pg_dsn.lower().startswith(
            ("postgresql://", "postgres://")
        ):
            problems.append("server.pg_dsn 必须是 postgresql:// 或 postgres:// 开头的连接串")

        for item in self.allow_hosts:
            if "/" in item or " " in item:
                problems.append(f"--allow-host 取值非法：{item!r}（示例：erp-tools.lan:8765）")
        return problems

    # ---------- 构造 ----------
    @classmethod
    def from_settings(cls, data: dict | None = None) -> "ServerConfig":
        """从 config.yaml 内容构造（读 `server` 段；缺项取安全默认值）。"""
        block: dict = {}
        if isinstance(data, dict) and isinstance(data.get("server"), dict):
            block = data["server"]

        bind = _text(block.get("bind")) or DEFAULT_BIND
        storage = _text(block.get("storage")).lower()
        pg_dsn = _text(block.get("pg_dsn"))
        if not storage:
            storage = "postgres" if pg_dsn else "sqlite"

        db_path: Path | None = None
        if storage != "postgres":
            candidate = Path(_text(block.get("sqlite_path")) or "auth.db").expanduser()
            if not candidate.is_absolute():
                candidate = app_data_dir() / candidate
            db_path = candidate

        tls_cert, tls_key = _text(block.get("tls_cert")), _text(block.get("tls_key"))
        tls_raw = _text(block.get("tls")).lower()
        if tls_cert or tls_key:
            tls_mode = "manual"
        elif tls_raw in ("off", "false", "no", "0", "none"):
            tls_mode = "off"
        else:  # `auto` 或未填：非回环时首次启动自签
            tls_mode = AUTO

        hosts_raw = block.get("allow_hosts")
        auto_hosts = not isinstance(hosts_raw, list)
        allow_hosts = [] if auto_hosts and _text(hosts_raw).lower() in ("", AUTO) \
            else _split_hosts(hosts_raw)

        register_raw = block.get("register_secret")
        register_secret = AUTO if register_raw is None else _text(register_raw)

        return cls(
            bind=bind,
            pg_dsn=pg_dsn,
            db_path=db_path,
            register_secret=register_secret or AUTO,
            allow_signup=_as_bool(block.get("allow_signup", True)),
            admin_users=[name.lower() for name in _split_hosts(block.get("admin_users"))],
            global_concurrency=max(1, int(block.get("concurrency") or 6)),
            tls_cert=tls_cert,
            tls_key=tls_key,
            tls_mode=tls_mode,
            allow_hosts=allow_hosts,
            public_host=_text(block.get("public_host")),
            trust_proxy=_as_bool(block.get("trust_proxy", False)),
            allow_plaintext=_as_bool(block.get("allow_plaintext", False)),
        )

    @classmethod
    def from_config_file(cls, path: str | Path | None = None) -> "ServerConfig":
        """定位并读取 config.yaml 的 `server` 段。"""
        from ...core.paths import find_config_file
        from ...core.settings import read_yaml

        found = find_config_file(path)
        data = read_yaml(found) if found is not None else {}
        return cls.from_settings(data)

    @classmethod
    def from_args(cls, args) -> "ServerConfig":
        """config.yaml 打底，命令行参数逐项覆盖。"""
        cfg = cls.from_config_file(getattr(args, "config", None))
        if getattr(args, "bind", None):
            cfg.bind = str(args.bind).strip()
        if getattr(args, "pg_dsn", None):
            cfg.pg_dsn = str(args.pg_dsn).strip()
            cfg.db_path = None  # 显式给 PG 时以 PG 为准
        if getattr(args, "db", None):
            cfg.db_path = Path(args.db).expanduser()
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
            cfg.tls_mode = "manual"
        if getattr(args, "tls_key", None):
            cfg.tls_key = str(args.tls_key).strip()
            cfg.tls_mode = "manual"
        if getattr(args, "tls", None) and str(args.tls).strip().lower() == "off":
            cfg.tls_mode = "off"
            cfg.tls_cert = cfg.tls_key = ""
        if getattr(args, "allow_host", None):
            cfg.allow_hosts = [str(h).strip() for h in args.allow_host if str(h).strip()]
        if getattr(args, "public_host", None):
            cfg.public_host = str(args.public_host).strip()
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


__all__ = ["AUTO", "DEFAULT_BIND", "SECRET_NAME", "ServerConfig", "TLS_DIR"]
