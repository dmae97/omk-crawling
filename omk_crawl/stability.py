"""Stability layer — circuit breaker, session management, structured logging.

Hardens the crawling stack against transient failures, connection churn,
and cascading errors. Complements resilience.py (rate-limit/retry/cache).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


# ─────────────────────────────────────────────
# Structured logger
# ─────────────────────────────────────────────

def get_logger(name: str = "omk_crawl", level: int = logging.INFO) -> logging.Logger:
    """Structured logger with consistent format across the stack."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(h)
    logger.setLevel(level)
    return logger


log = get_logger()


# ─────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"      # normal — requests flow
    OPEN = "open"          # tripped — requests fail fast
    HALF_OPEN = "half_open"  # probing — one trial request allowed


@dataclass
class CircuitBreaker:
    """Per-host circuit breaker. Stops hammering a failing endpoint.

    - CLOSED: requests pass; failures counted.
    - OPEN: after `failure_threshold` consecutive failures, fail fast for
      `recovery_timeout` seconds.
    - HALF_OPEN: after timeout, allow one probe; success closes, failure reopens.
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    _failures: int = field(default=0, init=False)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def allow(self) -> bool:
        """Return True if a request is allowed through."""
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                log.warning("circuit OPEN after %d failures", self._failures)

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute fn through the breaker. Raises CircuitOpenError if open."""
        if not self.allow():
            raise CircuitOpenError(f"circuit open, retry after {self.recovery_timeout}s")
        try:
            result = fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open."""


class BreakerRegistry:
    """One circuit breaker per host key."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._ft = failure_threshold
        self._rt = recovery_timeout

    def get(self, key: str) -> CircuitBreaker:
        with self._lock:
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker(self._ft, self._rt)
            return self._breakers[key]

    def status(self) -> dict[str, str]:
        return {k: v.state.value for k, v in self._breakers.items()}


# ─────────────────────────────────────────────
# Session manager (connection reuse + cookies)
# ─────────────────────────────────────────────

class SessionManager:
    """Manages curl_cffi sessions for connection reuse and cookie persistence.

    Solves: repeated TLS handshakes, lost cookies across requests.
    """

    def __init__(self, impersonate: str = "chrome124") -> None:
        self.impersonate = impersonate
        self._session = None
        self._lock = threading.Lock()

    def get(self):
        """Get or create a persistent session."""
        with self._lock:
            if self._session is None:
                from curl_cffi import requests as cffi
                self._session = cffi.Session(impersonate=self.impersonate)
            return self._session

    def reset(self) -> None:
        """Close and recreate the session (e.g. after auth change)."""
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
            self._session = None

    def set_cookies(self, cookies: dict[str, str]) -> None:
        """Inject cookies (e.g. user-provided session for legitimately-accessible content)."""
        s = self.get()
        for k, v in cookies.items():
            s.cookies.set(k, v)

    def close(self) -> None:
        self.reset()


# ─────────────────────────────────────────────
# Timeout budget
# ─────────────────────────────────────────────

@dataclass
class TimeoutBudget:
    """Tracks remaining time across a multi-step operation."""

    total: float
    _start: float = field(default_factory=time.monotonic, init=False)

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - (time.monotonic() - self._start))

    @property
    def expired(self) -> bool:
        return self.remaining <= 0

    def step(self, min_step: float = 1.0) -> float:
        """Return timeout for the next step (at least min_step, capped by remaining)."""
        return max(min_step, min(self.remaining, 30.0))
