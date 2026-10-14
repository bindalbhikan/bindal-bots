"""VMS (fkvms.synlabs.io) client: login and the AWBs that have a packing scan."""
from __future__ import annotations

import base64
import logging
import time
from datetime import date

import requests

log = logging.getLogger(__name__)

BASE = "https://fkvms.synlabs.io"
PAGE_SIZE = 500


class VmsClient:
    def __init__(self, username: str, password: str, timeout: int = 60):
        self.username, self.password, self.timeout = username, password, timeout
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json, text/plain, */*"})

    def login(self) -> None:
        # the web app sends the password base64-encoded
        body = {"username": self.username,
                "password": base64.b64encode(self.password.encode("utf-8")).decode("ascii")}
        r = self.s.post(f"{BASE}/login", json=body, timeout=self.timeout)
        try:
            token = r.json().get("token") if r.ok else None
        except ValueError:
            token = None
        if not token:
            raise RuntimeError(f"VMS login failed: HTTP {r.status_code} {r.text[:200]}")
        self.s.headers["Authorization"] = f"Bearer {token}"
        log.info("Logged in to VMS as %s", self.username)

    def _scans_page(self, start: date, end: date, page: int) -> dict:
        body = {"page": page, "pageSize": PAGE_SIZE, "active": True, "orgId": None, "sellerId": None,
                "feedId": None, "fromDate": f"{start:%Y-%m-%d}", "fromTime": "00:00:00",
                "toDate": f"{end:%Y-%m-%d}", "toTime": "23:59:59", "awb": None, "scanType": None}
        problem = ""
        for attempt in range(1, 4):
            try:
                r = self.s.post(f"{BASE}/api/awb/scans", json=body, timeout=self.timeout)
                if r.ok:
                    return r.json()
                problem = f"HTTP {r.status_code} {r.text[:200]}"
            except (requests.RequestException, ValueError) as exc:   # network error / not JSON
                problem = str(exc)
            log.warning("VMS scans page %d: %s (attempt %d)", page, problem, attempt)
            time.sleep(2 * attempt)
        raise RuntimeError(f"VMS scans failed: {problem}")

    def scanned_awbs(self, start: date, end: date) -> set[str]:
        """Every AWB (and linked tracking id) with a scan between start and end."""
        awbs: set[str] = set()
        page, total_pages = 0, 1
        while page < total_pages:
            data = self._scans_page(start, end, page)
            for scan in data.get("list") or []:
                if scan.get("awb"):
                    awbs.add(str(scan["awb"]).strip())
                awbs.update(str(x).strip() for x in (scan.get("linkedTrackingIds") or []))
            total_pages = int(data.get("totalPages") or 1)
            page += 1
        log.info("VMS scans %s -> %s: %d AWBs", start, end, len(awbs))
        return awbs
