"""HTTP 会话层。

关键约束（都是实测踩出来的）：
  * `requests.Session` 非线程安全 → 每线程一个 session；
  * 老旧 IIS 7.5 上 Keep-Alive 极易导致连接池超时 → 统一 `Connection: close`；
  * 服务端弱 → 并发由全局 RateLimiter 控制，而不是每工具自建线程池；
  * Cookie 以「字典」形式在 client 之间共享，登录态只需要建立一次。
"""

from __future__ import annotations

import threading
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import ErpConfig
from .errors import NetworkError
from .limiter import RateLimiter

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
)


class ErpClient:
    """一个 ERP 连接上下文：配置 + Cookie + 限流 + 线程内 Session。

    `ErpClient.spawn()` 用于开子线程：共享配置/Cookie/限流器，各自持有 session。
    """

    def __init__(
        self,
        cfg: ErpConfig,
        *,
        cookies: dict[str, str] | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.cfg = cfg
        self.cookies: dict[str, str] = dict(cookies or {})
        self.limiter = limiter or RateLimiter(cfg.max_concurrency, cfg.request_delay)
        self._local = threading.local()

    # ---------- 线程内 session ----------
    def _build_session(self) -> requests.Session:
        s = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=2,
            backoff_factor=1.0,
            # 注意：不要把 500 放进 status_forcelist —— 这个 ERP 的 500 往往是业务错误
            # （例如登录验证码错误），重试会把它变成难懂的「too many 500 error responses」
            # 并吞掉响应体，没法判断真正原因。网关类错误照旧重试。
            status_forcelist=(502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update(
            {
                "Connection": "close",
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        self._apply_cookies(s)
        return s

    def _apply_cookies(self, s: requests.Session) -> None:
        for name, value in self.cookies.items():
            s.cookies.set(name, value, domain=self.cfg.cookie_domain, path="/")

    @property
    def session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = self._build_session()
            self._local.session = sess
        return sess

    def reset_session(self) -> None:
        """丢弃当前线程的 session（切账号/会话失效时用）。"""
        self._local.session = None

    def spawn(self) -> "ErpClient":
        """给子线程用：共享 cookie 快照与限流器。"""
        return ErpClient(self.cfg, cookies=self.cookies, limiter=self.limiter)

    def capture_cookies(self) -> None:
        """把当前 session 的 cookie 同步成快照，供 spawn 出去的子 client 使用。"""
        self.cookies = {c.name: c.value for c in self.session.cookies}

    # ---------- 请求 ----------
    def url(self, path: str) -> str:
        if path.lower().startswith(("http://", "https://")):
            return path
        return urljoin(self.cfg.host.rstrip("/") + "/", path.lstrip("/"))

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        timeout = kwargs.pop("timeout", (self.cfg.timeout_connect, self.cfg.timeout_read))
        headers = kwargs.pop("headers", None)
        try:
            with self.limiter.slot():
                resp = self.session.request(method, self.url(path), timeout=timeout,
                                            headers=headers, **kwargs)
        except requests.RequestException as exc:
            raise NetworkError(
                f"{method} {self.url(path)} 失败：{exc}\n"
                f"  当前配置：ERP 主站={self.cfg.host}  验证码={self.cfg.captcha_host}"
            ) from exc

        # ERP 返回 UTF-8，但响应头常缺 charset；requests 会猜成 ISO-8859-1
        if not resp.headers.get("Content-Type", "").lower().count("charset"):
            resp.encoding = "utf-8"
        return resp

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, data=None, **kwargs) -> requests.Response:
        return self.request("POST", path, data=data, **kwargs)
