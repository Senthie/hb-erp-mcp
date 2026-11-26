"""core.ratelimit：令牌桶与登录锁定。"""

from __future__ import annotations

from erp_tools.core.ratelimit import LoginGuard, TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_token_bucket_allows_burst_then_denies() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=1.0 / 60.0, burst=3, clock=clock)  # 每分钟 1 次，桶容量 3

    for _ in range(3):
        allowed, _retry = bucket.allow("ip:1.1.1.1")
        assert allowed

    allowed, retry_after = bucket.allow("ip:1.1.1.1")
    assert not allowed
    assert retry_after > 0

    # 其它 key 不受影响
    assert bucket.allow("ip:2.2.2.2")[0]


def test_token_bucket_refills_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=1.0, burst=2, clock=clock)

    assert bucket.allow("k")[0]
    assert bucket.allow("k")[0]
    assert not bucket.allow("k")[0]

    clock.advance(1.0)          # 补 1 个令牌
    assert bucket.allow("k")[0]
    assert not bucket.allow("k")[0]


def test_token_bucket_prune() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=1.0, burst=1, clock=clock)
    bucket.allow("a")
    clock.advance(7200)
    assert bucket.prune(max_idle=3600) == 1
    assert bucket.prune(max_idle=3600) == 0


def test_login_guard_locks_after_max_failures() -> None:
    clock = FakeClock()
    guard = LoginGuard(max_failures=3, lock_seconds=60, clock=clock)

    assert guard.check("u") is None
    for _ in range(3):
        guard.fail("u")

    locked_for = guard.check("u")
    assert locked_for is not None and 0 < locked_for <= 60

    clock.advance(61)
    assert guard.check("u") is None


def test_login_guard_success_resets_counter() -> None:
    clock = FakeClock()
    guard = LoginGuard(max_failures=3, lock_seconds=60, clock=clock)

    guard.fail("u")
    guard.fail("u")
    guard.success("u")
    guard.fail("u")             # 第一次失败重新计数
    assert guard.check("u") is None
