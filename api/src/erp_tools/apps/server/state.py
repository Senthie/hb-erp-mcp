"""服务端运行状态：把配置、存储、会话池、限流器组装在一起。

单独成模块是为了让 `auth_routes` / `app` / 测试都能拿到同一份依赖，
同时避免 `app <-> routes` 循环导入。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.auth import AuthManager
from ...core.config import ErpConfig
from ...core.errors import ConfigError
from ...core.ratelimit import LoginGuard, TokenBucket
from ...runtime.auth_store import ApiKeyStore, AuthStore
from ...runtime.pg_store import PgStore
from ...runtime.sessions import SessionProvider, UserSession
from .config import ServerConfig

#: 登录限流：每 IP / 每账号 5 次/分钟
LOGIN_RATE_PER_MINUTE = 5
LOGIN_BURST = 5
#: 连续失败 10 次锁 15 分钟
LOGIN_MAX_FAILURES = 10
LOGIN_LOCK_SECONDS = 900.0
#: 服务注册限流：每 IP 10 次/分钟
REGISTER_RATE_PER_MINUTE = 10
REGISTER_BURST = 10
#: 平台账号登录 / 注册限流：每 IP 10 次/分钟
PLATFORM_RATE_PER_MINUTE = 10
PLATFORM_BURST = 10


@dataclass
class ServerState:
    server: ServerConfig
    erp: ErpConfig
    store: ApiKeyStore
    provider: SessionProvider
    buckets: TokenBucket
    guard: LoginGuard
    #: 注册接口独立的令牌桶（与登录互不影响）
    registration: TokenBucket = field(
        default_factory=lambda: TokenBucket(
            rate=REGISTER_RATE_PER_MINUTE / 60.0, burst=REGISTER_BURST
        )
    )
    #: 平台账号登录 / 注册独立的令牌桶
    platform_auth: TokenBucket = field(
        default_factory=lambda: TokenBucket(
            rate=PLATFORM_RATE_PER_MINUTE / 60.0, burst=PLATFORM_BURST
        )
    )
    extra: dict[str, object] = field(default_factory=dict)

    # ---------- 便捷访问 ----------
    @property
    def global_concurrency(self) -> int:
        return self.server.global_concurrency

    def close(self) -> None:
        self.store.close()
        self.provider.close()


def build_store(server_cfg: ServerConfig) -> ApiKeyStore:
    """按配置选存储：有 PostgreSQL 连接串走 PG，显式给了 `--db` 才用 SQLite。"""
    if server_cfg.db_path is not None:
        return AuthStore(server_cfg.db_path).init_schema()
    if server_cfg.pg_dsn:
        return PgStore(server_cfg.pg_dsn).init_schema()
    raise ConfigError(
        "未配置数据库：请在 config.yaml 的 server 段写 storage: postgres + pg_dsn，"
        "或 storage: sqlite（本地开发/测试）。`erp init --mode mcp` 会自动生成；"
        "也可以用 --db / --pg-dsn 临时指定"
    )


def build_state(
    server_cfg: ServerConfig,
    erp_cfg: ErpConfig | None = None,
    *,
    login_impl: Callable[[str, str], UserSession] | None = None,
    alive_impl: Callable[[AuthManager], bool] | None = None,
    alive_ttl: float = 30.0,
    store: ApiKeyStore | None = None,
) -> ServerState:
    """组装运行状态。`login_impl` / `alive_impl` / `store` 是测试注入点。"""
    erp_cfg = erp_cfg or ErpConfig.from_env()
    store = store if store is not None else build_store(server_cfg)
    provider = SessionProvider(
        erp_cfg,
        global_concurrency=server_cfg.global_concurrency,
        cookie_dir=erp_cfg.cookie_dir,
        login_impl=login_impl,
        alive_impl=alive_impl,
        alive_ttl=alive_ttl,
    )
    return ServerState(
        server=server_cfg,
        erp=erp_cfg,
        store=store,
        provider=provider,
        buckets=TokenBucket(rate=LOGIN_RATE_PER_MINUTE / 60.0, burst=LOGIN_BURST),
        guard=LoginGuard(max_failures=LOGIN_MAX_FAILURES,
                         lock_seconds=LOGIN_LOCK_SECONDS),
    )


__all__ = ["ServerState", "build_state"]
