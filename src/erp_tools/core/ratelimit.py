"""进程内限流与登录失败锁定。

都是**内存态**：进程重启即清零，够用且零依赖。
（多实例部署时要换成 Redis，本项目暂不支持多实例。）
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucket:
    """按 key 独立的令牌桶，用于「每 IP / 每账号」限流。

    ::

        bucket = TokenBucket(rate=5/60, burst=5)      # 每分钟 5 次
        allowed, retry_after = bucket.allow("ip:1.2.3.4")
    """

    def __init__(self, rate: float, burst: int, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = max(1e-6, float(rate))
        self._burst = max(1.0, float(burst))
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """返回 ``(是否放行, 建议重试秒数)``。"""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self._burst, updated=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
            bucket.updated = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, 0.0
            return False, (cost - bucket.tokens) / self._rate

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def prune(self, max_idle: float = 3600.0) -> int:
        """清掉长时间没动的桶，防止字典无限增长。返回清理数量。"""
        now = self._clock()
        with self._lock:
            stale = [k for k, b in self._buckets.items() if now - b.updated > max_idle]
            for k in stale:
                del self._buckets[k]
            return len(stale)


@dataclass
class _Failure:
    count: int = 0
    locked_until: float = 0.0


class LoginGuard:
    """连续失败锁定：10 次失败 → 锁 15 分钟。"""

    def __init__(self, *, max_failures: int = 10, lock_seconds: float = 900.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max_failures = max(1, int(max_failures))
        self._lock_seconds = max(1.0, float(lock_seconds))
        self._clock = clock
        self._state: dict[str, _Failure] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """可尝试返回 None；被锁则返回剩余秒数。"""
        now = self._clock()
        with self._lock:
            item = self._state.get(key)
            if item is None or item.locked_until <= now:
                return None
            return item.locked_until - now

    def fail(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            item = self._state.setdefault(key, _Failure())
            item.count += 1
            if item.count >= self._max_failures:
                item.locked_until = now + self._lock_seconds
                item.count = 0

    def success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._state.clear()
            else:
                self._state.pop(key, None)


__all__ = ["TokenBucket", "LoginGuard"]
