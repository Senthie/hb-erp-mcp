"""全局限流。

为什么必须全局：实测该 ERP 服务端约 4~8 并发就饱和（8/16 线程时单请求从 ~0.95s
涨到 ~1.6s，吞吐不再上升）。所以并发上限属于「ERP 实例」，不能每个工具各开一个
线程池各带 8 个并发。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class RateLimiter:
    """信号量（并发上限）+ 全局节流（最小请求间隔）。线程安全。"""

    def __init__(self, max_concurrency: int = 8, min_interval: float = 0.0) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self.min_interval = max(0.0, float(min_interval))
        self._sem = threading.BoundedSemaphore(self.max_concurrency)
        self._pace_lock = threading.Lock()
        self._next_allowed = 0.0

    @contextmanager
    def slot(self):
        self._sem.acquire()
        try:
            if self.min_interval:
                self._wait_turn()
            yield
        finally:
            self._sem.release()

    def _wait_turn(self) -> None:
        with self._pace_lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self.min_interval

    # 兼容 API 名称
    def acquire(self) -> None:
        self._sem.acquire()

    def release(self) -> None:
        self._sem.release()
