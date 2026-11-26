"""runtime.auth_store：api-key 的签发/校验/撤销/持久化。"""

from __future__ import annotations

from pathlib import Path

import pytest

from erp_tools.core.security import hash_api_key
from erp_tools.runtime.auth_store import AuthStore


@pytest.fixture()
def store(tmp_path: Path) -> AuthStore:
    s = AuthStore(tmp_path / "auth.db").init_schema()
    yield s
    s.close()


def test_issue_and_resolve(store: AuthStore) -> None:
    raw = store.issue("21146", user_id="1234", ip="10.0.0.5")

    record = store.resolve(raw)
    assert record is not None
    assert record.username == "21146"
    assert record.user_id == "1234"
    assert record.created_ip == "10.0.0.5"
    assert record.revoked is False
    assert record.created_at

    # 库里存的是哈希，不是明文
    assert store.get_by_hash(raw) is None
    assert store.get_by_hash(hash_api_key(raw)) is not None


def test_resolve_rejects_wrong_or_empty(store: AuthStore) -> None:
    store.issue("21146")
    assert store.resolve("erp_wrongkeywrongkeywrongkey") is None
    assert store.resolve("") is None
    assert store.resolve(None) is None


def test_multiple_keys_per_user(store: AuthStore) -> None:
    first = store.issue("21146", note="laptop")
    second = store.issue("21146", note="desktop")

    assert store.resolve(first) is not None
    assert store.resolve(second) is not None
    assert len(store.list_for_user("21146")) == 2
    assert {r.note for r in store.list_for_user("21146")} == {"laptop", "desktop"}


def test_revoke_single_key_keeps_others(store: AuthStore) -> None:
    first = store.issue("21146")
    second = store.issue("21146")

    assert store.revoke(first) is True
    assert store.revoke(first) is False       # 幂等：已撤销返回 False

    assert store.resolve(first) is None
    assert store.resolve(second) is not None
    assert store.resolve_any(first) is not None
    assert store.resolve_any(first).revoked is True


def test_revoke_hash_and_revoke_user(store: AuthStore) -> None:
    a = store.issue("21146")
    b = store.issue("21146")
    c = store.issue("30001")

    assert store.revoke_hash(hash_api_key(a)) is True
    assert store.resolve(a) is None

    assert store.revoke_user("21146") == 1    # 只剩 b 是活的
    assert store.resolve(b) is None
    assert store.resolve(c) is not None
    assert store.count() == 1
    assert store.count(include_revoked=True) == 3


def test_touch_updates_last_used(store: AuthStore) -> None:
    raw = store.issue("21146")
    record = store.resolve(raw)
    assert record is not None and record.last_used is None

    store.touch(record.key_hash)
    again = store.resolve(raw)
    assert again is not None and again.last_used


def test_touch_min_interval_throttles_writes(store: AuthStore) -> None:
    raw = store.issue("21146")
    key_hash = store.resolve(raw).key_hash

    store.touch(key_hash)
    first = store.resolve(raw).last_used

    store.touch(key_hash, min_interval=3600)   # 刚写过，应被跳过
    assert store.resolve(raw).last_used == first


def test_persists_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "auth.db"
    with AuthStore(db) as s1:
        raw = s1.issue("21146", user_id="1234")

    with AuthStore(db) as s2:
        record = s2.resolve(raw)
        assert record is not None
        assert record.username == "21146"
        assert record.user_id == "1234"


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "auth.db"
    AuthStore(db).init_schema().close()
    store = AuthStore(db).init_schema()
    try:
        assert store.count(include_revoked=True) == 0
    finally:
        store.close()
