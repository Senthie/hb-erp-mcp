"""ERP 会话池。

因为**不保存用户密码**（见 README「服务端模式」），所以这里的 login 只在
`POST /v1/auth/login` 的调用栈里短暂持有明文密码；登录成功立刻重建一个
`password=""` 的干净配置，密码对象随栈帧一起被回收。

两类并发控制：

* **每用户**：`ErpClient` 自带的 `RateLimiter`（沿用原有 `ERP_MAX_CONCURRENCY`）；
* **全局**：`SessionProvider.slot()` —— 实测该 ERP 4~8 并发即饱和，
  多用户时若各自开 8 线程会把上游打死，所以必须有一道进程级闸门。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..core.auth import AuthManager
from ..core.config import ErpConfig
from ..core.errors import ErpError
from ..core.session import ErpClient


class SessionExpired(ErpError):
    """绑定的 ERP 会话已失效 → 对应的 api-key 应一并作废。"""


@dataclass
class UserSession:
    username: str
    auth: AuthManager
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    user_id: str | None = None
    pages: dict[str, str] = field(default_factory=dict)
    #: 最近一次存活探测结果与时间戳（避免每次请求都打一次 ERP）
    alive: bool = True
    alive_checked_at: float = 0.0
    #: 最近一次探测的网络异常（仅诊断用，不代表会话失效）
    last_error: str | None = None


class SessionProvider:
    """按 ERP 账号维护会话，并提供全局并发闸门。

    :param login_impl: 测试注入点，签名 `(username, password) -> UserSession`
    :param alive_impl: 测试注入点，签名 `(AuthManager) -> bool`
    """

    def __init__(
        self,
        cfg: ErpConfig,
        *,
        global_concurrency: int = 6,
        cookie_dir: str | Path | None = None,
        login_impl: Callable[[str, str], UserSession] | None = None,
        alive_impl: Callable[[AuthManager], bool] | None = None,
        alive_ttl: float = 30.0,
    ) -> None:
        self._cfg = cfg
        self._cookie_dir = Path(cookie_dir) if cookie_dir else cfg.cookie_dir
        self._login_impl = login_impl
        self._alive_impl = alive_impl
        self._alive_ttl = max(0.0, float(alive_ttl))

        self._sessions: dict[str, UserSession] = {}
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(max(1, int(global_concurrency)))
        self.global_concurrency = max(1, int(global_concurrency))

    # ══════════════════ 全局并发闸门 ══════════════════
    @contextmanager
    def slot(self, timeout: float | None = None) -> Iterator[None]:
        """占用一个全局并发位（排队而非拒绝）。"""
        acquired = self._slots.acquire(timeout=timeout) if timeout else self._slots.acquire()
        if not acquired:
            raise TimeoutError("等待 ERP 并发槽位超时")
        try:
            yield
        finally:
            self._slots.release()

    # ══════════════════ 登录 ══════════════════
    def login(self, username: str, password: str) -> UserSession:
        """用一次性密码登录并登记会话。**不返回也不保存密码。**"""
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("username 与 password 必填")
        session = (self._login_impl or self._default_login)(username, password)
        with self._lock:
            self._sessions[username] = session
        return session

    def _default_login(self, username: str, password: str) -> UserSession:
        # 只有这一层持有明文密码
        login_cfg = self._cfg.replace(
            username=username, password=password, cookie_dir=str(self._cookie_dir)
        )
        login_auth = AuthManager(ErpClient(login_cfg), interactive=False)
        login_auth.login()
        cookies = dict(login_auth.client.cookies)
        login_auth = None  # noqa: F841 - 显式丢弃带密码的对象引用

        # 用「无密码」配置重建，登录态走 cookie
        clean_cfg = login_cfg.replace(password="")
        auth = AuthManager(ErpClient(clean_cfg, cookies=cookies), interactive=False)
        session = UserSession(username=username, auth=auth, user_id=auth.user_id)

        try:
            session.pages = auth.qxid_map(refresh=True)
        except ErpError:
            session.pages = {}
            auth._qxid_map = {}  # noqa: SLF001 - 探测失败不该拖垮登录
        return session

    # ══════════════════ 取用 ══════════════════
    def get(self, username: str) -> UserSession | None:
        with self._lock:
            return self._sessions.get(username)

    def require(self, username: str, *, check_alive: bool = True) -> UserSession:
        """取会话；必要时先从磁盘 cookie 恢复。失效则抛 `SessionExpired`。"""
        session = self.get(username)
        if session is None:
            session = self._restore(username)
        if session is None:
            raise SessionExpired(f"账号 {username} 没有可用的 ERP 会话，请重新登录")
        if check_alive and not self.is_alive(username):
            raise SessionExpired(f"账号 {username} 的 ERP 会话已过期，请重新登录")
        session.last_used = time.time()
        return session

    def _restore(self, username: str) -> UserSession | None:
        """服务重启后，用磁盘上的 cookie 恢复会话（api-key 因此不会因重启失效）。"""
        cfg = self._cfg.replace(
            username=username, password="", cookie_dir=str(self._cookie_dir)
        )
        auth = AuthManager(ErpClient(cfg), interactive=False)
        if not auth.load_cookies():
            return None
        if not auth.is_logged_in():
            return None
        session = UserSession(username=username, auth=auth, user_id=auth.user_id,
                              alive=True, alive_checked_at=time.time())
        with self._lock:
            self._sessions.setdefault(username, session)
            return self._sessions[username]

    # ══════════════════ 存活探测 ══════════════════
    def is_alive(self, username: str, *, refresh: bool = False) -> bool:
        session = self.get(username)
        if session is None:
            return False

        now = time.time()
        if (not refresh and session.alive_checked_at
                and now - session.alive_checked_at < self._alive_ttl):
            return session.alive

        probe = self._alive_impl or (lambda auth: auth.is_logged_in())
        try:
            with self.slot():
                alive = bool(probe(session.auth))
        except Exception as exc:  # noqa: BLE001
            # 网络/上游故障 ≠ 会话失效。这里必须保守：否则 ERP 抖一下
            # 就会把所有用户的 api-key 全部作废。
            session.last_error = str(exc)
            session.alive_checked_at = now
            return session.alive

        session.alive = alive
        session.alive_checked_at = now
        session.last_error = None
        if not alive:
            self.drop(username)
        return alive

    # ══════════════════ 释放 ══════════════════
    def drop(self, username: str) -> None:
        with self._lock:
            self._sessions.pop(username, None)

    def close(self) -> None:
        with self._lock:
            self._sessions.clear()


__all__ = ["SessionExpired", "SessionProvider", "UserSession"]
