"""附件下载代理：把内网静态直链换成**网关可访问的签名链接**。

ERP 的附件直链形如 `http://192.168.1.199:8887/Admin/FuJian/<模块>/Picture/<guid>.zip`。
它在 ERP 网段内可直接 GET，换个网段就不通了。做法是让网关自己转发：

```
GET /v1/files?path=/Admin/FuJian/...&t=<exp>.<hmac>
```

签名把 **路径 + 过期时间** 绑在一起：

- 链接里**不含 api-key**，可以放心贴进浏览器/IM/日志
- 有效期默认 30 分钟（`ERP_FILE_PROXY_TTL` 可调），过期后重新调工具拿新链接
- 只允许 `/Admin/FuJian/` 前缀，避免被当成任意 URL 的 SSRF 跳板
- 签名密钥落在 `<auth.db 所在目录>/proxy.secret`（首次启动自动生成），
  所以网关与 CLI 共享同一份密钥；网关重启不会让已发出的链接立刻失效

网关基地址（`scheme://host:port`）的取值顺序：
显式 `base` → 环境变量 `ERP_PUBLIC_BASE` → 当前请求的 Host（contextvar）→ `http://127.0.0.1:8765`
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

#: 只代理 ERP 附件静态目录，别的一律拒绝
ALLOWED_PREFIX = "/admin/fujian/"

#: 默认有效期（秒）
DEFAULT_TTL = 1800

_FALLBACK_BASE = "http://127.0.0.1:8765"
_secret_cache: str = ""


# ══════════════════ 密钥 ══════════════════
def secret_file(db_path: str | Path) -> Path:
    """签名密钥文件位置（与 api-key 库放同一目录）。"""
    return Path(db_path).expanduser().resolve().parent / "proxy.secret"


def load_secret(db_path: str | Path) -> str:
    """读取（必要时生成）签名密钥，并缓存到进程内。"""
    global _secret_cache
    path = secret_file(db_path)
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            _secret_cache = value
            return value
    value = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    try:                                    # 尽量收紧权限（Windows 上失败可忽略）
        os.chmod(path, 0o600)
    except OSError:
        pass
    _secret_cache = value
    return value


def configure(secret: str) -> None:
    """显式注入密钥（测试 / 由服务端启动时调用）。"""
    global _secret_cache
    _secret_cache = (secret or "").strip()


def secret() -> str:
    """当前生效的签名密钥；未显式初始化时从默认位置惰性加载（CLI 场景）。"""
    if not _secret_cache:
        from ..core.paths import app_data_dir

        return load_secret(app_data_dir() / "auth.db")
    return _secret_cache


def reset() -> None:
    """清空缓存（测试用）。"""
    global _secret_cache
    _secret_cache = ""


# ══════════════════ 路径 ══════════════════
def path_of(url: str) -> str:
    """从完整直链里取出路径部分（`/Admin/FuJian/...`）；不是 URL 就原样当归一化路径。"""
    text = (url or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    path = parts.path if parts.scheme or parts.netloc else text.split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path.lstrip("./")
    return unquote(path)


def is_proxyable(path_or_url: str) -> bool:
    """是不是可以代理的 ERP 附件路径。"""
    return path_of(path_or_url).lower().startswith(ALLOWED_PREFIX)


def _normalize_for_sign(path: str) -> str:
    """签名前归一化：去 query、统一小写前缀、保留原始大小写文件名。"""
    raw = path_of(path)
    return raw.split("?", 1)[0]


# ══════════════════ 签名 ══════════════════
def _digest(path: str, exp: int, key: str) -> str:
    msg = f"{path}\n{exp}".encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def ttl_default() -> int:
    try:
        return max(60, int(os.getenv("ERP_FILE_PROXY_TTL", "") or DEFAULT_TTL))
    except ValueError:
        return DEFAULT_TTL


def sign(path: str, *, ttl: int | None = None, key: str | None = None) -> str:
    """给路径签一个 `"<exp>.<hmac>"` 令牌。"""
    target = _normalize_for_sign(path)
    exp = int(time.time()) + (ttl_default() if ttl is None else int(ttl))
    return f"{exp}.{_digest(target, exp, key or secret())}"


def verify(path: str, token: str, *, key: str | None = None) -> bool:
    """校验令牌（路径必须与签名时一致、未过期）。"""
    raw = (token or "").strip()
    exp_text, _, sign_part = raw.partition(".")
    if not exp_text.isdigit() or not sign_part:
        return False
    exp = int(exp_text)
    if exp < int(time.time()):
        return False
    try:
        expected = _digest(_normalize_for_sign(path), exp, key or secret())
    except (RuntimeError, OSError):
        return False
    return hmac.compare_digest(expected, sign_part)


# ══════════════════ 生成链接 ══════════════════
def public_base(explicit: str = "") -> str:
    """网关对外基地址：显式 > `ERP_PUBLIC_BASE` > 当前请求 Host > 回环默认值。"""
    for candidate in (explicit, os.getenv("ERP_PUBLIC_BASE", "")):
        text = (candidate or "").strip().rstrip("/")
        if text:
            return text
    try:
        from .context import base_url

        current = base_url()
    except Exception:                      # noqa: BLE001 - 不在请求上下文里
        current = ""
    return (current or os.getenv("ERP_GATEWAY_BASE", "").strip().rstrip("/")
            or _FALLBACK_BASE)


def build_url(url: str, *, base: str = "", ttl: int | None = None,
              name: str = "") -> str:
    """把内网直链换成网关代理链接；不可代理时返回空串。

    `name` 是 ERP 里的「附件名称」，只用于下载时的文件名（`&n=`），不参与签名。
    """
    path = path_of(url)
    if not is_proxyable(path):
        return ""
    token = sign(path, ttl=ttl)
    extra = f"&n={quote(name, safe='')}" if (name or "").strip() else ""
    return (f"{public_base(base)}/v1/files"
            f"?path={quote(path, safe='/')}&t={quote(token, safe='.')}{extra}")


def download_name(path_or_url: str, requested: str = "") -> str:
    """下载时对外显示的文件名：优先用调用方给的附件名称，并做安全清洗。"""
    want = (requested or "").strip()
    if want:
        want = want.replace("\\", "/").rsplit("/", 1)[-1]
        want = re.sub(r"[\r\n\x00-\x1f\x7f\"]+", "", want).strip()
        if want and len(want) <= 120:
            return want
    return filename_of(path_or_url)


def filename_of(path_or_url: str) -> str:
    path = path_of(path_or_url)
    return unquote(path.rsplit("/", 1)[-1]) or "attachment.bin"


def describe() -> dict[str, str]:
    """给 `/v1/me`、文档用的自述信息。"""
    return {
        "endpoint": "/v1/files?path=<附件路径>&t=<签名>",
        "allowed_prefix": ALLOWED_PREFIX.rstrip("/"),
        "ttl_seconds": str(ttl_default()),
        "public_base": public_base(),
    }


__all__ = [
    "ALLOWED_PREFIX",
    "DEFAULT_TTL",
    "build_url",
    "configure",
    "describe",
    "download_name",
    "filename_of",
    "is_proxyable",
    "load_secret",
    "path_of",
    "public_base",
    "reset",
    "secret",
    "secret_file",
    "sign",
    "ttl_default",
    "verify",
]
