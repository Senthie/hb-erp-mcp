"""core.security：api-key 生成/校验 + 日志脱敏。"""

from __future__ import annotations

import logging

from erp_tools.core.security import (
    API_KEY_PREFIX,
    SensitiveFilter,
    generate_api_key,
    hash_api_key,
    is_well_formed_api_key,
    mask,
    scrub,
    verify_api_key,
)


def test_generate_api_key_shape_and_uniqueness() -> None:
    keys = {generate_api_key() for _ in range(200)}
    assert len(keys) == 200, "api-key 必须唯一"
    for key in keys:
        assert key.startswith(API_KEY_PREFIX)
        assert len(key) >= len(API_KEY_PREFIX) + 20
        assert is_well_formed_api_key(key)


def test_hash_is_deterministic_and_irreversible() -> None:
    raw = generate_api_key()
    digest = hash_api_key(raw)
    assert digest == hash_api_key(raw)
    assert len(digest) == 64
    assert raw not in digest
    assert verify_api_key(raw, digest)
    assert not verify_api_key(raw + "x", digest)
    assert not verify_api_key(None, digest)
    assert not verify_api_key(raw, None)


def test_well_formed_rejects_garbage() -> None:
    assert not is_well_formed_api_key("")
    assert not is_well_formed_api_key(None)
    assert not is_well_formed_api_key("bearer-token-without-prefix")
    assert not is_well_formed_api_key("erp_short")


def test_mask_keeps_edges_only() -> None:
    assert mask("erp_abcdefghijklmnop") == "erp_…mnop"
    assert mask("abc") == "***"
    assert mask("") == ""
    assert mask(None) == ""


def test_scrub_masks_api_key_and_password() -> None:
    raw = generate_api_key()
    assert raw not in scrub(f"Authorization: Bearer {raw}")

    text = scrub("登录失败 password=hunter2 api_key=erp_abcdefghijklmnop token: abcdef123456")
    assert "hunter2" not in text
    assert "erp_abcdefghijklmnop" not in text
    assert "abcdef123456" not in text


def test_sensitive_filter_sanitizes_record() -> None:
    filt = SensitiveFilter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="user=%s password=%s", args=("21146", "hunter2"), exc_info=None,
    )
    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert "hunter2" not in rendered
    assert "21146" in rendered


def test_scrub_never_raises_on_weird_input() -> None:
    assert scrub(None) == ""
    assert scrub("") == ""
