"""服务端运行状态：把配置、存储、会话池、限流器组装在一起。

单独成模块是为了让 `auth_routes` / `app` / 测试都能拿到同一份依赖，
同时避免 `app <-> routes` 循环导入。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ...core.auth import AuthManager
from ...core.config import ErpConfig
from ...core.errors import ConfigError
from ...core.ratelimit import LoginGuard, TokenBucket
from ...runtime.audit import ACTOR_SYSTEM, RESULT_FAILURE, RESULT_SUCCESS, build_entry
from ...runtime.auth_store import ApiKeyStore, AuthStore
from ...runtime.pg_store import PgStore
from ...runtime.secrets_box import KEY_NAME, SecretBox, SecretBoxUnavailable
from ...runtime.sessions import SessionProvider, UserSession
from .config import ServerConfig

_log = logging.getLogger("erp_tools.server.state")

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
    #: ERP 密码密文的加解密器；`store_erp_password: false` 时为 None
    secrets: SecretBox | None = None
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


def build_secrets(server_cfg: ServerConfig) -> SecretBox | None:
    """准备 ERP 密码加解密器（密钥文件在数据目录，与库分开）。"""
    if not server_cfg.store_erp_password:
        return None
    path = server_cfg.data_dir / KEY_NAME
    try:
        box = SecretBox.from_file(path)
    except (SecretBoxUnavailable, ValueError) as exc:
        server_cfg.notes.append(
            f"ERP 密码加密不可用（{exc}）；会话过期后将无法自动重登"
        )
        return None
    server_cfg.notes.append(f"ERP 密码将加密保存（密钥：{path}）")
    return box


def store_erp_secret(state: "ServerState", erp_username: str, password: str) -> bool:
    """把 ERP 密码加密后写库（失败只告警，绝不影响登录本身）。"""
    if state.secrets is None or not password:
        return False
    try:
        state.store.set_erp_secret(erp_username, state.secrets.encrypt(password))
        return True
    except Exception:  # noqa: BLE001 - 存不进密码只影响「自动重登」
        _log.warning("保存 ERP 密码密文失败（%s）", erp_username, exc_info=True)
        return False


def _relogin_audit(state: "ServerState", username: str, ok: bool,
                   error: str | None) -> None:
    """自动重登的审计：成功/失败都记一行（actor_type=system）。"""
    entry = build_entry(
        "erp.relogin",
        result=RESULT_SUCCESS if ok else RESULT_FAILURE,
        actor_name=username,
        actor_type=ACTOR_SYSTEM,
        target_type="erp_account",
        target_id=username,
        detail={"reason": error} if error else None,
    )
    try:
        state.store.add_audit(entry)
    except Exception:  # noqa: BLE001 - 审计失败不能影响请求
        _log.warning("写自动重登审计失败（%s）", username, exc_info=True)


def build_state(
    server_cfg: ServerConfig,
    erp_cfg: ErpConfig | None = None,
    *,
    login_impl: Callable[[str, str], UserSession] | None = None,
    alive_impl: Callable[[AuthManager], bool] | None = None,
    alive_ttl: float = 30.0,
    store: ApiKeyStore | None = None,
    secrets: SecretBox | None = None,
) -> ServerState:
    """组装运行状态。`login_impl` / `alive_impl` / `store` 是测试注入点。"""
    erp_cfg = erp_cfg or ErpConfig.from_env()
    store = store if store is not None else build_store(server_cfg)
    box = secrets if secrets is not None else build_secrets(server_cfg)

    state = ServerState(
        server=server_cfg,
        erp=erp_cfg,
        store=store,
        provider=None,  # type: ignore[arg-type]  # 见下方：provider 需要引用 state
        buckets=TokenBucket(rate=LOGIN_RATE_PER_MINUTE / 60.0, burst=LOGIN_BURST),
        guard=LoginGuard(max_failures=LOGIN_MAX_FAILURES,
                         lock_seconds=LOGIN_LOCK_SECONDS),
        secrets=box,
    )

    def lookup(username: str) -> str | None:
        """取该 ERP 账号存的密码（密文 → 明文）；没存/解不开返回 None。"""
        if state.secrets is None:
            return None
        try:
            token = state.store.get_erp_secret(username)
        except Exception:  # noqa: BLE001 - 读库失败等于「没存密码」
            _log.warning("读取 ERP 密码密文失败（%s）", username, exc_info=True)
            return None
        return state.secrets.try_decrypt(token)

    state.provider = SessionProvider(
        erp_cfg,
        global_concurrency=server_cfg.global_concurrency,
        cookie_dir=erp_cfg.cookie_dir,
        login_impl=login_impl,
        alive_impl=alive_impl,
        alive_ttl=alive_ttl,
        secret_lookup=lookup if state.secrets is not None else None,
        on_relogin=lambda name, ok, err: _relogin_audit(state, name, ok, err),
    )
    return state


__all__ = ["ServerState", "build_secrets", "build_state", "store_erp_secret"]
