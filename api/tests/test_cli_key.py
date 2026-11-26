"""`erp key`：向网关换取 api-key（密码只在这次请求里用一次）。

用假的 requests.post 覆盖成功 / 失败路径，不发起真实网络请求。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from erp_tools import cli


class _Resp:
    def __init__(self, status: int, payload) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else str(payload)

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")


def _args(**kw):
    base = dict(server="https://erp-tools.lan:8443", user="21146",
                password="secret", cacert=None, insecure=False, timeout=30.0)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture()
def fake_post(monkeypatch):
    calls: list[dict] = []

    def _post(url, json=None, verify=True, timeout=30.0):  # noqa: A002
        calls.append({"url": url, "json": json, "verify": verify, "timeout": timeout})
        return _Resp(200, {"api_key": "erp_abc", "username": json["username"],
                           "user_id": "2474",
                           "erp_pages": {"quote_index": "876", "quote": "873"}})

    monkeypatch.setattr("requests.post", _post)
    return calls


def test_key_prints_api_key_only_on_stdout(fake_post, capsys) -> None:
    assert cli.cmd_key(_args()) == 0

    out = capsys.readouterr()
    assert out.out.strip() == "erp_abc", "stdout 只放 key，方便管道/复制"
    assert "已换到 api-key" in out.err
    assert fake_post[0]["json"] == {"username": "21146", "password": "secret"}
    assert fake_post[0]["url"] == "https://erp-tools.lan:8443/v1/auth/login"


def test_key_trims_trailing_slash(fake_post) -> None:
    cli.cmd_key(_args(server="https://erp-tools.lan:8443/"))
    assert fake_post[0]["url"] == "https://erp-tools.lan:8443/v1/auth/login"


def test_key_passes_cacert_and_insecure(monkeypatch) -> None:
    seen = {}

    def _post(url, json=None, verify=True, timeout=30.0):  # noqa: A002
        seen["verify"] = verify
        return _Resp(200, {"api_key": "erp_x", "username": "u"})

    monkeypatch.setattr("requests.post", _post)

    cli.cmd_key(_args(cacert="certs/erp-tools.crt"))
    assert seen["verify"] == "certs/erp-tools.crt"

    cli.cmd_key(_args(cacert="certs/erp-tools.crt", insecure=True))
    assert seen["verify"] is False, "--insecure 必须压过 --cacert"


def test_key_reports_http_error(monkeypatch) -> None:
    def _post(url, json=None, verify=True, timeout=30.0):  # noqa: A002
        return _Resp(401, {"error": "invalid_credentials", "message": "账号或密码错误"})

    monkeypatch.setattr("requests.post", _post)

    assert cli.cmd_key(_args()) == 1


def test_key_requires_server() -> None:
    assert cli.cmd_key(_args(server="  ")) == 2


def test_key_refuses_non_interactive_without_password(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    assert cli.cmd_key(_args(password="")) == 2


def test_key_never_writes_password_anywhere(fake_post, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    cli.cmd_key(_args())
    capsys.readouterr()

    leftovers = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == [], f"不应落任何文件：{leftovers}"


def test_key_is_registered_in_parser() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["key", "--server", "http://127.0.0.1:8765"])
    assert args.func is cli.cmd_key
    assert args.server == "http://127.0.0.1:8765"
    assert args.password is None
