"""OMSGuru (client.omsguru.com) client shared by every bot.

  login / warehouses / set_warehouse   one session, warehouse by warehouse
  list_orders / fetch_window           AJIO TAT: orders of a date window + order pages
  get / order_filter                   sale + VMS bots read the list pages themselves
  search                               SPF bot: the search-box autocomplete (AWB / order id)

parse_order_page() gives the "Orders" and "Status History" rows of the
OMSGuru_All_Order_Data_*.xlsx export, so the TAT engine takes them unchanged.
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import requests
from bs4 import BeautifulSoup

PENDING_PREFIX = "PENDING-"  # key for orders without an invoice number yet

log = logging.getLogger(__name__)

BASE_URL = "https://client.omsguru.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
MAX_PAGES = 50                 # 300 orders per list page -> 15,000 orders max per window
VIEW_ID = re.compile(r"/orders/view/(\d+)")

# Item statuses OMSGuru uses (from the list page's status filter), longest first so
# "Cancelled Return Received - RTO" wins over "Cancelled".
ITEM_STATUSES = sorted([
    "Pending", "New", "Packed", "Ready to ship", "Shipped", "In Transit", "Delivered",
    "Return Received - RTV", "Return Init - RTV", "Cancel Init - RTO",
    "Cancelled Return Received - RTO", "Cancelled Before Shipping", "Removed Before Shipping",
    "Cancelled", "Processing", "Return Rejected", "Partial Return Received",
    "Partial Cancelled Return Received",
], key=len, reverse=True)

ORDER_COLS = ["Input Identifier", "Internal Order ID", "Channel Order ID", "Sub-Order Internal ID",
              "SKU Code", "Qty", "Item Status", "Order Date", "Invoice Date", "SLA Date",
              "Pack Log", "Dispatch Log", "AWB / Shipment Tracker", "Shipping Company",
              "Warehouse", "Payment Mode", "Channel"]
HIST_COLS = ["Internal Order ID", "Status", "Timestamp"]


class OmsGuruError(RuntimeError):
    pass


def text_of(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


class OmsGuruClient:
    def __init__(self, email: str, password: str, otp: str = "", workers: int = 4,
                 delay: float = 0.2, timeout: int = 60):
        self.email, self.password, self.otp = email, password, otp
        self.workers, self.delay, self.timeout = workers, delay, timeout
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        self.ajax: dict[str, str] = {}

    @classmethod
    def from_env(cls, **kw) -> "OmsGuruClient":
        from ..core.config import env
        client = cls(env("OMSGURU_EMAIL"), env("OMSGURU_PASSWORD"), env("OMSGURU_OTP"), **kw)
        if not client.email or not client.password:
            raise OmsGuruError("OMSGURU_EMAIL / OMSGURU_PASSWORD not set")
        return client

    # ------------------------------------------------------------ http
    def get(self, url: str, **kw) -> requests.Response:
        for attempt in range(1, 4):
            try:
                r = self.s.get(url, timeout=self.timeout, **kw)
                if r.status_code == 200:
                    if "/login" in r.url and "/login" not in url:
                        raise OmsGuruError("Session expired / not logged in (redirected to /login)")
                    return r
                log.warning("GET %s -> HTTP %s (attempt %d)", url[:120], r.status_code, attempt)
            except requests.RequestException as exc:
                log.warning("GET %s failed: %s (attempt %d)", url[:120], exc, attempt)
            time.sleep(2 * attempt)
        raise OmsGuruError(f"GET failed after retries: {url[:160]}")

    def login(self) -> None:
        self.get(f"{BASE_URL}/login")
        form = {"_method": "POST", "data[Client][email]": self.email,
                "data[Client][password]": self.password, "data[Client][otp]": self.otp or "",
                "data[Client][remember_me]": "1"}
        r = self.s.post(f"{BASE_URL}/login", data=form, timeout=self.timeout,
                        headers={"Referer": f"{BASE_URL}/login", "Origin": BASE_URL})
        if r.status_code != 200 or "/login" in r.url or 'id="ClientIndexForm"' in r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            msg = text_of(soup.select_one(".alert, .error-message, #flashMessage")) or "unknown reason"
            raise OmsGuruError(f"OMSGuru login failed: {msg}")
        token = re.search(r'name="csrf-token" content="([^"]+)"', r.text)
        self.ajax = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/javascript, */*"}
        if token:
            self.ajax["X-CSRF-Token"] = token.group(1)
        log.info("Logged in to OMSGuru as %s", self.email)

    # ------------------------------------------------------------ list
    @staticmethod
    def order_filter(start: datetime, end: datetime, channel_ids: list[str] | None = None) -> str:
        """The list page's ?filter= value: order date window, optionally only some channels."""
        f: dict = {"order_date": [start.strftime("%m/%d/%Y %I:%M %p"), end.strftime("%m/%d/%Y %I:%M %p")]}
        if channel_ids:
            f["channel_company_id"] = channel_ids
        return quote(json.dumps(f, separators=(",", ":")), safe="")


    @staticmethod
    def list_page_url(flt: str, page: int) -> str:
        return f"{BASE_URL}/orders?filter={flt}" if page == 1 else f"{BASE_URL}/orders/index/page:{page}?filter={flt}"

    # ------------------------------------------------------------ search
    def search(self, term: str, kind: str = "AWB") -> list[str]:
        """Internal order ids whose identifier of this kind matches (the search box autocomplete)."""
        if not term:
            return []
        r = self.get(f"{BASE_URL}/orders/general_search", params={"f": kind, "term": term}, headers=self.ajax)
        try:
            hits = r.json()
        except ValueError:
            return []
        hits = [h for h in hits if isinstance(h, dict) and h.get("id")] if isinstance(hits, list) else []
        if kind == "AWB":   # the autocomplete also returns near matches
            hits = [h for h in hits if str(h.get("label", "")).upper() == term.upper()]
        return [str(h["id"]) for h in hits]

    # ------------------------------------------------------------ warehouses
    def warehouses(self) -> dict[str, str]:
        """Warehouse id -> name. A fresh login must pick one before any order page opens.
        The order list only shows the current warehouse, so every one is visited."""
        soup = BeautifulSoup(self.get(f"{BASE_URL}/orders").text, "html.parser")
        sel = (soup.select_one("#forceWarehouseSelector")
               or soup.select_one('select[id*="arehouse" i], select[name*="arehouse" i]'))
        found = {o.get("value"): text_of(o) for o in (sel.find_all("option") if sel else [])
                 if re.fullmatch(r"\d+", o.get("value") or "")}
        log.info("Warehouses: %s", found or "none listed (using the current one)")
        return found

    def set_warehouse(self, warehouse_id: str) -> None:
        self.get(f"{BASE_URL}/orders/setCurWarehouse/{warehouse_id}")

    def fetch_window(self, start: datetime, end: datetime, channel_ids: list[str],
                     warehouse_ids: list[str] | None = None, skip_channel_orders: set[str] | None = None):
        """List every order in the window, warehouse by warehouse, and open the order page
        only for orders not in skip_channel_orders (orders the master already has complete)."""
        whs = warehouse_ids or list(self.warehouses()) or [None]
        skip = skip_channel_orders or set()
        frames_o, frames_h, errors, seen = [], [], [], set()
        for wh in whs:
            if wh:
                self.set_warehouse(wh)
            listed = self.list_orders(start, end, channel_ids)
            ids = [i for i, co in listed.items() if i not in seen and co not in skip]
            seen.update(listed)
            log.info("Warehouse %s: %d AJIO orders listed, %d complete in master (skipped), %d to open",
                     wh or "current", len(listed), len(listed) - len(ids), len(ids))
            o, h, err = self.fetch_orders(ids)
            frames_o.append(o)
            frames_h.append(h)
            errors += err
        return (pd.concat(frames_o, ignore_index=True), pd.concat(frames_h, ignore_index=True), errors)

    def list_order_ids(self, start: datetime, end: datetime, channel_ids: list[str]) -> list[str]:
        return list(self.list_orders(start, end, channel_ids))

    def list_orders(self, start: datetime, end: datetime, channel_ids: list[str]) -> dict[str, str]:
        """Internal order id (orders/view/<id>) -> AJIO channel order id, for the window."""
        flt = self.order_filter(start, end, channel_ids)
        ids: dict[str, str] = {}
        for page in range(1, MAX_PAGES + 1):
            url = self.list_page_url(flt, page)
            soup = BeautifulSoup(self.get(url).text, "html.parser")
            table = soup.select_one("#order-table")
            if table is None:
                if soup.select_one("#forceWarehouseSelector"):
                    raise OmsGuruError("OMSGuru asks to select a warehouse first - call set_warehouse()")
                raise OmsGuruError(f"Orders table not found on {url} - page layout changed?")
            before = len(ids)
            for tr in table.select("tr"):
                a = tr.select_one('a[href*="/orders/view/"]')
                m = VIEW_ID.search(a["href"]) if a else None
                if not m:
                    continue
                co = re.search(r"OrderID\s+(\S+)", text_of(tr))
                ids.setdefault(m.group(1), co.group(1) if co else "")
            added = len(ids) - before
            has_next = soup.select_one(f'a[href*="page:{page + 1}"]') is not None
            log.info("List page %d: +%d orders (total %d)", page, added, len(ids))
            if added == 0 or not has_next:
                break
            time.sleep(self.delay)
        return ids

    # ------------------------------------------------------------ order page
    def fetch_order(self, order_id: str) -> tuple[list[dict], list[dict]]:
        html = self.get(f"{BASE_URL}/orders/view/{order_id}").text
        time.sleep(self.delay)
        return parse_order_page(html, order_id)

    def fetch_orders(self, order_ids: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
        orders, hist, errors = [], [], []

        def one(oid):
            try:
                return oid, self.fetch_order(oid), None
            except Exception as exc:  # one bad page must not kill the run
                return oid, None, str(exc)

        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            for n, (oid, res, err) in enumerate(pool.map(one, order_ids), 1):
                if err:
                    errors.append(f"{oid}: {err}")
                    log.warning("Order %s failed: %s", oid, err)
                else:
                    orders += res[0]
                    hist += res[1]
                if n % 50 == 0:
                    log.info("Fetched %d/%d order pages", n, len(order_ids))
        o = pd.DataFrame(orders, columns=ORDER_COLS)
        h = pd.DataFrame(hist, columns=HIST_COLS)
        return o, h, errors


# ---------------------------------------------------------------- parsing

def _dl_map(container) -> dict[str, str]:
    out: dict[str, str] = {}
    if container is None:
        return out
    for dt in container.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        key = text_of(dt).rstrip(":").strip().lower()
        if key and key not in out:
            out[key] = text_of(dd)
    return out


def item_status(text: str) -> str:
    for st in ITEM_STATUSES:
        if re.search(r"(?<![A-Za-z])" + re.escape(st) + r"(?![A-Za-z])", text):
            return st
    return ""


def parse_order_page(html: str, order_id: str) -> tuple[list[dict], list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("#ClientIndexForm"):
        raise OmsGuruError("Got the login page instead of the order page")
    details = soup.select_one("#tab-details") or soup
    info = {}
    for block in details.select(".row.separateBlock") or [details]:
        for k, v in _dl_map(block).items():
            info.setdefault(k, v)
    shipping_co = info.get("shipping company", "")
    pop = soup.select_one("#popover_shipment")
    if pop:
        shipping_co = _dl_map(pop).get("shipping company", shipping_co)
    tracker = info.get("shipment tracker", "")
    if not shipping_co:
        m = re.search(r"\(\s*([^)]+?)\s*\)", tracker)
        shipping_co = m.group(1) if m else ""

    invoice = info.get("invoice number", "")
    channel_order = info.get("order", "")
    key = invoice or f"{PENDING_PREFIX}{channel_order or order_id}"
    base = {
        "Input Identifier": key, "Internal Order ID": order_id, "Channel Order ID": channel_order,
        "Order Date": info.get("order date", ""), "Invoice Date": info.get("invoice date", ""),
        "SLA Date": info.get("sla date", ""), "Pack Log": info.get("pack log", ""),
        "Dispatch Log": info.get("dispatch log", ""), "AWB / Shipment Tracker": tracker,
        "Shipping Company": shipping_co, "Warehouse": info.get("warehouse", ""),
        "Payment Mode": info.get("payment mode", ""), "Channel": info.get("channel", ""),
    }

    items = []
    for tr in soup.select("#order-items tbody tr[data-id]"):
        cells = tr.find_all("td", recursive=False)
        row_text = text_of(tr)
        qty = re.search(r"Qty:?\s*(\d+)", row_text)
        sku_text = text_of(cells[1]) if len(cells) > 1 else ""
        items.append({**base,
                      "Sub-Order Internal ID": tr.get("data-id", ""),
                      "SKU Code": sku_text.split(" - ")[0].strip(),
                      "Qty": int(qty.group(1)) if qty else 1,
                      "Item Status": item_status(row_text)})
    if not items:
        items.append({**base, "Sub-Order Internal ID": order_id, "SKU Code": "", "Qty": 1, "Item Status": ""})

    hist, seen = [], set()
    for table in soup.find_all("table"):
        first = table.find("tr")
        if not first:
            continue
        head = [text_of(c).lower() for c in first.find_all(["th", "td"], recursive=False)]
        if head[:2] != ["status", "timestamp"]:
            continue
        for tr in table.find_all("tr")[1:]:
            c = [text_of(x) for x in tr.find_all(["td", "th"], recursive=False)]
            if len(c) >= 2 and c[0] and (c[0], c[1]) not in seen:
                seen.add((c[0], c[1]))
                hist.append({"Internal Order ID": order_id, "Status": c[0], "Timestamp": c[1]})
    return items, hist
