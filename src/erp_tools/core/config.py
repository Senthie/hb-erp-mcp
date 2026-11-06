"""集中配置：环境变量 > .env 文件 > 默认值。

设计要点：
  * 主机/端口/QXID **不再写死在代码里**（历史教训：两个脚本分别硬编码了内网与外网 IP）；
  * 验证码端口默认由 ERP 端口推导（8889 → 8887），可用 ERP_CAPTCHA_HOST 覆盖；
  * QXID 允许留空，运行期由 auth.discover_qxid_map() 从菜单探测。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .paths import resolve_cookie_dir, find_env_file
from .settings import load_config_file

DEFAULT_HOST = "http://192.168.1.199:8889"
DEFAULT_CAPTCHA_PORT = "8887"

_TRUE = {"1", "true", "yes", "on"}

#: 记录「由我们加载的配置文件」写进 os.environ 的值，
#: 只有当 os.environ 里仍是这个值（没被用户改过）时，才允许下次覆盖。
_LOADED_VALUES: dict[str, str] = {}


def _is_ours(key: str, current: str | None) -> bool:
    """该环境变量是否仍是我们之前从文件里写入的值。"""
    return key in _LOADED_VALUES and _LOADED_VALUES[key] == current


def _apply_env(values: dict[str, str]) -> None:
    """写入环境变量，但不覆盖用户真实设置的值。"""
    for key, value in values.items():
        current = os.environ.get(key)
        if current is None or _is_ours(key, current):
            os.environ[key] = value
            _LOADED_VALUES[key] = value


def load_env_file(path: str | Path = ".env") -> None:
    """把 .env 里的键值写进 os.environ（不覆盖用户真实环境变量）。"""
    p = Path(path)
    if not p.is_file():
        return
    parsed: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    _apply_env(parsed)


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name)
    if not raw:
        return default
    return raw.lower() in _TRUE


@dataclass(frozen=True)
class ErpConfig:
    host: str = DEFAULT_HOST
    captcha_host: str = ""
    username: str = ""
    password: str = ""
    cookie_dir: Path = field(default_factory=lambda: Path("./cookies"))

    timeout_connect: float = 10.0
    timeout_read: float = 60.0

    #: 整个 ERP 实例的全局并发上限（不是每工具各自一份）
    max_concurrency: int = 8
    #: 全局最小请求间隔（秒）
    request_delay: float = 0.0

    #: 真实业务请求前是否必须先登录（报价页目前免认证，但按需求强制登录）
    require_auth: bool = True

    #: 各页面的 QXID 覆盖值；空则自动探测
    qxid_quote: str = ""
    qxid_quote_index: str = ""
    qxid_attendance: str = ""

    #: 单页返回的最大记录数（考勤翻页主策略：调大页大小，一次拿全）
    page_size_max: int = 5000

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", (self.host or DEFAULT_HOST).strip().rstrip("/"))
        if not self.captcha_host:
            parts = urlsplit(self.host)
            hostname = parts.hostname or "127.0.0.1"
            object.__setattr__(
                self, "captcha_host", f"{parts.scheme or 'http'}://{hostname}:{DEFAULT_CAPTCHA_PORT}"
            )
        object.__setattr__(self, "cookie_dir", Path(self.cookie_dir))

    def validate(self) -> list[str]:
        """返回配置疑点（不报错，只提示）——最常见的就是端口填反。"""
        problems: list[str] = []
        host_port = urlsplit(self.host).port
        captcha_port = urlsplit(self.captcha_host).port

        if host_port is not None and str(host_port) == DEFAULT_CAPTCHA_PORT:
            problems.append(
                f"host 指向了 {DEFAULT_CAPTCHA_PORT} 端口，这是**验证码服务**的端口；"
                f"ERP 主站通常是 8889（如 http://192.168.1.199:8889）"
            )
        if self.host.rstrip("/") == self.captcha_host.rstrip("/"):
            problems.append(
                "host 与 captcha_host 完全相同；正常情况下它们是同一台机器的不同端口"
                f"（主站 8889 / 验证码 {DEFAULT_CAPTCHA_PORT}）"
            )
        if captcha_port is not None and host_port is not None and captcha_port == host_port:
            problems.append("host 与 captcha_host 端口相同，登录回发会打到验证码服务上")
        if not self.username or not self.password:
            problems.append("账号或密码未配置（登录时会提示输入验证码后仍会失败）")
        return problems

    # ---------- 派生属性 ----------
    @property
    def netloc(self) -> str:
        parts = urlsplit(self.host)
        return parts.netloc

    @property
    def cookie_domain(self) -> str:
        return urlsplit(self.host).hostname or "127.0.0.1"

    # ---------- 构造 ----------
    @classmethod
    def from_env(cls, env_file: str | Path | None = None,
                 config_file: str | Path | None = None) -> "ErpConfig":
        """读取配置。

        优先级（高 → 低）：**真实环境变量 > config.yaml > .env > 内置默认值**。
        `config.yaml` 自动查找顺序：用户数据目录 → 程序目录 → 当前目录 → 内置模板。
        """
        # 先把「调用方真实设置的环境变量」记下来，它们不会被文件覆盖
        real_env = {
            key: value
            for key, value in os.environ.items()
            if (key.startswith("ERP_") or key == "COOKIE_DIR") and not _is_ours(key, value)
        }

        found_env = find_env_file(env_file)
        if found_env is not None:
            load_env_file(found_env)

        yaml_env, _yaml_path = load_config_file(config_file)
        for key, value in yaml_env.items():
            if key not in real_env:
                os.environ[key] = value
                _LOADED_VALUES[key] = value

        return cls(
            host=_env_str("ERP_HOST", DEFAULT_HOST) or DEFAULT_HOST,
            captcha_host=_env_str("ERP_CAPTCHA_HOST"),
            username=_env_str("ERP_USERNAME"),
            password=_env_str("ERP_PASSWORD"),
            cookie_dir=resolve_cookie_dir(os.environ.get("COOKIE_DIR")),
            timeout_connect=_env_float("ERP_TIMEOUT_CONNECT", 10.0),
            timeout_read=_env_float("ERP_TIMEOUT_READ", 60.0),
            max_concurrency=max(1, _env_int("ERP_MAX_CONCURRENCY", 8)),
            request_delay=max(0.0, _env_float("ERP_REQUEST_DELAY", 0.0)),
            require_auth=_env_bool("ERP_REQUIRE_AUTH", True),
            qxid_quote=_env_str("ERP_QXID_QUOTE"),
            qxid_quote_index=_env_str("ERP_QXID_QUOTE_INDEX"),
            qxid_attendance=_env_str("ERP_QXID_ATTENDANCE"),
            page_size_max=max(50, _env_int("ERP_PAGE_SIZE_MAX", 5000)),
        )

    def replace(self, **changes) -> "ErpConfig":
        from dataclasses import replace

        return replace(self, **changes)

    def describe(self) -> str:
        pwd = "***" if self.password else "(未设置)"
        return (
            f"host={self.host} captcha={self.captcha_host} user={self.username or '(未设置)'} "
            f"pwd={pwd} cookie_dir={self.cookie_dir} concurrency={self.max_concurrency}"
        )
