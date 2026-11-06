"""路径解析测试（源码运行 / 打包运行两种模式）。"""

import sys
from pathlib import Path

import pytest

from erp_tools.core import paths


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """模拟 PyInstaller 打包运行，并把数据目录指向临时目录。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def test_source_mode_uses_cwd(monkeypatch):
    monkeypatch.delenv("ERP_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert paths.app_data_dir() == Path.cwd()
    assert paths.default_cookie_dir() == Path.cwd() / "cookies"


def test_frozen_mode_uses_appdata(frozen):
    assert paths.is_frozen()
    assert paths.app_data_dir() == frozen
    assert paths.default_cookie_dir() == frozen / "cookies"


def test_frozen_env_file_prefers_data_dir(frozen, monkeypatch):
    monkeypatch.chdir(frozen.parent)
    candidates = paths.candidate_env_files()
    assert candidates[0] == frozen / ".env"
    assert candidates[1] == Path.cwd() / ".env"


def test_relative_cookie_dir_anchored_to_data_dir(frozen):
    # 打包后即使 .env 里写的是相对路径，也要落到可写的数据目录
    assert paths.resolve_cookie_dir("./cookies") == frozen / "cookies"
    absolute = frozen / "elsewhere"
    assert paths.resolve_cookie_dir(str(absolute)) == absolute
    assert paths.resolve_cookie_dir("") == frozen / "cookies"


def test_source_mode_keeps_relative_cookie_dir(monkeypatch):
    monkeypatch.delenv("ERP_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert str(paths.resolve_cookie_dir("./cookies")) == "cookies"
