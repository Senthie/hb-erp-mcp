"""服务端运行状态：把配置、存储、会话池、限流器组装在一起。

单独成模块是为了让 `auth_routes` / `app` / 测试都能拿到同一份依赖，
同时避免 `app <-> routes` 循环导入。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.auth import AuthManager
from ...core.config import ErpConfig
from ...core.ratelimit import LoginGuard, TokenBucket
from ...runtime.auth_store import AuthStore
from ...runtime.sessions import SessionProvider, UserSession
from .config import ServerConfig

#: 登录限流：每 IP / 每账号 5 次/分钟
LOGIN_RATE_PER_MINUTE = 5
LOGIN_BURST = 5
#: 连续失败 10 次锁 15 分钟
LOGIN_MAX_FAILURES = 10
LOGIN_LOCK_SECONDS = 900.0


@dataclass
class ServerState:
    server: ServerConfig
    erp: ErpConfig
    store: AuthStore
    provider: SessionProvider
    buckets: TokenBucket
    guard: LoginGuard
    extra: dict[str, object] = field(default_factory=dict)

    # ---------- 便捷访问 ----------
    @property
    def global_concurrency(self) -> int:
        return self.server.global_concurrency

    def close(self) -> None:
        self.store.close()
        self.provider.close()


def build_state(
    server_cfg: ServerConfig,
    erp_cfg: ErpConfig | None = None,
    *,
    login_impl: Callable[[str, str], UserSession] | None = None,
    alive_impl: Callable[[AuthManager], bool] | None = None,
    alive_ttl: float = 30.0,
) -> ServerState:
    """组装运行状态。`login_impl` / `alive_impl` 是测试注入点。"""
    erp_cfg = erp_cfg or ErpConfig.from_env()
    store = AuthStore(server_cfg.db_path).init_schema()
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
