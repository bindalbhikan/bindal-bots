"""Flipkart Seller Hub: FBF (Flipkart warehouse) units per day.

Login is a plain JSON POST; the CSRF token for the dashboard APIs comes from
/getFeaturesForSeller. Units come from the Seller Insights metrics API filtered on
Flipkart's own warehouses (location_id), so only FBF orders are counted.
"""
from __future__ import annotations

import logging
import time
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

    def login(self, tries: int = 3) -> None:
        """Log in; a dropped connection (Flipkart resets some, 18 Oct backfill) is tried again.
        A refused login is not retried."""
        for attempt in range(1, tries + 1):
            try:
                return self._login()
            except requests.ConnectionError as exc:
                if attempt == tries:
                    raise
                log.warning("Flipkart login: connection dropped (%s), trying again", exc)
                time.sleep(20 * attempt)

    def _login(self) -> None:
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

    # ------------------------------------------------------------ report centre
    def _report_api(self, method: str, path: str, **kw) -> requests.Response:
        r = self.s.request(method, f"{BASE}/napi/metrics/bizReport/{path}", timeout=self.timeout,
                           headers={"fk-csrf-token": self.csrf, "Content-Type": "application/json",
                                    "Origin": BASE, "Referer": f"{BASE}/index.html"}, **kw)
        if r.status_code != 200:
            raise FlipkartError(f"Flipkart report centre {path.split('?')[0]}: HTTP {r.status_code} {r.text[:200]}")
        return r

    def report_requests(self, pages: int = 3) -> list[dict]:
        """The report centre's one-time report requests, newest first."""
        out = []
        for page in range(1, pages + 1):
            data = self._report_api("POST", "report/getReportsV2", json={
                "reportGroup": None, "reportName": None, "enable": True, "status": None,
                "page_size": 50, "starting_page": page}).json()
            out += data.get("report_requests") or []
            if not data.get("has_more"):
                break
        return out

    @staticmethod
    def _dates(req: dict) -> list[str]:
        return next((f.get("value") for f in req.get("filters") or [] if f.get("name") == "date"), [])

    def request_report(self, name: str, start: date, end: date, fresh: bool = False) -> str:
        """Request id of a report centre report (e.g. "Sales Report", "Returns") for exactly
        start..end: a finished or running request for the same dates is reused (unless fresh),
        else one is submitted."""
        want = [f"{start}", f"{end}"]
        req = None if fresh else next((q for q in self.report_requests() if q.get("report_name") == name
                                       and self._dates(q) == want and q.get("status") not in ("FAILED", "ERROR")),
                                      None)
        if req:
            log.info("Flipkart %s %s..%s already requested (%s)", name, start, end, req.get("status"))
            return req["request_id"]
        body = {"from_date": want[0], "to_date": want[1], "file_format": "EXCEL", "type": "ONETIME",
                "filters": [{"values": want, "operator": "RANGE", "name": "date"}]}
        rid = self._report_api("POST", f"submitReport?reportName={requests.utils.quote(name)}",
                               json=body).json()["request_id"]["request_id"]
        log.info("Flipkart %s %s..%s requested (%s)", name, start, end, rid)
        return rid

    def report(self, name: str, start: date, end: date, wait_minutes: int = 45,
               poll_seconds: int = 30) -> tuple[str, bytes]:
        """(file name, xlsx bytes) of a report centre report for exactly start..end, waited for
        until it is stored (Flipkart says up to 30 minutes). When an existing (old) request will
        not download, a new one is submitted and waited for."""
        try:
            return self._report(name, start, end, self.request_report(name, start, end), wait_minutes, poll_seconds)
        except (FlipkartError, requests.RequestException) as exc:
            log.warning("Flipkart %s %s..%s not downloaded (%s): asking for it again", name, start, end, exc)
            rid = self.request_report(name, start, end, fresh=True)
            return self._report(name, start, end, rid, wait_minutes, poll_seconds)

    def _report(self, name, start, end, rid, wait_minutes, poll_seconds) -> tuple[str, bytes]:
        req = None
        deadline = time.time() + wait_minutes * 60
        while True:
            # an old request can sit beyond the first page of the list
            req = next((q for q in self.report_requests(1) if q.get("request_id") == rid), None) or \
                next((q for q in self.report_requests() if q.get("request_id") == rid), req)
            status = (req or {}).get("status")
            if status == "FILE_STORED":
                break
            if status in ("FAILED", "ERROR"):
                raise FlipkartError(f"Flipkart {name} report {rid} {status}")
            if time.time() > deadline:
                raise FlipkartError(f"Flipkart {name} report not ready after {wait_minutes} min ({status})")
            log.info("Flipkart %s report: %s, waiting", name, status or "?")
            time.sleep(poll_seconds)
        file_name = f"{rid}_{req['created_at']}.xlsx"
        r = self._report_api("GET", f"downloadReport/{file_name}?token={rid}&sellerId={self.seller_id}")
        log.info("Flipkart %s report downloaded (%d bytes)", name, len(r.content))
        return f"Flipkart {name} {start} to {end}.xlsx", r.content
