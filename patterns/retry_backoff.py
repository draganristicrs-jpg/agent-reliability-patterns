"""Retries with exponential backoff + jitter, and a circuit breaker.

Pattern write-up: https://orbiresearch.com/lab/llm-api-retry-backoff-circuit-breakers
Failure report:   https://orbiresearch.com/lab/agent-crashed-on-429

Rules this module enforces:
- Only retry errors that are worth retrying (429, 5xx, timeouts).
- Honor the provider's Retry-After when it is given.
- Add jitter so many workers don't retry in lockstep.
- Stop calling a dependency that keeps failing (circuit breaker),
  instead of hammering it and burning budget.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class RetryableError(Exception):
    """Raise this (or a subclass) from your call for errors worth retrying."""

    def __init__(self, message: str = "", status: Optional[int] = None,
                 retry_after: Optional[float] = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class CircuitOpenError(Exception):
    """Raised when the breaker is open and the call is refused without trying."""


@dataclass
class CircuitBreaker:
    """Classic three-state breaker: closed -> open -> half-open -> closed."""

    failure_threshold: int = 5          # consecutive failures before opening
    reset_timeout: float = 30.0         # seconds to wait before a trial call
    clock: Callable[[], float] = time.monotonic
    _failures: int = field(default=0, init=False)
    _opened_at: Optional[float] = field(default=None, init=False)

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self.clock() - self._opened_at >= self.reset_timeout:
            return "half-open"
        return "open"

    def before_call(self) -> None:
        if self.state == "open":
            raise CircuitOpenError("circuit open: dependency is failing, call refused")

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        if self.state == "half-open":
            # Trial call failed: open again for a full timeout.
            self._opened_at = self.clock()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self.clock()


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0,
                  retry_after: Optional[float] = None,
                  rng: Callable[[], float] = random.random) -> float:
    """Delay before retry number `attempt` (1-based).

    Full jitter: uniform in [0, min(cap, base * 2**(attempt-1))].
    If the server sent Retry-After, never wait less than that.
    """
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    delay = rng() * ceiling
    if retry_after is not None:
        delay = max(delay, retry_after)
    return delay


def call_with_retry(fn: Callable[[], T], *, max_attempts: int = 5,
                    breaker: Optional[CircuitBreaker] = None,
                    base: float = 0.5, cap: float = 30.0,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """Call `fn`, retrying RetryableError with backoff, guarded by `breaker`.

    Non-retryable exceptions propagate immediately and are NOT counted as
    dependency failures (a bug in your own code shouldn't open the breaker).
    """
    last_error: Optional[RetryableError] = None
    for attempt in range(1, max_attempts + 1):
        if breaker:
            breaker.before_call()
        try:
            result = fn()
        except RetryableError as e:
            last_error = e
            if breaker:
                breaker.record_failure()
            if attempt == max_attempts:
                break
            sleep(backoff_delay(attempt, base, cap, e.retry_after))
            continue
        if breaker:
            breaker.record_success()
        return result
    assert last_error is not None
    raise last_error
