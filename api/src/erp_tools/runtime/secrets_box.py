"""ERP 密码的落库加密（AES-256-GCM）。

为什么要存密码：ERP 会话（cookie）会过期，而重新登录需要「工号 + 密码 + 验证码」。
不存密码就只能等用户手动重新绑定；存下来（加密）才能做到**用户无感**。

安全约定：

* 明文密码只在 `POST /v1/platform/erp/bind` / `POST /v1/auth/login` 的调用栈里出现，
  落库前就在这里加密成密文；任何接口都不返回、日志不记录；
* 密钥是进程外的随机 32 字节，存在数据目录（`<data>/erp_credentials.key`，
  `chmod 600`），**与数据库分开**：只拿到数据库 dump 的人无法解密；
* 密文带版本前缀 `v1:`，用 AAD 绑定用途，避免密文被挪用到别处；
* 想彻底不存：配置 `server.store_erp_password: false`（或 `--no-store-erp-password`）。
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

#: 密钥文件名（放在数据目录，与库文件分开）
KEY_NAME = "erp_credentials.key"
#: 密文版本前缀
PREFIX = "v1:"
#: 附加认证数据：把密文绑定到「ERP 密码」这一个用途
_AAD = b"erp-tools/erp-credential"
_KEY_BYTES = 32


class SecretBoxUnavailable(RuntimeError):
    """缺少 cryptography，或密钥文件不可用。"""


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - 依赖已写进 server extra
        raise SecretBoxUnavailable(
            "加密保存 ERP 密码需要 cryptography：请执行 `uv sync --extra server`"
        ) from exc
    return AESGCM


def load_or_create_key(path: str | Path) -> bytes:
    """读密钥文件；没有就生成一份（32 字节，base64 文本，权限 600）。"""
    p = Path(path).expanduser()
    try:
        if p.is_file():
            raw = p.read_text(encoding="utf-8").strip()
            key = base64.urlsafe_b64decode(raw.encode()) if raw else b""
            if len(key) == _KEY_BYTES:
                return key
        p.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(_KEY_BYTES)
        p.write_text(base64.urlsafe_b64encode(key).decode() + "\n", encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:  # pragma: no cover - Windows
            pass
        return key
    except OSError as exc:
        raise SecretBoxUnavailable(f"读写密钥文件失败（{p}）：{exc}") from exc


class SecretBox:
    """对称加密小工具：`encrypt(text) -> "v1:<b64>"` / `decrypt(token) -> text`。"""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise ValueError(f"密钥必须是 {_KEY_BYTES} 字节")
        self._aes = _crypto()(key)

    @classmethod
    def from_file(cls, path: str | Path) -> "SecretBox":
        return cls(load_or_create_key(path))

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise ValueError("不能加密空字符串")
        nonce = os.urandom(12)
        blob = self._aes.encrypt(nonce, plaintext.encode("utf-8"), _AAD)
        return PREFIX + base64.urlsafe_b64encode(nonce + blob).decode()

    def decrypt(self, token: str) -> str:
        text = (token or "").strip()
        if not text.startswith(PREFIX):
            raise ValueError("密文格式不认识（缺少版本前缀）")
        try:
            raw = base64.urlsafe_b64decode(text[len(PREFIX):].encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("密文不是合法 base64") from exc
        nonce, blob = raw[:12], raw[12:]
        return self._aes.decrypt(nonce, blob, _AAD).decode("utf-8")

    def try_decrypt(self, token: str | None) -> str | None:
        """解密失败（换了密钥/密文损坏）返回 None，让调用方走「过期需重登」。"""
        if not token:
            return None
        try:
            return self.decrypt(token)
        except Exception:  # noqa: BLE001 - 密文不可用等同于「没存密码」
            return None


__all__ = [
    "KEY_NAME",
    "PREFIX",
    "SecretBox",
    "SecretBoxUnavailable",
    "load_or_create_key",
]
