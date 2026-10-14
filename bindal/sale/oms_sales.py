"""OMSGuru sale quantities: GROSS units per order date and report block.

Read from the order list pages (every warehouse, every channel), which give the
same numbers as OMSGuru's order export: one row per sub-order line with its Qty.
Cancelled orders stay in GROSS, as in the original main.py.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from ..core.omsguru import BASE_URL, OmsGuruClient, text_of

log = logging.getLogger(__name__)

# The first matching keyword decides the destination block of the sale report.
# Cocoblu (an Amazon seller channel) has its own block since Oct 2026; Snapdeal is not reported.
IGNORE = "-"
CHANNEL_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cocoblu",), "COCOBLU"),
    (("amazon",), "AZ/FX/FBA"),
    (("meesho",), "MEESHO"),
    (("ajio",), "AJIO B2C"),
    (("flipkart",), "FLIPKART"),
    (("myntra",), "MYNTRA"),
    (("nykaa",), "NYKAA"),
    (("snapdeal",), IGNORE),
)
OMS_BLOCKS = sorted({block for _, block in CHANNEL_RULES} - {IGNORE})
ORDER_DATE_RE = re.compile(r"Order Date\s+(\d{2}/\w{3}/\d{4} \d{2}:\d{2} [AP]M)")


def block_for(channel_name: str) -> str | None:
    name = channel_name.casefold()
    for keywords, block in CHANNEL_RULES:
        if any(k in name for k in keywords):
            return block
    return None


def channel_names(client: OmsGuruClient) -> dict[str, str]:
    html = client.get(f"{BASE_URL}/orders").text
    m = re.search(r'"channel_company_id":\{"type":"preFilled".*?"dataOptions":(\{.*?\})', html)
    if not m:
        raise RuntimeError("OMSGuru channel list not found on the orders page")
    return json.loads(m.group(1))


def gross_by_day(client: OmsGuruClient, days: list[date], max_pages: int = 80):
    """(Counter[(day, block)] of units, Counter of unmapped channel units)."""
    start = datetime.combine(min(days), datetime.min.time())
    end = datetime.combine(max(days) + timedelta(days=1), datetime.min.time())
    wanted = set(days)
    gross: Counter = Counter()
    unmapped: Counter = Counter()
    names: dict[str, str] = {}
    warehouses = list(client.warehouses()) or [None]
    for wh in warehouses:
        if wh:
            client.set_warehouse(wh)
        if not names:
            names = channel_names(client)
        flt = client.order_filter(start, end)  # every channel
        rows = 0
        for page in range(1, max_pages + 1):
            url = client.list_page_url(flt, page)
            soup = BeautifulSoup(client.get(url).text, "html.parser")
            table = soup.select_one("#order-table")
            if table is None:
                raise RuntimeError(f"Orders table not found on {url}")
            added = 0
            for tr in table.select("tbody > tr"):
                tds = tr.find_all("td", recursive=False)
                if len(tds) < 7:
                    continue
                added += 1
                m = ORDER_DATE_RE.search(text_of(tds[2]))
                if not m:
                    continue
                day = datetime.strptime(m.group(1), "%d/%b/%Y %I:%M %p").date()
                if day not in wanted:
                    continue
                ch = tr.select_one('a[href*="channel_companies/edit/"]')
                chid = re.search(r"edit/(\d+)", ch["href"]).group(1) if ch else ""
                qty = sum(int(q) for q in re.findall(r"Qty\s+(\d+)", text_of(tds[3]))) or 1
                name = names.get(chid, chid or "(blank)")
                block = block_for(name)
                if block == IGNORE:
                    continue
                if block:
                    gross[(day, block)] += qty
                else:
                    unmapped[name] += qty
            rows += added
            if added == 0 or not soup.select_one(f'a[href*="page:{page + 1}"]'):
                break
            time.sleep(client.delay)
        log.info("OMSGuru warehouse %s: %d order lines read", wh or "current", rows)
    return gross, unmapped
