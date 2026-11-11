#!/usr/bin/env python
"""生成团队模式用的自签 TLS 证书（PEM 证书 + PEM 私钥）。

    .\\.venv\\Scripts\\python.exe scripts\\make-cert.py
    .\\.venv\\Scripts\\python.exe scripts\\make-cert.py --host 192.168.1.226 --host erp-tools.lan

为什么需要它：`erp serve` 绑定非回环地址时**强制要求** `--tls-cert/--tls-key`，
否则 api-key 会在内网明文传输。

证书是**自签**的，并且带 `CA:TRUE`——所以可以直接把 `erp-tools.crt` 装进各设备的
「受信任的根证书颁发机构」，客户端就不会再报 self-signed。
SAN 一定要写全：客户端用 IP 就用 IP，用域名就用域名，否则 Node 客户端会报
`ERR_TLS_CERT_ALTNAME_INVALID`。

不带 `--host` 时会自动收集本机所有 IPv4（除回环/APIPA），并补上 localhost。
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import socket
import sys
from pathlib import Path


def _default_hosts() -> list[str]:
    hosts = {"localhost", "127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr.startswith(("127.", "169.254.")):
                continue
            hosts.add(addr)
    except OSError:
        pass
    return sorted(hosts)


def _build(cn: str, hosts: list[str], days: int):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "erp-tools"),
    ])

    san: list[x509.GeneralName] = []
    for host in hosts:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            san.append(x509.DNSName(host))
    if not san:  # pragma: no cover - 由调用方保证
        raise SystemExit("至少需要一个 --host")

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        # CA:TRUE → 证书本身可以作为「受信任的根」，方便分发到别的设备
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return key_pem, cert_pem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 erp serve 用的自签 TLS 证书")
    parser.add_argument("--host", dest="hosts", action="append", default=None,
                        metavar="IP|域名", help="要写进 SAN 的主机名/IP，可重复")
    parser.add_argument("--cn", default="erp-tools", help="证书 CN，默认 erp-tools")
    parser.add_argument("--days", type=int, default=825,
                        help="有效期天数，默认 825（Apple 客户端上限）")
    parser.add_argument("--out-dir", default="certs", help="输出目录，默认 ./certs")
    args = parser.parse_args(argv)

    hosts = sorted({h.strip() for h in (args.hosts or []) if h and h.strip()})
    if not hosts:
        hosts = _default_hosts()

    key_pem, cert_pem = _build(args.cn, hosts, args.days)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    key_path = out / f"{args.cn}.key"
    cert_path = out / f"{args.cn}.crt"
    key_path.write_bytes(key_pem)
    cert_path.write_bytes(cert_pem)
    try:  # POSIX 下把私钥权限收到 600（Windows 上无意义，忽略）
        key_path.chmod(0o600)
    except OSError:  # pragma: no cover
        pass

    print(f"[OK] 证书：{cert_path.resolve()}")
    print(f"[OK] 私钥：{key_path.resolve()}")
    print(f"[OK] SAN：{', '.join(hosts)}")
    print()
    print("下一步：")
    print(f"  erp serve --bind 0.0.0.0:8765 "
          f"--tls-cert {cert_path} --tls-key {key_path} "
          + " ".join(f"--allow-host {h}:8765".replace(":8765:8765", ":8765") for h in hosts
                     if h not in ("localhost", "127.0.0.1")))
    print("  # 客户端需信任上面这个 .crt；详见 docs/mcp.md 的「5.5 让客户端信任自签证书」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
