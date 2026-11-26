"""配置向导写到哪里（源码运行 vs 打包运行）。"""

import sys

from erp_tools.core import paths


def test_setup_target_source_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("ERP_DATA_DIR", raising=False)
    assert paths.preferred_config_path() == tmp_path / "config.yaml"


def test_setup_target_frozen_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path / "data"))
    assert paths.preferred_config_path() == tmp_path / "data" / "config.yaml"


def test_program_dir_source_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert paths.program_dir() == tmp_path
