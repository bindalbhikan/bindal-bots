"""OMSGuru order lookup for returns the master still shows as not received.

Same approach as the Quiry Suite tool "Order lookup to Excel"
(omsguru-order-export.js): search an identifier, open the order page, read the
item status and the status history. Only the returns still open are looked
up, so there is no bulk export to wait for (the Return Logs export is capped
at 90 days and arrives later through notifications).

A found receipt is written as a row of the old "AJIO RETURN SHEET OMS GURU"
layout, which the master script already reads.
"""
from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from bs4 import BeautifulSoup

from ...core.omsguru import BASE_URL, OmsGuruClient, OmsGuruError, item_status, text_of

log = logging.getLogger(__name__)


def same_sku(report_sku: str, oms_sku: str) -> bool:
    """The report adds a brand prefix (LEA-/LA-/FSH-/MUR-...) that OMSGuru does not have."""
    a, b = (report_sku or "").upper(), (oms_sku or "").upper()
    return bool(a and b) and (a == b or a.endswith("-" + b))


RETURN_LOG = re.compile(r"Return Log\s+(RT\d+)\s*\(\s*(\d{4}-\d{2}-\d{2})\s*\)")
RECEIVED_LABELS = ("Partial Cancelled Return Received", "Cancelled Return Received",
                   "Partial Return Received", "Return Received")
# Return-side labels shown in an item's return block, just before "Return Comments"
RETURN_LABEL = re.compile(r"(Partial Cancelled Return Received|Cancelled Return Received|Partial Return Received|"
                          r"Return Received|Return Rejected|Return Init|Cancel Init)\s+Return Comments")
TRACKER = re.compile(r"^\s*([A-Za-z0-9]+)\s*(?:\(\s*([^)]+?)\s*\))?")


def parse_order_page(html: str) -> tuple[list[dict], dict]:
    """([{sku, status, log, received_on}] per item, {forward_awb, carrier, latest}) of one order page.

    The item table shows the forward status ("Delivered"); the return block under
    each item carries the return status and, once the warehouse booked it in,
    "Return Log RT260459 ( 2026-10-05)". That Return Log line is what proves a
    receipt, so an item counts as received only when it has one. The order's
    "Shipment Tracker" is the forward AWB.
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one("#ClientIndexForm"):
        raise OmsGuruError("Got the login page instead of the order page")
    info = {}
    for dt in (soup.select_one("#tab-details") or soup).find_all("dt"):
        dd = dt.find_next_sibling("dd")
        info.setdefault(text_of(dt).rstrip(":").strip().lower(), text_of(dd))
    tracker = TRACKER.match(info.get("shipment tracker", "") or "")
    latest = ""
    for table in soup.find_all("table"):
        first = table.find("tr")
        head = [text_of(c).lower() for c in first.find_all(["th", "td"], recursive=False)] if first else []
        if head[:2] == ["status", "timestamp"]:
            row = table.find_all("tr")[1:2]
            if row:
                latest = text_of(row[0].find(["td", "th"]))
            break
    order = {"forward_awb": tracker.group(1) if tracker else "",
             "carrier": (tracker.group(2) if tracker and tracker.group(2) else info.get("shipping company", "")),
             "latest": latest}

    items_box = soup.select_one("#order-items") or soup
    text = re.sub(r"\s+", " ", items_box.get_text(" ", strip=True))
    items = []
    for seg in re.split(r"Qty:\s*\d+\s+", text)[1:]:
        sku = seg.split(" - ")[0].strip().split(" ")[0]
        log_hit = RETURN_LOG.search(seg)
        ret = RETURN_LABEL.search(seg)
        items.append({
            "sku": sku,
            "status": ret.group(1) if ret else item_status(seg[:300]),
            "log": log_hit.group(1) if log_hit else "",
            "received_on": datetime.strptime(log_hit.group(2), "%Y-%m-%d") if log_hit else None,
        })
    return items, order


class ReturnLookup:
    """Return lookups on the shared OMSGuru client; order pages are read once per run."""

    def __init__(self, client: OmsGuruClient):
        self.client = client
        self.workers = client.workers
        self.pages: dict[str, tuple] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "ReturnLookup":
        client = OmsGuruClient.from_env()
        client.login()
        return cls(client)

    def search(self, term: str, kind: str = "AWB") -> list[str]:
        return self.client.search(term, kind)

    def order(self, order_id: str) -> tuple[list[dict], dict]:
        with self._lock:
            hit = self.pages.get(order_id)
        if hit is None:
            hit = parse_order_page(self.client.get(f"{BASE_URL}/orders/view/{order_id}").text)
            with self._lock:
                self.pages[order_id] = hit
        return hit

    def lookup(self, ret: dict) -> dict | None:
        """What OMSGuru knows about one return, or None when no order matches.

        ret: {return_awb, forward_awb, order_id, sku, invoice}
        Returns {status, forward_awb, carrier, received_row}; received_row is a
        row of the return sheet when the warehouse booked the return in.
        """
        ids: list[str] = []
        for term, kind in ((ret.get("return_awb"), "AWB"), (ret.get("forward_awb"), "AWB"), (ret.get("order_id"), "order")):
            ids = self.search(str(term or ""), kind)
            if ids:
                break
        best = None
        for oid in ids:
            items, order = self.order(oid)
            for item in items:
                if not same_sku(ret.get("sku", ""), item["sku"]):
                    continue
                received = None
                if item["log"]:
                    status = item["status"] if item["status"] in RECEIVED_LABELS else "Return Received"
                    received = {
                        "Return Log ID": item["log"], "Channel Name": "AJIO (order lookup)",
                        "Invoice Number": ret.get("invoice"), "Order Number": ret.get("order_id"),
                        "Item SkuCode": item["sku"], "Return Tracker": ret.get("return_awb"),
                        "FWD Tracker": order["forward_awb"] or ret.get("forward_awb"), "Order Status": status,
                        "Received On": item["received_on"], "DATE": item["received_on"],
                    }
                result = {"status": (received or {}).get("Order Status") or item["status"] or order["latest"],
                          "forward_awb": order["forward_awb"], "carrier": order["carrier"], "received_row": received}
                if received:
                    return result
                best = best or result
        if best is None and ids:
            # Order found but no item with this SKU: still good for the forward AWB.
            _, order = self.order(ids[0])
            best = {"status": "", "forward_awb": order["forward_awb"], "carrier": order["carrier"], "received_row": None}
        return best

    def lookup_many(self, returns: list[dict]) -> tuple[list[tuple[dict, dict]], int]:
        """([(return, lookup result)] for every return OMSGuru knows, lookups that failed)."""
        found, failed = [], 0

        def one(ret):
            try:
                return ret, self.lookup(ret), None
            except Exception as exc:  # noqa: BLE001 - one bad lookup must not stop the rest
                return ret, None, exc

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for n, (ret, res, err) in enumerate(pool.map(one, returns), 1):
                if err is not None:
                    failed += 1
                    log.warning("OMSGuru lookup failed: %s", err)
                elif res:
                    found.append((ret, res))
                if n % 100 == 0:
                    log.info("OMSGuru lookups: %d/%d", n, len(returns))
        received = sum(1 for _, r in found if r["received_row"])
        log.info("OMSGuru: %d returns looked up, %d found, %d received, %d failed",
                 len(returns), len(found), received, failed)
        return found, failed
