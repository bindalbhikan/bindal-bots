"""Flipkart Seller Hub: FBF (Flipkart warehouse) units per day.

Login is a plain JSON POST; the CSRF token for the dashboard APIs comes from
/getFeaturesForSeller. Units come from the Seller Insights metrics API filtered on
Flipkart's own warehouses (location_id), so only FBF orders are counted.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date

import requests

log = logging.getLogger(__name__)

BASE = "https://seller.flipkart.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
DEFAULT_FBF_LOCATIONS = ["bhi_vas_wh_nl_01nl", "frk_bts", "gur_san_wh_nl_01nl", "malur_bts", "ulub_bts"]


class FlipkartError(RuntimeError):
    pass


class FlipkartClient:
    def __init__(self, username: str, password: str, timeout: int = 60):
        self.username, self.password, self.timeout = username, password, timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self.csrf = ""
        self.seller_id = ""

    def login(self) -> None:
        xhr = {"Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"}
        self.s.get(f"{BASE}/", timeout=self.timeout)
        r = self.s.post(f"{BASE}/login", headers={**xhr, "Content-Type": "application/json",
                                                   "Origin": BASE, "Referer": f"{BASE}/"},
                        json={"authName": "flipkart", "username": self.username,
                              "password": self.password, "userNameType": "email"},
                        timeout=self.timeout)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or body.get("code") != 1000:
            raise FlipkartError(f"Flipkart login failed: HTTP {r.status_code} {str(body)[:200] or r.text[:200]}")
        f = self.s.get(f"{BASE}/getFeaturesForSeller", headers=xhr, timeout=self.timeout).json()
        self.csrf, self.seller_id = f.get("csrfToken", ""), f.get("sellerId", "")
        if not self.csrf:
            raise FlipkartError("Flipkart CSRF token not found after login")
        log.info("Logged in to Flipkart Seller Hub (seller %s)", self.seller_id)

    def _metrics(self, body: dict, view: str) -> dict:
        r = self.s.post(f"{BASE}/napi/metrics/search", json=body, timeout=self.timeout,
                        headers={"fk-csrf-token": self.csrf, "x-view-name": view,
                                 "Content-Type": "application/json", "Origin": BASE,
                                 "Referer": f"{BASE}/index.html"})
        if r.status_code != 200:
            raise FlipkartError(f"Flipkart metrics API failed: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def fbf_units(self, days: list[date], locations: list[str] | None = None) -> tuple[Counter, Counter]:
        """(gross units per day, cancelled units per day) for the FBF warehouses.
        The API sums the whole requested range into one row, so each day is queried on its own."""
        locations = locations or DEFAULT_FBF_LOCATIONS
        gross, cancel = Counter(), Counter()
        for day in days:
            page, pages = 0, 1
            while page < pages:
                body = {
                    "filters": [
                        {"field_name": "date", "filter_type": "RANGE_MATCH",
                         "range_from": f"{day:%Y-%m-%d}", "range_to": f"{day:%Y-%m-%d}"},
                        {"field_name": "location_id", "filter_type": "FULL_TEXT_MATCH",
                         "text_match_filter_values": locations},
                    ],
                    "sort": [],
                    "metrics": [
                        {"metric_name": "gross_units", "metric_field": "gross_units", "metric_type": "longSum"},
                        {"metric_name": "cancellation_units", "metric_field": "cancellation_units",
                         "metric_type": "longSum"},
                    ],
                    "derived_metrics": [], "aggregate_by": "all", "interval": "DAILY",
                    "pivot": ["address_state_code"], "page_number": page, "page_size": 500,
                }
                data = self._metrics(body, "sell_more_aggregated_groupby_view")
                for row in data.get("data") or []:
                    gross[day] += int(row.get("gross_units") or 0)
                    cancel[day] += int(row.get("cancellation_units") or 0)
                pages = int(data.get("total_pages") or 1)
                page += 1
        log.info("Flipkart FBF: gross %s, cancel %s",
                 {f"{d}": gross[d] for d in days}, {f"{d}": cancel[d] for d in days})
        return gross, cancel
