"""Base class every courier adapter extends."""
from __future__ import annotations

import threading
from typing import Any, Iterable

from .http_client import HttpClient
from .models import NOT_FOUND, TrackResult

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


class BaseCourier:
    """Contract for a courier adapter.

    Subclasses implement ``_fetch`` and, optionally, ``fetch_many`` when the
    upstream API accepts several AWBs in one call. Everything else -- error
    wrapping, status normalization, lazily-primed session state -- is handled
    here so a new courier is a single small file.
    """

    name: str = "BASE"
    aliases: tuple[str, ...] = ()
    #: seconds to leave between two calls to this courier
    min_interval: float = 0.0
    #: how many AWBs ``fetch_many`` may take in one upstream request
    batch_size: int = 1

    def __init__(self, client: HttpClient | None = None, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.client = client or HttpClient(min_interval=self.min_interval)
        self._prime_lock = threading.Lock()
        self._primed = False

    # ---- hooks for subclasses -------------------------------------------------

    def _prime(self) -> None:
        """One-time setup (fetch a CSRF token, scrape a static key, ...)."""

    def _fetch(self, awb: str) -> TrackResult:
        raise NotImplementedError

    # ---- public API -----------------------------------------------------------

    def ensure_primed(self) -> None:
        if self._primed:
            return
        with self._prime_lock:
            if not self._primed:
                self._prime()
                self._primed = True

    def new_result(self, awb: str) -> TrackResult:
        return TrackResult(awb=awb, partner=self.name)

    def fetch(self, awb: str) -> TrackResult:
        awb = str(awb).strip()
        try:
            self.ensure_primed()
            return self._fetch(awb).finalize()
        except Exception as exc:
            result = self.new_result(awb)
            result.ok = False
            result.status = NOT_FOUND if "not found" in str(exc).lower() else result.status
            result.error = f"{type(exc).__name__}: {exc}"
            return result.finalize()

    def fetch_many(self, awbs: Iterable[str]) -> list[TrackResult]:
        """Default: one request per AWB. Overridden where batching is supported."""
        return [self.fetch(a) for a in awbs]

    def base_headers(self, referer: str = "", origin: str = "") -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": UA,
        }
        if referer:
            headers["Referer"] = referer
        if origin:
            headers["Origin"] = origin
        return headers
