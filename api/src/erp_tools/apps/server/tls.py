"""自签 TLS 证书（`tls: auto` 用）。

团队模式下（监听非回环地址）以前要手工跑 `scripts/gen-cert.sh`、再把两个路径回填
到配置里。现在 `erp serve` 首次启动时直接在这里签好，落在数据目录：

    <data>/tls/erp-tools.crt
    <data>/tls/erp-tools.key

SAN 自动包含 bind 的 Host、`allow_hosts` 里的每个域名、以及本机 IPv4 与
`127.0.0.1` / `localhost`，客户端用 IP 或域名访问都能过校验。
"""

from __future__ import annotations

import datetime
import ipaddress
import socket
from pathlib import Path

CERT_NAME = "erp-tools.crt"
KEY_NAME = "erp-tools.key"
DEFAULT_DAYS = 825


class TlsUnavailable(RuntimeError):
    """缺少 cryptography，或环境不允许生成证书。"""


def _crypto():
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ImportError as exc:  # pragma: no cover - 依赖已写进 server extra
        raise TlsUnavailable(
            "自动签发自签证书需要 cryptography：请执行 `uv sync --extra server`"
        ) from exc
    return x509, hashes, serialization, rsa, ExtendedKeyUsageOID, NameOID


def local_ipv4() -> list[str]:
    """本机 IPv4（尽力而为：UDP connect 拿出口地址 + 主机名解析）。"""
    out: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            out.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in out:
                out.append(addr)
    except OSError:
        pass
    return out


def san_names(hosts: list[str]) -> list[str]:
    """把 `host:port` / 域名 / IP 统一成 SAN 条目（去端口、去通配地址）。"""
    names: list[str] = ["localhost", "127.0.0.1"]
    for raw in hosts:
        text = (raw or "").strip()
        if not text:
            continue
        if text.startswith("["):  # [::1]:8765
            text = text.partition("]")[0].lstrip("[")
        elif text.count(":") == 1:  # host:port
            text = text.partition(":")[0]
        if not text or text in {"0.0.0.0", "::"}:
            continue
        if text not in names:
            names.append(text)
    for addr in local_ipv4():
        if addr not in names:
            names.append(addr)
    return names


def ensure_self_signed(
    directory: str | Path,
    hosts: list[str],
    *,
    days: int = DEFAULT_DAYS,
    force: bool = False,
) -> tuple[Path, Path]:
    """确保目录里有可用的自签证书，返回 (crt, key)。已存在且不 force 就复用。"""
    x509, hashes, serialization, rsa, ExtendedKeyUsageOID, NameOID = _crypto()

    base = Path(directory).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    cert_path = base / CERT_NAME
    key_path = base / KEY_NAME
    if cert_path.is_file() and key_path.is_file() and not force:
        return cert_path, key_path

    names = san_names(hosts)
    primary = names[0] if names else "erp-tools"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, primary),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "erp-tools"),
    ])
    alt: list[x509.GeneralName] = []
    for name in names:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            alt.append(x509.DNSName(name))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        key_path.chmod(0o600)
    except OSError:  # pragma: no cover - Windows
        pass
    return cert_path, key_path


__all__ = [
    "CERT_NAME",
    "DEFAULT_DAYS",
    "KEY_NAME",
    "TlsUnavailable",
    "ensure_self_signed",
    "local_ipv4",
    "san_names",
]
