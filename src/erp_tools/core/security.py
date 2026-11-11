"""API Key 的生成/校验，以及日志脱敏。

这里要区分两种完全不同的「安全」需求：

* **API Key**：服务端只需要证明「请求方持有这把钥匙」，不需要还原它。
  → 用 SHA-256 单向哈希存储，比对时用恒定耗时比较。库里泄露也拿不到可用的 key。
* **ERP 密码**：本项目**根本不落盘**（登录一次换一把 API Key，密码只活在
  `POST /v1/auth/login` 的调用栈里）。所以不存在「可逆加密」的需求。

因此本模块只用 SHA-256，**不要**把它当成加密算法用。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets

#: key 前缀，便于在日志/配置里识别，也方便 secret 扫描器命中
API_KEY_PREFIX = "erp_"
#: 32 字节随机 → token_urlsafe 后 43 字符，加前缀共 47 字符
API_KEY_RANDOM_BYTES = 32


def generate_api_key() -> str:
    """生成一把新的 API Key（明文只返回这一次，库里只存哈希）。"""
    return API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_RANDOM_BYTES)


def hash_api_key(raw: str) -> str:
    """计算 API Key 的存储形式（sha256 十六进制）。"""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def verify_api_key(raw: str | None, stored_hash: str | None) -> bool:
    """恒定耗时比对：避免通过响应时间试探 key。"""
    return hmac.compare_digest(hash_api_key(raw or ""), stored_hash or "")


def is_well_formed_api_key(raw: str | None) -> bool:
    """粗筛格式，省掉一次 sha256 + 数据库查询。"""
    text = (raw or "").strip()
    return text.startswith(API_KEY_PREFIX) and len(text) >= len(API_KEY_PREFIX) + 20


def mask(value: str | None, *, keep: int = 4) -> str:
    """把敏感值压成 `abcd…wxyz`；太短则整体打码。"""
    text = value if value is not None else ""
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}…{text[-keep:]}"


# ══════════════════ 日志脱敏 ══════════════════
#: 形如 erp_xxxxxxxx 的 key，出现在任意文本里都要打码
_API_KEY_INLINE = re.compile(re.escape(API_KEY_PREFIX) + r"[A-Za-z0-9_\-]{20,}")
#: password=xxx / "api_key": "xxx" / token: xxx
_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|api[_-]?key|token|secret|authorization|set-cookie|cookie)"
    r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;&}\]\"']+)"
)


def scrub(text: str | None) -> str:
    """抹掉文本里的 key / 密码 / cookie 值，用于日志与错误信息。"""
    if text is None:
        return ""
    out = _API_KEY_INLINE.sub(lambda m: mask(m.group(0)), str(text))
    out = _KEY_VALUE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{mask(m.group(3))}", out
    )
    return out


class SensitiveFilter(logging.Filter):
    """挂到 root logger 上，防止密码/key 意外进日志文件。

    **必须先合并 args 再脱敏**：直接清洗 msg 模板会把 `password=%s`
    打码成 `password=**`，导致 `record.getMessage()` 里的 `%` 格式化崩掉。

    用法::

        logging.getLogger().addFilter(SensitiveFilter())
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = scrub(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001 - 脱敏绝不能反过来把日志搞崩
            pass
        return True


__all__ = [
    "API_KEY_PREFIX",
    "API_KEY_RANDOM_BYTES",
    "SensitiveFilter",
    "generate_api_key",
    "hash_api_key",
    "is_well_formed_api_key",
    "mask",
    "scrub",
    "verify_api_key",
]
