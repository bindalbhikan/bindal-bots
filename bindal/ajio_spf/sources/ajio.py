"""AJIO Seller Central client: login, returns (RTV) report, SPF tickets.

Login is the Reliance seller OAuth form (sellers.relianceretail.com): plain
username + password, no OTP. The portal home must be opened first, otherwise
the OAuth callback answers HTTP 500. The session lives in the `bff_session`
cookie on seller.ajio.com.

Ticket and return-order calls mirror the Quiry Suite browser tools
(ajio-ticket-followup.js, ajio-ticket-raise.js).
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import urljoin

import requests

log = logging.getLogger(__name__)

BASE = "https://seller.ajio.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
POB_IDS = ("DV00348890", "DV00316239")
STORE_NAMES = {"DV00348890": "Fashfun - 394210", "DV00316239": "FASHFUN CLOTHING - 394210"}
CONTACT_NO = "9898896895"
ASSIGNMENT_GROUP = "Primary_B2C"


class AjioError(RuntimeError):
    pass


class AjioClient:
    def __init__(self, username: str, password: str, timeout: int = 120):
        self.username, self.password, self.timeout = username, password, timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        self.seller_id = ""

    # ------------------------------------------------------------ auth
    def login(self, attempts: int = 3) -> None:
        for attempt in range(1, attempts + 1):
            try:
                return self._login()
            except (AjioError, requests.RequestException) as exc:
                if attempt == attempts:
                    raise
                log.warning("AJIO login attempt %d failed: %s - retrying", attempt, exc)
                self.s.cookies.clear()
                time.sleep(5 * attempt)

    def _login(self) -> None:
        self.s.get(f"{BASE}/vmsui/", timeout=self.timeout)
        r = self.s.get(f"{BASE}/malekith/auth/login?returnTo=https%3A%2F%2Fseller.ajio.com%2Fvmsui%2F",
                       timeout=self.timeout)
        form = re.search(r'<form id="loginForm".*?</form>', r.text, re.S)
        if not form:
            raise AjioError(f"AJIO login page not found (landed on {r.url.split('?')[0]})")
        action = urljoin(r.url, re.search(r'action="([^"]+)"', form.group(0)).group(1))
        data = dict(re.findall(r'<input type="hidden" name="([^"]+)"\s+value="([^"]*)"', form.group(0)))
        data.update(username=self.username, password=self.password)
        # Redirects are followed by hand: requests would carry the Reliance
        # Origin header on to the AJIO callback, which then answers HTTP 500.
        r2 = self.s.post(action, data=data, timeout=self.timeout, allow_redirects=False,
                         headers={"Referer": r.url, "Origin": "https://sellers.relianceretail.com"})
        callback = r2.headers.get("location", "")
        if r2.status_code not in (301, 302, 303) or "seller.ajio.com" not in callback:
            txt = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<[^>]+>", " ", r2.text, flags=re.S))
            raise AjioError(f"AJIO login rejected: {txt[:200]}")
        r3 = self.s.get(callback, timeout=self.timeout, allow_redirects=False,
                        headers={"Referer": "https://sellers.relianceretail.com/"})
        if r3.status_code >= 400:
            raise AjioError(f"AJIO login callback answered HTTP {r3.status_code}")
        self.s.get(f"{BASE}/vmsui/?_uap_callback=1", timeout=self.timeout)
        me = self.s.get(f"{BASE}/malekith/auth/me", timeout=self.timeout,
                        headers={"X-Tenant": "AJIO", "Accept": "application/json"}).json()
        if not me.get("authenticated"):
            raise AjioError("AJIO login did not create a session (auth/me says not authenticated)")
        self.seller_id = me["data"]["id"]
        log.info("Logged in to AJIO Seller Central")

    @property
    def castellan(self) -> dict:
        return {"Accept": "application/json, text/plain, */*", "X-Tenant": "AJIO",
                "X-Tenant-Id": "AJIO", "x-seller-id": self.seller_id}

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        for attempt in range(1, 4):
            try:
                r = self.s.request(method, BASE + path, timeout=self.timeout, **kw)
                if r.status_code < 500 or attempt == 3:
                    return r
                log.warning("%s %s -> HTTP %s (attempt %d)", method, path[:80], r.status_code, attempt)
            except requests.RequestException as exc:
                if attempt == 3:
                    raise
                log.warning("%s %s failed: %s (attempt %d)", method, path[:80], exc, attempt)
            time.sleep(3 * attempt)
        raise AssertionError("unreachable")

    # ------------------------------------------------------------ returns report
    def rtv_reports(self, start: date, end: date, days: int = 30):
        """(from, to, xlsx bytes) per window; the portal allows at most 31 days per download."""
        cursor = start
        while cursor <= end:
            stop = min(cursor + timedelta(days=days - 1), end)
            blob = self.rtv_report(cursor, stop)
            if blob:
                yield cursor, stop, blob
            cursor = stop + timedelta(days=1)

    def rtv_report(self, start: date, end: date) -> bytes:
        """The Dropship RTV report (xlsx) for both POBs, by return created date (max 31 days)."""
        r = self._req("POST", f"/malekith/vision/v1/dropship/rtv/report/download?from={start}&to={end}",
                      data="pobIds=" + "%2C".join(POB_IDS),
                      headers={"Accept": "application/json, text/plain, */*", "X-Tenant": "AJIO",
                               "x-pob-id": POB_IDS[0], "businessModel": "B2C",
                               "Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code == 404 and "No records found" in r.text:
            return b""          # a window with no returns yet (e.g. just after midnight)
        if r.status_code != 200 or "spreadsheet" not in r.headers.get("content-type", ""):
            raise AjioError(f"RTV report download failed: HTTP {r.status_code} {r.text[:200]}")
        return r.content

    def order_report(self, start: date, end: date) -> bytes:
        """The Dropship order report (xlsx, "DropShipOrderReports") for both POBs (max 31 days)."""
        r = self._req("POST", f"/malekith/vision/v1/dropship/order/report/download?from={start}&to={end}",
                      data="pobIds=" + "%2C".join(POB_IDS),
                      headers={"Accept": "application/json, text/plain, */*", "X-Tenant": "AJIO",
                               "x-pob-id": POB_IDS[0], "businessModel": "B2C",
                               "Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200 or "spreadsheet" not in r.headers.get("content-type", ""):
            raise AjioError(f"Order report download failed: HTTP {r.status_code} {r.text[:200]}")
        return r.content

    # ------------------------------------------------------------ tickets
    def ticket_list(self, start: date, end: date, size: int = 200) -> list[dict]:
        out, page, pages = [], 0, 1
        rng = f"{start:%Y/%m/%d}_{end:%Y/%m/%d}"
        while page < pages:
            r = self._req("GET", "/malekith/castellan/api/v1/tickets",
                          params={"page": page, "size": size, "sort": "desc", "ticketDateRange": rng},
                          headers=self.castellan)
            if r.status_code != 200:
                raise AjioError(f"Ticket list failed: HTTP {r.status_code} {r.text[:200]}")
            j = r.json()
            out += j.get("result") or []
            pages = (j.get("page") or {}).get("totalPages") or 1
            page += 1
        return out

    def ticket_detail(self, ticket_id: str) -> dict | None:
        r = self._req("GET", f"/malekith/castellan/api/v1/tickets/{ticket_id}", headers=self.castellan)
        if r.status_code != 200:
            log.warning("Ticket %s detail: HTTP %s", ticket_id, r.status_code)
            return None
        return (r.json() or {}).get("result")

    def ticket_details(self, ids: list[str], workers: int = 4) -> list[dict]:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return [t for t in pool.map(self.ticket_detail, ids) if t]

    def comment(self, ticket_id: str, text: str) -> dict:
        """Post a comment. On a closed / resolved ticket this also re-opens it."""
        r = self._req("PUT", f"/malekith/castellan/api/v1/tickets/{ticket_id}/comment",
                      json={"comment": text}, headers=self.castellan)
        if r.status_code not in (200, 201):
            raise AjioError(f"Comment on {ticket_id} failed: HTTP {r.status_code} {r.text[:200]}")
        return (r.json() or {}).get("result") or {}

    def tickets_for_return(self, return_order: str) -> list[dict]:
        """Tickets already open for this return. 404 = none. Any other answer (session expired,
        server error) raises, so a ticket is never raised twice just because the check failed."""
        r = self._req("GET", f"/malekith/castellan/api/v1/tickets/return-order/{return_order}",
                      headers=self.castellan)
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            raise AjioError(f"Ticket check for {return_order} failed: HTTP {r.status_code} {r.text[:200]}")
        result = (r.json() or {}).get("result")
        if isinstance(result, list):
            return result or [{"ticketId": "?"}]
        return [result] if isinstance(result, dict) and result else [{"ticketId": "?"}]

    def return_order(self, return_order: str, pob_id: str) -> dict | None:
        r = self._req("GET", f"/malekith/gamora/vms/v1/return-orders/{return_order}",
                      headers={"Accept": "application/json, text/plain, */*", "X-Tenant": "AJIO",
                               "x-pob-id": pob_id, "x-integration-type": "VMS_OMSGURU"})
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("result")

    def create_spf(self, payload: dict) -> str:
        r = self._req("POST", "/malekith/castellan/api/v1/tickets/spf", json=payload, headers=self.castellan)
        if r.status_code not in (200, 201):
            raise AjioError(f"SPF ticket create failed: HTTP {r.status_code} {r.text[:300]}")
        return str(((r.json() or {}).get("result") or {}).get("ticketId") or "?")


def spf_payload(ro: dict, item: dict, sub_issue: str, description: str, pob_id: str, email: str) -> dict:
    """Same body the portal (and ajio-ticket-raise.js) sends for one return item."""
    product = item.get("product") or {}
    return {
        "assignmentGroup": ASSIGNMENT_GROUP,
        "description": description,
        "issueTitle": f"Claiming SPF | Return Order ID: {ro.get('returnOrderId')}",
        "subIssueType": sub_issue,
        "orderId": ro.get("orderId"),
        "priority": "CRITICAL",
        "sellerContactNo": CONTACT_NO,
        "sellerEmail": email,
        "storeName": STORE_NAMES.get(pob_id, ""),
        "issueType": "RETURNS",
        "pobId": pob_id,
        "fulfilmentType": "DROPSHIP",
        "tenant": "AJIO",
        "ticketType": "SPF",
        "orderDetail": {
            "customerOrderId": ro.get("salesOrderId"),
            "forwardPONumber": ro.get("forwardPONumber"),
            "forwardShipmentId": ro.get("forwardShipmentId"),
            "forwardInvoiceNumber": ro.get("forwardInvoiceNumber"),
            "forwardAwbNumber": ro.get("forwardAwbNumber"),
            "forwardInvoiceAmount": item.get("fwdInvoiceBasePrice"),
            "returnOrderId": ro.get("returnOrderId"),
            "returnAwbNumber": ro.get("trackingNumber"),
            "returnShipmentId": ro.get("returnShipmentId"),
            "productDescription": product.get("productDescription"),
            "jioCode": product.get("itemCode"),
            "ean": product.get("eanCode"),
            "skuId": product.get("sellerSkuId"),
            "quantity": item.get("returnQuantity"),
            "returnCarrier": ro.get("returnCarrier"),
            "forwardCarrier": ro.get("forwardCarrier"),
            "returnType": ro.get("returnType"),
            "returnCreationDate": ro.get("returnDate"),
            "returnItemId": item.get("returnItemId"),
        },
    }
