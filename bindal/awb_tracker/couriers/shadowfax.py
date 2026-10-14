"""Shadowfax -- saruman API behind a static ``Authorization: Token`` header.

The token is a public constant baked into the tracker's Angular bundle
(``sfxTrackStaticToken``). It is scraped from the live bundle on first use so
the tool keeps working when Shadowfax rotates it; the last known value is the
fallback, and ``config.json`` can override both.
"""
from __future__ import annotations

import re

from ..base import BaseCourier
from ..models import (FORWARD, NOT_FOUND, RETURN, TrackEvent, TrackResult,
                      classify, to_iso)

TRACKER = "https://tracker.shadowfax.in"
API = "https://saruman.shadowfax.in"
FALLBACK_TOKEN = "cePcVR7z7FIETB4PxguHC2YJGk6NncHnByrJttgRIUqNxfWezuzAUvtALyqcHJEC"

MAIN_JS_RE = re.compile(r'(?:src|href)="(main\.[0-9a-f]+\.js)"')
TOKEN_RE = re.compile(r'sfxTrackStaticToken:"([^"]+)"')


class Shadowfax(BaseCourier):
    name = "SHADOWFAX"
    aliases = ("SFX", "SHADOW FAX")
    min_interval = 0.5   # saruman returns 429 above roughly 2 req/s

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._token = self.config.get("token") or ""

    def _prime(self) -> None:
        if self._token:
            return
        try:
            home = self.client.get(TRACKER + "/",
                                   headers={**self.base_headers(),
                                            "Accept": "text/html,application/xhtml+xml"})
            match = MAIN_JS_RE.search(home.text)
            if match:
                bundle = self.client.get(f"{TRACKER}/{match.group(1)}",
                                         headers=self.base_headers(referer=TRACKER + "/"))
                found = TOKEN_RE.search(bundle.text)
                if found:
                    self._token = found.group(1)
        except Exception:
            pass  # fall through to the known-good constant
        self._token = self._token or FALLBACK_TOKEN

    def _headers(self) -> dict[str, str]:
        return {**self.base_headers(referer=TRACKER + "/", origin=TRACKER),
                "Authorization": f"Token {self._token}"}

    def _fetch(self, awb: str) -> TrackResult:
        res = self.new_result(awb)
        headers = self._headers()

        resp = self.client.get(f"{API}/web_app/delivery/track/{awb}/", headers=headers)
        if resp.status_code == 401:
            # Token rotated: re-scrape once, then retry.
            self._token = ""
            self._primed = False
            self.ensure_primed()
            resp = self.client.get(f"{API}/web_app/delivery/track/{awb}/",
                                   headers=self._headers())
        if resp.status_code != 200:
            res.error = f"HTTP {resp.status_code} {resp.text[:120]}".strip()
            if resp.status_code in (400, 404) and "exist" in resp.text.lower():
                res.status = NOT_FOUND   # not manifested yet (or another courier's AWB)
            return res

        payload = resp.json()
        res.raw = payload
        details = payload.get("order_details") or {}
        stages = payload.get("data") or []

        if not details and not stages:
            res.status = NOT_FOUND
            res.error = payload.get("message") or "no tracking data"
            return res

        res.ok = True
        # Shadowfax labels the leg outright as "Fwd" or "Rev".
        leg = str(details.get("type") or payload.get("type") or "").lower()
        if leg.startswith("rev"):
            res.flow = RETURN
        elif leg.startswith("fwd"):
            res.flow = FORWARD

        res.status_raw = details.get("final_status") or details.get("status_id") or ""
        res.status_detail = details.get("remarks") or ""
        res.expected_delivery = to_iso(details.get("exp_delivery_date") or "")
        res.reference_no = details.get("order_id") or ""

        # Each stage pairs a main_status with parallel sub_status/time lists.
        for stage in stages:
            main = stage.get("main_status") or ""
            subs = stage.get("sub_status") or []
            times = stage.get("time") or []
            if not subs:
                res.events.append(TrackEvent(timestamp=to_iso(times[0] if times else ""),
                                             status=main))
                continue
            for i, sub in enumerate(subs):
                res.events.append(TrackEvent(
                    timestamp=to_iso(times[i] if i < len(times) else ""),
                    status=main,
                    remark=sub,
                ))

        if not stages and not res.status_raw:
            # AWB is recognised but carries no scans -- usually an old or
            # not-yet-manifested reverse pickup. Report it as a miss, not a win.
            res.ok = False
            res.status = NOT_FOUND
            res.error = "shipment found but no scan history available"
            return res

        res.status = classify(res.status_raw, res.status_detail)
        return res
