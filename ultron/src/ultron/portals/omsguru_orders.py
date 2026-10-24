"""OMSGuru order list lines: units per order date and channel, status, SLA, AWB, SKUs.

Read from the order list pages (every warehouse, every channel), which give the same numbers as
OMSGuru's order export: one row per sub-order line with its Qty.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from .omsguru import BASE_URL, OmsGuruClient, text_of

log = logging.getLogger(__name__)

ORDER_DATE_RE = re.compile(r"Order Date\s+(\d{2}/\w{3}/\d{4} \d{2}:\d{2} [AP]M)")
SLA_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4} \d{1,2}:\d{2}:\d{2} [AP]M)")
# The order list stops after ~10 pages (~3,000 lines) whatever the filter: a window that comes
# back near it is read again in two halves.
LIST_CAP = 2500
CANCELLED = re.compile(r"^\s*(cancelled|cancelled before shipping|removed before shipping)\s*$", re.I)
OPEN = {"new", "pending", "processing", "packed"}      # not ready to ship yet


def channel_names(client: OmsGuruClient) -> dict[str, str]:
    html = client.get(f"{BASE_URL}/orders").text
    m = re.search(r'"channel_company_id":\{"type":"preFilled".*?"dataOptions":(\{.*?\})', html)
    if not m:
        raise RuntimeError("OMSGuru channel list not found on the orders page")
    return json.loads(m.group(1))


def _row(tr) -> dict | None:
    """One order list line: day, time, channel id, order id, units, status, AWB, [(SKU, qty)],
    order value (the line's Total, Rs), SLA; None for others."""
    tds = tr.find_all("td", recursive=False)
    if len(tds) < 7:
        return None
    m = ORDER_DATE_RE.search(text_of(tds[2]))
    if not m:
        return {}
    ordered = datetime.strptime(m.group(1), "%d/%b/%Y %I:%M %p")
    ch = tr.select_one('a[href*="channel_companies/edit/"]')
    order = re.search(r"OrderID\s+(\S+)", text_of(tds[1]))
    sla = SLA_RE.search(text_of(tds[6]))
    awb = re.search(r"AWB\s+(\S+)", text_of(tds[5]))
    skus = [(s.upper(), int(q)) for s, q in re.findall(r"SkuCode\s+(\S+).*?Qty\s+(\d+)", text_of(tds[3]))]
    total = re.search(r"Total\s+([\d,]+(?:\.\d+)?)", text_of(tds[2]))
    return {"day": ordered.date(), "ordered": ordered,
            "channel": re.search(r"edit/(\d+)", ch["href"]).group(1) if ch else "",
            "order": order.group(1) if order else "",
            "qty": sum(int(q) for q in re.findall(r"Qty\s+(\d+)", text_of(tds[3]))) or 1,
            "status": text_of(tds[4]), "awb": awb.group(1).upper() if awb else "", "skus": skus,
            "value": float(total.group(1).replace(",", "")) if total else 0.0,
            "sla": datetime.strptime(sla.group(1), "%B %d, %Y %I:%M:%S %p") if sla else None}


def _read_window(client: OmsGuruClient, start: datetime, end: datetime, max_pages: int) -> tuple[list[dict], int]:
    """(order lines with an order date, lines read) of one window, every channel."""
    flt = client.order_filter(start, end)
    out, rows = [], 0
    for page in range(1, max_pages + 1):
        url = client.list_page_url(flt, page)
        soup = BeautifulSoup(client.get(url).text, "html.parser")
        table = soup.select_one("#order-table")
        if table is None:
            raise RuntimeError(f"Orders table not found on {url}")
        added = 0
        for tr in table.select("tbody > tr"):
            r = _row(tr)
            if r is None:
                continue
            added += 1
            if r:
                out.append(r)
        rows += added
        if added == 0 or not soup.select_one(f'a[href*="page:{page + 1}"]'):
            break
        time.sleep(client.delay)
    return out, rows


def order_lines(client: OmsGuruClient, days: list[date], max_pages: int = 80) -> tuple[list[dict], dict[str, str]]:
    """(every order list line ordered on one of `days`, {channel id: name}), every warehouse.
    Each day is its own window (the days need not follow each other); a window near the list's
    limit is read again in two halves."""
    lines: list[dict] = []
    names: dict[str, str] = {}
    wanted = set(days)
    for wh in list(client.warehouses()) or [None]:
        if wh:
            client.set_warehouse(wh)
        if not names:
            names = channel_names(client)
        todo = [(datetime.combine(d, datetime.min.time()), datetime.combine(d + timedelta(days=1), datetime.min.time()))
                for d in sorted(wanted)]
        rows = 0
        while todo:
            a, b = todo.pop(0)
            got, n = _read_window(client, a, b, max_pages)
            if n >= LIST_CAP and b - a > timedelta(hours=1):
                mid = a + (b - a) / 2
                log.info("%d lines %s..%s, near the list limit: read in two halves", n, a, b)
                todo[:0] = [(a, mid), (mid, b)]
                continue
            lines += [r for r in got if r["day"] in wanted]
            rows += n
        log.info("OMSGuru warehouse %s: %d order lines read", wh or "current", rows)
    return lines, names


def units_by_channel(client: OmsGuruClient, days: list[date], max_pages: int = 80):
    """(Counter[(day, channel id)] of units, Counter[(day, channel id)] of cancelled units,
    {channel id: name}) for the order dates in `days`, every warehouse."""
    lines, names = order_lines(client, days, max_pages)
    gross: Counter = Counter()
    cancel: Counter = Counter()
    for r in lines:
        gross[(r["day"], r["channel"])] += r["qty"]
        if CANCELLED.match(r["status"]):
            cancel[(r["day"], r["channel"])] += r["qty"]
    return gross, cancel, names


def at_risk(lines: list[dict], now: datetime) -> list[dict]:
    """Open lines whose SLA is today or earlier, overdue first, then by SLA."""
    end = datetime.combine(now.date(), datetime.max.time())
    out = [r for r in lines if r["status"].strip().lower() in OPEN and r["sla"] and r["sla"] <= end]
    return sorted(out, key=lambda r: (r["sla"] >= now, r["sla"], r["channel"]))
