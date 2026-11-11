"""runtime.sessions：会话池、存活探测、全局并发闸门。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from erp_tools.core.config import ErpConfig
from erp_tools.runtime.sessions import SessionExpired, SessionProvider, UserSession


def make_cfg(tmp_path: Path) -> ErpConfig:
    return ErpConfig(
        host="http://127.0.0.1:8889",
        username="21146",
        password="",
        cookie_dir=tmp_path / "cookies",
    )


def fake_session(username: str = "21146") -> UserSession:
    return UserSession(username=username, auth=object(), user_id="1234")


def test_login_registers_session_and_reuses_it(tmp_path: Path) -> None:
    provider = SessionProvider(make_cfg(tmp_path), login_impl=lambda u, p: fake_session(u))

    session = provider.login("21146", "secret")
    assert session.username == "21146"
    assert provider.get("21146") is session
    assert provider.require("21146") is session


def test_login_does_not_keep_password(tmp_path: Path) -> None:
    """登录实现只能拿到密码；返回的会话对象里不应出现明文。"""
    captured: list[str] = []

    def impl(username: str, password: str) -> UserSession:
        captured.append(password)
        return fake_session(username)

    provider = SessionProvider(make_cfg(tmp_path), login_impl=impl)
    session = provider.login("21146", "hunter2")

    assert captured == ["hunter2"]          # 只传入一次
    dumped = repr(session.__dict__)
    assert "hunter2" not in dumped


def test_login_requires_credentials(tmp_path: Path) -> None:
    provider = SessionProvider(make_cfg(tmp_path), login_impl=lambda u, p: fake_session(u))
    with pytest.raises(ValueError):
        provider.login("", "x")
    with pytest.raises(ValueError):
        provider.login("21146", "")


def test_require_without_session_raises(tmp_path: Path) -> None:
    provider = SessionProvider(make_cfg(tmp_path), login_impl=lambda u, p: fake_session(u))
    with pytest.raises(SessionExpired):
        provider.require("21146")


def test_is_alive_is_cached_within_ttl(tmp_path: Path) -> None:
    calls = {"n": 0}

    def alive_impl(_auth) -> bool:
        calls["n"] += 1
        return True

    provider = SessionProvider(
        make_cfg(tmp_path), login_impl=lambda u, p: fake_session(u),
        alive_impl=alive_impl, alive_ttl=60.0,
    )
    provider.login("21146", "secret")

    assert provider.is_alive("21146") is True
    assert provider.is_alive("21146") is True
    assert calls["n"] == 1, "TTL 内不应重复探测"

    assert provider.is_alive("21146", refresh=True) is True
    assert calls["n"] == 2


def test_network_error_does_not_invalidate_session(tmp_path: Path) -> None:
    """ERP 抖动不能导致所有 api-key 被误判失效。"""

    def boom(_auth) -> bool:
        raise RuntimeError("connection reset")

    provider = SessionProvider(
        make_cfg(tmp_path), login_impl=lambda u, p: fake_session(u), alive_impl=boom,
    )
    provider.login("21146", "secret")

    assert provider.is_alive("21146") is True
    assert provider.get("21146") is not None
    assert provider.get("21146").last_error == "connection reset"


def test_definitive_false_drops_session(tmp_path: Path) -> None:
    provider = SessionProvider(
        make_cfg(tmp_path), login_impl=lambda u, p: fake_session(u),
        alive_impl=lambda _auth: False,
    )
    provider.login("21146", "secret")

    assert provider.is_alive("21146") is False
    assert provider.get("21146") is None


def test_global_concurrency_gate(tmp_path: Path) -> None:
    provider = SessionProvider(
        make_cfg(tmp_path), global_concurrency=2,
        login_impl=lambda u, p: fake_session(u),
    )

    peak = {"value": 0}
    current = {"value": 0}
    lock = threading.Lock()

    def worker() -> None:
        with provider.slot():
            with lock:
                current["value"] += 1
                peak["value"] = max(peak["value"], current["value"])
            time.sleep(0.05)
            with lock:
                current["value"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak["value"] <= 2, f"并发峰值 {peak['value']} 超过闸门上限 2"


def test_drop_releases_session(tmp_path: Path) -> None:
    provider = SessionProvider(make_cfg(tmp_path), login_impl=lambda u, p: fake_session(u))
    provider.login("21146", "secret")
    provider.drop("21146")
    assert provider.get("21146") is None
