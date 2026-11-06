"""YAML 配置加载、拍平与优先级测试。"""

import os
import sys

import pytest

from erp_tools.core import paths
from erp_tools.core.config import ErpConfig
from erp_tools.core.settings import read_yaml, update_yaml, yaml_to_env

SAMPLE = """
erp:
  host: http://10.0.0.9:8889
  username: "21146"
  password: "secret"
  require_auth: false
runtime:
  max_concurrency: 3
  cookie_dir: ""
qxid:
  quote: "873"
"""


def test_yaml_to_env_flattens_and_skips_empty():
    env = yaml_to_env({
        "erp": {"host": "http://h:1", "password": "", "require_auth": False},
        "runtime": {"max_concurrency": 3},
        "qxid": {},
    })
    assert env["ERP_HOST"] == "http://h:1"
    assert env["ERP_REQUIRE_AUTH"] == "false"
    assert env["ERP_MAX_CONCURRENCY"] == "3"
    assert "ERP_PASSWORD" not in env        # 空值不覆盖
    assert "COOKIE_DIR" not in env
    assert "ERP_QXID_QUOTE" not in env


def test_update_yaml_preserves_other_sections(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")

    update_yaml(p, {"erp": {"host": "http://new:8889"}})
    data = read_yaml(p)

    assert data["erp"]["host"] == "http://new:8889"
    assert data["erp"]["username"] == "21146"        # 未涉及键保留
    assert data["runtime"]["max_concurrency"] == 3
    assert data["qxid"]["quote"] == "873"


@pytest.fixture
def frozen_with_yaml(tmp_path, monkeypatch):
    """模拟打包运行：用户配置在数据目录，程序目录另有一份。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("ERP_DATA_DIR", str(tmp_path / "data"))
    program = tmp_path / "program"
    program.mkdir()
    monkeypatch.setattr(sys, "executable", str(program / "erp.exe"))

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.yaml").write_text(
        "erp:\n  host: http://user-level:8889\n", encoding="utf-8")
    (program / "config.yaml").write_text(
        "erp:\n  host: http://program:8889\n  username: shipped\n", encoding="utf-8")
    return tmp_path


def test_config_file_priority_user_then_program(frozen_with_yaml):
    found = paths.find_config_file()
    assert found == frozen_with_yaml / "data" / "config.yaml"

    (frozen_with_yaml / "data" / "config.yaml").unlink()
    assert paths.find_config_file() == frozen_with_yaml / "program" / "config.yaml"


def test_env_beats_yaml_beats_dotenv(tmp_path, monkeypatch, frozen_with_yaml):
    for key in ("ERP_HOST", "ERP_USERNAME", "ERP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    yaml_path = frozen_with_yaml / "data" / "config.yaml"
    yaml_path.write_text(
        "erp:\n  host: http://yaml:8889\n  username: from-yaml\n  password: yaml-pwd\n",
        encoding="utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("ERP_HOST=http://dotenv:8889\nERP_USERNAME=from-dotenv\n",
                      encoding="utf-8")

    cfg = ErpConfig.from_env(env_file=dotenv)
    assert cfg.host == "http://yaml:8889"          # yaml 覆盖 .env
    assert cfg.username == "from-yaml"

    monkeypatch.setenv("ERP_HOST", "http://real-env:8889")
    cfg = ErpConfig.from_env(env_file=dotenv)
    assert cfg.host == "http://real-env:8889"      # 真实环境变量最高


def test_missing_yaml_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("ERP_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    for key in ("ERP_HOST",):
        monkeypatch.delenv(key, raising=False)
    cfg = ErpConfig.from_env()
    assert cfg.host.startswith("http://")
