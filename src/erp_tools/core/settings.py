"""YAML 配置文件。

随包携带一份 `config.yaml`（模板），用户级覆盖写 `%APPDATA%\\erp-tools\\config.yaml`。
为了与既有的环境变量/`.env` 机制兼容，这里把 YAML 的嵌套结构**拍平成 ERP_* 环境变量**，
后续 `ErpConfig` 的读取逻辑完全不变。

优先级（高 → 低）：
    命令行参数 > 真实环境变量 > config.yaml > .env > 内置默认值
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ConfigError
from .paths import find_config_file

#: (YAML 段, 键) -> 环境变量名
YAML_TO_ENV: dict[tuple[str, str], str] = {
    ("erp", "host"): "ERP_HOST",
    ("erp", "captcha_host"): "ERP_CAPTCHA_HOST",
    ("erp", "username"): "ERP_USERNAME",
    ("erp", "password"): "ERP_PASSWORD",
    ("erp", "require_auth"): "ERP_REQUIRE_AUTH",
    ("runtime", "max_concurrency"): "ERP_MAX_CONCURRENCY",
    ("runtime", "request_delay"): "ERP_REQUEST_DELAY",
    ("runtime", "timeout_connect"): "ERP_TIMEOUT_CONNECT",
    ("runtime", "timeout_read"): "ERP_TIMEOUT_READ",
    ("runtime", "page_size_max"): "ERP_PAGE_SIZE_MAX",
    ("runtime", "cookie_dir"): "COOKIE_DIR",
    ("qxid", "quote"): "ERP_QXID_QUOTE",
    ("qxid", "quote_index"): "ERP_QXID_QUOTE_INDEX",
    ("qxid", "price"): "ERP_QXID_PRICE",
    ("qxid", "price_index"): "ERP_QXID_PRICE_INDEX",
    ("qxid", "attendance"): "ERP_QXID_ATTENDANCE",
}


def read_yaml(path: str | Path) -> dict[str, Any]:
    """读取 YAML（缺文件返回空 dict）。"""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - 依赖已声明
        raise ConfigError("缺少 PyYAML，请先 `uv sync`") from exc

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"配置文件 {p} 解析失败：{exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"配置文件 {p} 顶层必须是映射（key: value）")
    return data


def yaml_to_env(data: dict[str, Any]) -> dict[str, str]:
    """把嵌套配置拍平成 {ERP_*: "值"}。空值/缺省段会被跳过。"""
    out: dict[str, str] = {}
    for (section, key), env_name in YAML_TO_ENV.items():
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        value = block.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            out[env_name] = "true" if value else "false"
        else:
            out[env_name] = str(value)
    return out


def load_config_file(explicit: str | Path | None = None) -> tuple[dict[str, str], Path | None]:
    """查找并加载配置文件，返回 (拍平后的环境变量, 实际路径)。"""
    path = find_config_file(explicit)
    if path is None:
        return {}, None
    return yaml_to_env(read_yaml(path)), path


def update_yaml(path: str | Path, values: dict[str, dict[str, Any]]) -> Path:
    """就地更新 YAML 中的指定段落/键，保留其它内容。"""
    import yaml

    p = Path(path)
    data = read_yaml(p)
    for section, items in values.items():
        block = data.get(section)
        if not isinstance(block, dict):
            block = {}
        block.update(items)
        data[section] = block

    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    p.write_text(text, encoding="utf-8")
    return p
