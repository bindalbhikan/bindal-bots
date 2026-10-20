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

from ..portals.omsguru import BASE_URL, OmsGuruClient, text_of

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


# The order list stops after ~10 pages (~3,000 lines) whatever the filter: a window that comes
# back near it is read again in two halves.
LIST_CAP = 2500
CANCELLED = re.compile(r"^\s*(cancelled|cancelled before shipping|removed before shipping)\s*$", re.I)


def _read_window(client: OmsGuruClient, start: datetime, end: datetime, wanted: set[date],
                 max_pages: int) -> tuple[Counter, Counter, int]:
    """(units per (day, channel id), cancelled units per (day, channel id), lines read) of one window."""
    gross: Counter = Counter()
    cancel: Counter = Counter()
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
            gross[(day, chid)] += qty
            if CANCELLED.match(text_of(tds[4])):
                cancel[(day, chid)] += qty
        rows += added
        if added == 0 or not soup.select_one(f'a[href*="page:{page + 1}"]'):
            break
        time.sleep(client.delay)
    return gross, cancel, rows


def units_by_channel(client: OmsGuruClient, days: list[date], max_pages: int = 80):
    """(Counter[(day, channel id)] of units, Counter[(day, channel id)] of cancelled units,
    {channel id: name}) for the order dates in `days`, every warehouse. Each day is its own window
    (the days need not follow each other); a window near the list's limit is split in two."""
    gross: Counter = Counter()
    cancel: Counter = Counter()
    names: dict[str, str] = {}
    wanted = set(days)
    warehouses = list(client.warehouses()) or [None]
    for wh in warehouses:
        if wh:
            client.set_warehouse(wh)
        if not names:
            names = channel_names(client)
        todo = [(datetime.combine(d, datetime.min.time()), datetime.combine(d + timedelta(days=1), datetime.min.time()))
                for d in sorted(wanted)]
        rows = 0
        while todo:
            a, b = todo.pop(0)
            g, c, n = _read_window(client, a, b, wanted, max_pages)
            if n >= LIST_CAP and b - a > timedelta(hours=1):
                mid = a + (b - a) / 2
                log.info("%d lines %s..%s, near the list limit: read in two halves", n, a, b)
                todo[:0] = [(a, mid), (mid, b)]
                continue
            gross.update(g)
            cancel.update(c)
            rows += n
        log.info("OMSGuru warehouse %s: %d order lines read", wh or "current", rows)
    return gross, cancel, names


def gross_by_day(client: OmsGuruClient, days: list[date], max_pages: int = 80):
    """(Counter[(day, block)] of units, Counter of unmapped channel units)."""
    units, _, names = units_by_channel(client, days, max_pages)
    gross: Counter = Counter()
    unmapped: Counter = Counter()
    for (day, chid), qty in units.items():
        name = names.get(chid, chid or "(blank)")
        block = block_for(name)
        if block == IGNORE:
            continue
        if block:
            gross[(day, block)] += qty
        else:
            unmapped[name] += qty
    return gross, unmapped
