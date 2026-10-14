"""curl_cffi HTTP layer: TLS/JA3 browser impersonation, retries, rate limiting.

Every courier adapter goes through this module rather than calling curl_cffi
directly, so timeouts, retry policy and throttling stay consistent.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any

from curl_cffi import requests

DEFAULT_IMPERSONATE = "chrome"
DEFAULT_TIMEOUT = 30
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524}


class RateLimiter:
    """Minimum spacing between calls, shared across worker threads.

    ``slow_down`` lets a thread that hit a 429 widen the spacing for every
    other worker on the same courier, so the whole pool eases off together.
    """

    MAX_INTERVAL = 5.0

    def __init__(self, min_interval: float = 0.0) -> None:
        self.base_interval = min_interval
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def slow_down(self, factor: float = 2.0) -> None:
        with self._lock:
            self.min_interval = min(self.MAX_INTERVAL,
                                    max(0.5, self.min_interval * factor))

    def speed_up(self, decay: float = 0.9) -> None:
        """Ease back toward the configured spacing after a clean response.

        Without this, one 429 early in a run would keep every later request
        throttled at the penalty rate for the rest of the batch.
        """
        if self.min_interval <= self.base_interval:
            return
        with self._lock:
            eased = self.min_interval * decay
            self.min_interval = max(self.base_interval, eased)

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_at - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_at = now + self.min_interval


class HttpError(Exception):
    """Raised when a request fails after exhausting retries."""


class HttpClient:
    """Thread-safe wrapper around per-thread curl_cffi sessions.

    curl_cffi sessions are not safe to share across threads, so each thread
    gets its own (keeping its own cookie jar, which is exactly what the
    session-based couriers such as Ekart and BlueDart need).
    """

    def __init__(
        self,
        impersonate: str = DEFAULT_IMPERSONATE,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = 3,
        backoff: float = 1.5,
        min_interval: float = 0.0,
        proxy: str | None = None,
        verify: bool = True,
    ) -> None:
        self.impersonate = impersonate
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.proxy = proxy
        self.verify = verify
        self.limiter = RateLimiter(min_interval)
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            kwargs: dict[str, Any] = {
                "impersonate": self.impersonate,
                "timeout": self.timeout,
                "verify": self.verify,
            }
            if self.proxy:
                kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
            sess = requests.Session(**kwargs)
            self._local.session = sess
        return sess

    def reset_session(self) -> None:
        """Drop this thread's session so the next call starts a fresh cookie jar."""
        sess = getattr(self._local, "session", None)
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
        self._local.session = None

    @staticmethod
    def _retry_after(resp: requests.Response) -> float:
        """Seconds requested by a Retry-After header, 0 when absent/unparsable."""
        raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        try:
            return max(0.0, min(60.0, float(raw)))
        except (TypeError, ValueError):
            return 0.0

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            delay = (self.backoff ** attempt) + random.uniform(0, 0.4)
            try:
                resp = self.session.request(method, url, **kwargs)
            except Exception as exc:                     # network / TLS failure
                last_error = exc
                self.reset_session()
            else:
                if resp.status_code not in RETRY_STATUSES:
                    self.limiter.speed_up()
                    return resp
                last_error = HttpError(f"HTTP {resp.status_code} from {url}")
                if attempt == self.retries:
                    return resp                          # let the caller inspect it
                if resp.status_code == 429:
                    # Back off hard and widen the global spacing for this
                    # courier so the other workers stop pushing too.
                    delay = max(delay * 3, self._retry_after(resp), 2.0)
                    self.limiter.slow_down()

            if attempt < self.retries:
                time.sleep(delay)

        raise HttpError(f"{method} {url} failed after {self.retries + 1} attempts: {last_error}")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)
