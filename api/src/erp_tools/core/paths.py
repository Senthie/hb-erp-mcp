"""运行期路径解析：源码运行 vs PyInstaller 打包运行。

关键约束：安装目录可能位于 `C:\\Program Files`（普通用户**不可写**）。
所以打包后所有需要写的东西（.env、cookies、日志、导出）一律落在
`%APPDATA%\\erp-tools`，而不是 exe 旁边。源码运行时保持原来的「当前目录」行为。

可用 `ERP_DATA_DIR` 环境变量整体覆盖数据目录（便于测试 / 绿色版）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "erp-tools"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def app_data_dir() -> Path:
    """可写数据目录。"""
    override = (os.environ.get("ERP_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
        if not base:
            base = str(Path.home() / ".config")
        return Path(base) / APP_NAME
    return Path.cwd()


def default_cookie_dir() -> Path:
    """Cookie / QXID 缓存目录。"""
    return app_data_dir() / "cookies"


def resolve_cookie_dir(raw: str | None) -> Path:
    """把 COOKIE_DIR 配置解析成绝对路径。

    相对路径在打包运行时以数据目录为基准（安装目录不可写，且用户的工作目录不确定）。
    """
    text = (raw or "").strip()
    if not text:
        return default_cookie_dir()
    path = Path(text).expanduser()
    if is_frozen() and not path.is_absolute():
        return app_data_dir() / path
    return path


def candidate_env_files() -> list[Path]:
    """按优先级返回候选 .env 路径。

    打包运行优先用数据目录（避免用户当前目录里的杂项 .env 干扰）；
    源码运行优先用当前目录（方便开发）。
    """
    cwd_env = Path.cwd() / ".env"
    data_env = app_data_dir() / ".env"
    if data_env == cwd_env:
        return [cwd_env]
    return [data_env, cwd_env] if is_frozen() else [cwd_env, data_env]


def find_env_file(explicit: str | Path | None = None) -> Path | None:
    """定位 .env：显式路径 > 当前目录 > 数据目录。"""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for p in candidate_env_files():
        if p.is_file():
            return p
    return None


def preferred_env_path() -> Path:
    """写 .env 时应该写到哪里（打包后写数据目录，源码运行写当前目录）。"""
    if is_frozen():
        return app_data_dir() / ".env"
    return Path.cwd() / ".env"


def bundled_dir() -> Path | None:
    """PyInstaller 解包目录（onedir 下是 `_internal`）；源码运行返回 None。"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def program_dir() -> Path:
    """程序所在目录（打包后为 exe 所在目录，源码运行为当前目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def config_candidates() -> list[Path]:
    """config.yaml 候选路径（按优先级）。

    打包：用户数据目录 → 程序目录（随包携带）→ 当前目录 → 内置模板
    源码：当前目录 → 打包数据目录（一般没有）
    """
    out: list[Path] = []
    if is_frozen():
        out.append(app_data_dir() / "config.yaml")
        out.append(program_dir() / "config.yaml")
    out.append(Path.cwd() / "config.yaml")

    base = bundled_dir()
    if base is not None:
        out.append(base / "config.example.yaml")
        out.append(program_dir() / "config.example.yaml")
    else:
        out.append(Path.cwd() / "config.example.yaml")

    deduped: list[Path] = []
    for p in out:
        if p not in deduped:
            deduped.append(p)
    return deduped


def find_config_file(explicit: str | Path | None = None) -> Path | None:
    """定位 config.yaml：显式路径优先，否则按上面的优先级。"""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    for p in config_candidates():
        if p.is_file():
            return p
    return None


def preferred_config_path() -> Path:
    """写 config.yaml 时应该写到哪里：打包写数据目录，源码运行写当前目录。"""
    if is_frozen():
        return app_data_dir() / "config.yaml"
    return Path.cwd() / "config.yaml"


def describe_paths() -> dict[str, str]:
    """给 `erp config` 用的路径总览。"""
    from .config import ErpConfig
    from .settings import load_config_file

    cfg = ErpConfig.from_env()
    _env, yaml_path = load_config_file()
    env_file = find_env_file()
    return {
        "运行方式": "PyInstaller 打包" if is_frozen() else "源码/venv",
        "程序位置": sys.executable,
        "数据目录": str(app_data_dir()),
        "配置(yaml)": str(yaml_path) if yaml_path else "(未找到，可用 erp setup 生成)",
        "配置(.env)": str(env_file) if env_file else "(无)",
        "Cookie 目录": str(cfg.cookie_dir),
    }
