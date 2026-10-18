"""Myntra orders from OMSGuru: list rows (SLA date, AWB, courier) and the original
Myntra AWB for orders shipped by Shadowfax / Delhivery.

VMS scans the Myntra tracking number (MYSP..., MYEC..., MYSC..., MYEP...). For most
orders that is the AWB OMSGuru shows, but when Myntra hands the order to Shadowfax
or Delhivery OMSGuru shows the courier's AWB instead. The Myntra number is then
only printed as a Code 128 barcode on the shipping label PDF, so the label is
rendered and its barcodes are decoded (same approach as omsguru-invoice-parser.js).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
from bs4 import BeautifulSoup

from ..portals.omsguru import BASE_URL, VIEW_ID, OmsGuruClient, item_status, text_of

log = logging.getLogger(__name__)

MYNTRA_AWB = re.compile(r"^MY[A-Z]{2}\d{6,}$")
SLA_RE = re.compile(r"(\w+ \d{1,2}, \d{4} \d{1,2}:\d{2}:\d{2} [AP]M)")
PDF_LOCK = threading.Lock()  # pdfium is not thread-safe: downloads run in parallel, rendering does not
ROW_COLS = ["Order Id", "Channel Id", "Status", "Courier", "AWB", "SLA"]


def list_rows(client: OmsGuruClient, start: datetime, end: datetime, channel_ids: list[str],
              max_pages: int = 80) -> pd.DataFrame:
    """One row per order in the order-date window, every warehouse, read from the list
    pages only (no order page is opened)."""
    rows: dict[str, dict] = {}
    for wh in list(client.warehouses()) or [None]:
        if wh:
            client.set_warehouse(wh)
        flt = client.order_filter(start, end, channel_ids)
        for page in range(1, max_pages + 1):
            url = client.list_page_url(flt, page)
            soup = BeautifulSoup(client.get(url).text, "html.parser")
            table = soup.select_one("#order-table")
            if table is None:
                raise RuntimeError(f"Orders table not found on {url}")
            added = 0
            for tr in table.select("tbody > tr"):
                tds = tr.find_all("td", recursive=False)
                link = tr.select_one('a[href*="/orders/view/"]')
                if not link or len(tds) < 7:
                    continue
                oid = VIEW_ID.search(link["href"]).group(1)
                if oid in rows:
                    continue
                ch = tr.select_one('a[href*="channel_companies/edit/"]')
                ship = text_of(tds[5])
                sla = SLA_RE.search(text_of(tds[6]))
                rows[oid] = {
                    "Order Id": oid,
                    "Channel Id": re.search(r"edit/(\d+)", ch["href"]).group(1) if ch else "",
                    "Status": item_status(text_of(tds[4])),
                    "Courier": (re.search(r"Courier\s+(.*?)\s+AWB", ship) or [None, ""])[1],
                    "AWB": (re.search(r"AWB\s+(\S+)", ship) or [None, ""])[1],
                    "SLA": sla.group(1) if sla else "",
                }
                added += 1
            log.info("Warehouse %s list page %d: +%d orders (total %d)", wh or "current", page, added, len(rows))
            if added == 0 or not soup.select_one(f'a[href*="page:{page + 1}"]'):
                break
            time.sleep(client.delay)
    df = pd.DataFrame(list(rows.values()), columns=ROW_COLS)
    df["SLA Date"] = pd.to_datetime(df["SLA"], format="%B %d, %Y %I:%M:%S %p", errors="coerce").dt.normalize()
    return df


def _label_barcodes(client: OmsGuruClient, order_id: str) -> list[str]:
    import pypdfium2 as pdfium
    import zxingcpp

    soup = BeautifulSoup(client.get(f"{BASE_URL}/orders/view/{order_id}").text, "html.parser")
    hrefs = [a["href"] for a in soup.select('a[href*="/orders/download/"]')
             if re.fullmatch(r"(shipping|shipment|courier)\s*label", text_of(a), re.I)]
    if not hrefs:
        raise RuntimeError("no shipping label link on the order page")
    url = hrefs[0] if hrefs[0].startswith("http") else BASE_URL + hrefs[0]
    pdf = client.s.get(url, timeout=client.timeout).content
    if b"%PDF" not in pdf[:1024]:
        raise RuntimeError("shipping label link did not return a PDF")
    texts = []
    with PDF_LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            for page in doc:
                for scale in (200 / 72, 300 / 72):  # higher resolution only if the first pass finds nothing
                    found = [r.text for r in zxingcpp.read_barcodes(page.render(scale=scale).to_pil())]
                    if found:
                        texts += found
                        break
        finally:
            doc.close()
    return texts


def myntra_awbs(client: OmsGuruClient, order_ids: list[str], workers: int = 4) -> tuple[dict[str, str], list[str]]:
    """Order id -> Myntra AWB decoded from the shipping label."""
    found, errors = {}, []

    def one(oid):
        try:
            codes = _label_barcodes(client, oid)
            return oid, next((c for c in codes if MYNTRA_AWB.match(c)), ""), None
        except Exception as exc:  # one bad label must not stop the report
            return oid, "", str(exc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for oid, awb, err in pool.map(one, order_ids):
            if awb:
                found[oid] = awb
            else:
                errors.append(f"label {oid}: {err or 'no Myntra AWB barcode found'}")
    log.info("Labels read: %d, Myntra AWB found for %d", len(order_ids), len(found))
    return found, errors
