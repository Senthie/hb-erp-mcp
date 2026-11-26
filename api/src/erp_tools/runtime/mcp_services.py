"""MCP 服务注册信息（本项目即 HB-MCP）的校验、归一化与序列化。

注册的是**服务本身**的元数据：叫什么、什么版本、从哪里访问、谁负责。
不涉及任何 ERP 账号或密码，所以字段全部可安全对外展示。

校验集中在这里，SQLite（本地/测试）与 PostgreSQL（生产）两个存储实现共用，
避免两边对同一个请求给出不同的接受范围。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: 本项目在服务目录里的默认名字
DEFAULT_SERVICE_NAME = "HB-MCP"

MAX_NAME = 64
MAX_VERSION = 32
MAX_ENDPOINT = 512
MAX_DESCRIPTION = 2000
MAX_OWNER = 128
MAX_TOOLS = 1_000_000
#: metadata 序列化后的字符数上限（防止有人往里塞整份文档）
MAX_METADATA_CHARS = 8000

TRANSPORTS = ("streamable-http", "sse", "stdio", "websocket")
ENVIRONMENTS = ("production", "staging", "development", "test")

_ENV_ALIASES = {"prod": "production", "stage": "staging", "dev": "development",
                "qa": "test", "local": "development"}
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_HTTP_URL = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def _text(payload: Mapping[str, Any], key: str, *, max_len: int,
          label: str, required: bool = False) -> str:
    raw = payload.get(key)
    if raw is None:
        raw = ""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError(f"{label}（{key}）必须是字符串")
    text = str(raw).strip()
    if _CONTROL_CHARS.search(text):
        raise ValueError(f"{label}（{key}）含非法控制字符")
    if required and not text:
        raise ValueError(f"{label}（{key}）必填")
    if len(text) > max_len:
        raise ValueError(f"{label}（{key}）最长 {max_len} 个字符")
    return text


def _choice(payload: Mapping[str, Any], key: str, allowed: tuple[str, ...],
            *, default: str, label: str,
            aliases: dict[str, str] | None = None) -> str:
    raw = payload.get(key)
    text = "" if raw is None else str(raw).strip().lower()
    if not text:
        return default
    text = (aliases or {}).get(text, text)
    if text not in allowed:
        raise ValueError(f"{label}（{key}）只能是：{' / '.join(allowed)}")
    return text


def _tools(payload: Mapping[str, Any]) -> int | None:
    raw = payload.get("tools")
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError("工具数量（tools）必须是整数")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("工具数量（tools）必须是整数") from exc
    if value < 0 or value > MAX_TOOLS:
        raise ValueError(f"工具数量（tools）必须在 0~{MAX_TOOLS} 之间")
    return value


def _metadata(payload: Mapping[str, Any]) -> str:
    raw = payload.get("metadata")
    if raw is None or raw == "":
        return "{}"
    if not isinstance(raw, Mapping):
        raise ValueError("metadata 必须是 JSON 对象")
    try:
        text = json.dumps(dict(raw), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata 不是可序列化的 JSON 对象") from exc
    if len(text) > MAX_METADATA_CHARS:
        raise ValueError(f"metadata 序列化后不能超过 {MAX_METADATA_CHARS} 个字符")
    return text


def normalize_registration(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把注册请求归一化成可以直接入库的一行；非法输入抛 ``ValueError``。"""
    if not isinstance(payload, Mapping):
        raise ValueError("请求体必须是 JSON 对象")

    name = _text(payload, "name", max_len=MAX_NAME, label="服务名称", required=True)
    version = _text(payload, "version", max_len=MAX_VERSION, label="服务版本")
    endpoint = _text(payload, "endpoint", max_len=MAX_ENDPOINT, label="访问地址")
    transport = _choice(payload, "transport", TRANSPORTS,
                        default="streamable-http", label="传输方式")
    environment = _choice(payload, "environment", ENVIRONMENTS, default="production",
                          label="环境", aliases=_ENV_ALIASES)

    if endpoint and transport != "stdio" and not _HTTP_URL.match(endpoint):
        raise ValueError(
            "访问地址（endpoint）必须是 http:// 或 https:// 开头的 URL；"
            "stdio 传输可以不填"
        )

    return {
        "name": name,
        "version": version,
        "endpoint": endpoint or None,
        "transport": transport,
        "environment": environment,
        "description": _text(payload, "description", max_len=MAX_DESCRIPTION,
                             label="服务描述") or None,
        "owner": _text(payload, "owner", max_len=MAX_OWNER, label="负责人") or None,
        "tools": _tools(payload),
        "metadata": _metadata(payload),
    }


@dataclass(frozen=True)
class McpService:
    """一条已注册的 MCP 服务记录。"""

    name: str
    id: int | None = None
    version: str = ""
    endpoint: str | None = None
    transport: str = "streamable-http"
    environment: str = "production"
    description: str | None = None
    owner: str | None = None
    tools: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "McpService":
        raw_meta = row.get("metadata")
        if isinstance(raw_meta, str):
            try:
                raw_meta = json.loads(raw_meta)
            except ValueError:
                raw_meta = {}
        if not isinstance(raw_meta, Mapping):
            raw_meta = {}
        return cls(
            id=int(row["id"]) if row.get("id") is not None else None,
            name=str(row.get("name") or ""),
            version=str(row.get("version") or ""),
            endpoint=row.get("endpoint"),
            transport=str(row.get("transport") or "streamable-http"),
            environment=str(row.get("environment") or "production"),
            description=row.get("description"),
            owner=row.get("owner"),
            tools=int(row["tools"]) if row.get("tools") is not None else None,
            metadata=dict(raw_meta),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "endpoint": self.endpoint,
            "transport": self.transport,
            "environment": self.environment,
            "description": self.description,
            "owner": self.owner,
            "tools": self.tools,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


__all__ = [
    "DEFAULT_SERVICE_NAME",
    "ENVIRONMENTS",
    "TRANSPORTS",
    "McpService",
    "normalize_registration",
]
