"""Ekart -- JSON proxy behind a CSRF token scraped from the tracking page.

The token lives in a ``<meta name="csrf-token">`` tag and is bound to the
session cookie that served that page -- so it is fetched and cached *per
worker thread*, alongside that thread's cookie jar. Sharing one token across
threads makes Ekart answer HTTP 205 for every thread but the first.

``tracking_ids`` must be a JSON **array**. Passing a comma-joined string is
accepted with HTTP 200 and an empty ``{}`` body -- a silent failure -- so the
list form is used even for a single AWB. Batches of ~50 work; 100 makes the
upstream time out, so the chunk size is kept well under that.
"""
from __future__ import annotations

import re
import threading

from ..base import BaseCourier
from ..models import NOT_FOUND, TrackEvent, TrackResult, classify, to_iso

PAGE = "https://ekartlogistics.com/ekartlogistics-web"
API = ("https://ekartlogistics.com/ekartlogistics-web-routes-api/"
       "ekartlogistics-web-proxy/trackings/v2")
SITE = "https://ekartlogistics.com"
CSRF_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"')


class Ekart(BaseCourier):
    name = "EKART"
    aliases = ("EKART LOGISTICS", "FLIPKART", "EKL")
    min_interval = 0.3
    batch_size = 25          # 50 still works; 100 times out upstream

    #: Ekart answers with these when the token does not match the session.
    _STALE_TOKEN = (205, 401, 403, 419)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The CSRF token is bound to the *session cookie*, and HttpClient gives
        # every worker thread its own cookie jar -- so the token has to be
        # per-thread too. A shared one makes every thread but the first fail.
        self._local = threading.local()

    def _token(self, refresh: bool = False) -> str:
        token = getattr(self._local, "csrf", "")
        if token and not refresh:
            return token
        if refresh:
            self.client.reset_session()
        resp = self.client.get(PAGE, headers={**self.base_headers(referer=SITE),
                                              "Accept": "text/html,application/xhtml+xml"})
        match = CSRF_RE.search(resp.text)
        if not match:
            raise RuntimeError("could not read csrf-token from Ekart page")
        self._local.csrf = match.group(1)
        return self._local.csrf

    def _api_headers(self, awb: str, token: str) -> dict[str, str]:
        return {
            **self.base_headers(referer=f"{PAGE}/shipmenttrack/{awb}", origin=SITE),
            "content-type": "application/json",
            "csrf-token": token,
            "x-user-agent": self.base_headers()["User-Agent"] + " EKCL/website/1",
        }

    def _call(self, ids: list[str]) -> dict:
        ids = list(ids)
        resp = self.client.post(API, json={"tracking_ids": ids},
                                headers=self._api_headers(ids[0], self._token()))
        if resp.status_code in self._STALE_TOKEN:
            # Fresh cookie jar + fresh token for this thread, then one retry.
            resp = self.client.post(API, json={"tracking_ids": ids},
                                    headers=self._api_headers(ids[0],
                                                              self._token(refresh=True)))
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.json() or {}

    def _build(self, awb: str, item: dict | None) -> TrackResult:
        res = self.new_result(awb)
        if not item:
            res.status = NOT_FOUND
            res.error = "no tracking data"
            return res

        res.raw = item
        res.ok = True
        res.origin = item.get("sourceCity") or ""
        res.destination = item.get("destinationCity") or ""
        res.expected_delivery = to_iso(item.get("expectedDeliveryDate"))

        for scan in item.get("shipmentTrackingDetails") or []:
            res.events.append(TrackEvent(
                timestamp=to_iso(scan.get("date")),
                status=scan.get("statusDetails") or "",
                location=scan.get("city") or "",
            ))

        if res.events:
            res.status_raw = max(res.events, key=lambda e: e.timestamp or "").status
        res.status = classify(res.status_raw)
        return res

    def _fetch(self, awb: str) -> TrackResult:
        return self._build(awb, (self._call([awb]) or {}).get(awb))

    def fetch_many(self, awbs):
        awbs = [str(a).strip() for a in awbs]
        results: list[TrackResult] = []
        for start in range(0, len(awbs), self.batch_size):
            chunk = awbs[start:start + self.batch_size]
            try:
                payload = self._call(chunk)
            except Exception as exc:
                for awb in chunk:
                    res = self.new_result(awb)
                    res.error = f"{type(exc).__name__}: {exc}"
                    results.append(res.finalize())
                continue
            for awb in chunk:
                results.append(self._build(awb, payload.get(awb)).finalize())
        return results
