"""Stock-out alert (10 AM): SKUs that sell and are out of stock or about to be, from OMSGuru.

Sold = units on order lines of the last STOCK_SALE_DAYS (14) order days, every channel, cancelled
lines left out. Stock = the OMSGuru inventory export of every warehouse (Qty, blocked not in it).
At risk = sold at least STOCK_MIN_SOLD (7) units and stock for STOCK_DAYS (3) days or less at
that pace (0 included). Listed by style with the sizes at risk. Telegram only, nothing written.
"""
from __future__ import annotations

import argparse
import io
import logging
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

import pandas as pd

from ..core import telegram
from ..core.app import run_bot
from ..core.config import num
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import CANCELLED, order_lines

log = logging.getLogger("bindal.stock")

SIZE = re.compile(r"^(XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL|FS|F|\d{2,3})$", re.I)
SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL", "5XL", "6XL", "FS", "F"]
MAX_STYLES = 30


def size_key(size: str):
    return (0, SIZE_ORDER.index(size)) if size in SIZE_ORDER else (1, int(size)) if size.isdigit() else (2, size)


def style_size(sku: str) -> tuple[str, str]:
    """("COORD-AE-1-N10", "L") from COORD-AE-1-N10-L; a SKU without a size suffix is its own style."""
    head, _, tail = sku.rpartition("-")
    return (head, tail.upper()) if head and SIZE.match(tail) else (sku, "")


def stock_of(csv_blobs: list[bytes]) -> Counter:
    """SKU -> units in stock, summed over the warehouses' inventory exports."""
    stock: Counter = Counter()
    for blob in csv_blobs:
        df = pd.read_csv(io.BytesIO(blob), dtype={"Item SkuCode": str})
        for sku, qty in zip(df["Item SkuCode"].astype(str).str.strip().str.upper(), pd.to_numeric(df["Qty"], errors="coerce")):
            if sku and pd.notna(qty):
                stock[sku] += int(qty)
    return stock


def sold_of(lines: list[dict]) -> Counter:
    sold: Counter = Counter()
    for line in lines:
        if not CANCELLED.match(line["status"]):
            for sku, qty in line.get("skus") or []:
                sold[sku] += qty
    return sold


def at_risk(sold: Counter, stock: Counter, days: int, min_sold: int, cover_days: float) -> list[dict]:
    """SKUs known to the inventory that sold min_sold+ and have cover_days of stock or less."""
    out = []
    for sku, n in sold.items():
        if sku not in stock or n < min_sold:
            continue                          # combos / unknown SKUs are not in the inventory
        per_day = n / days
        left = stock[sku] / per_day
        if left <= cover_days:
            out.append({"sku": sku, "sold": n, "per_day": per_day, "stock": max(stock[sku], 0),
                        "days_left": max(left, 0)})
    return sorted(out, key=lambda r: (-r["per_day"], r["sku"]))


def message(risk: list[dict], days: int, cover_days: float, today: date) -> str:
    styles: dict[str, list[dict]] = defaultdict(list)
    for r in risk:
        styles[style_size(r["sku"])[0]].append(r)
    ranked = sorted(styles.items(), key=lambda kv: -sum(r["per_day"] for r in kv[1]))
    oos = sum(1 for r in risk if r["stock"] == 0)
    lost = sum(r["per_day"] for r in risk if r["stock"] == 0)
    out = [f"📦 <b>Stock-out alert · {today:%d %b}</b>",
           f"<i>SKUs selling in the last {days} days with {cover_days:g} days of stock or less</i>",
           f"Out of stock: <b>{oos}</b> SKUs (about {lost:.0f} units a day not sold) · "
           f"running out: <b>{len(risk) - oos}</b>", ""]
    for style, items in ranked[:MAX_STYLES]:
        sizes = ", ".join(f"{style_size(r['sku'])[1] or r['sku']} {r['stock']}"
                          + (f" ({r['days_left']:.1f}d)" if r["stock"] else " (OUT)")
                          for r in sorted(items, key=lambda r: size_key(style_size(r["sku"])[1])))
        out.append(f"• <b>{telegram.esc(style)}</b> · sells {sum(r['per_day'] for r in items):.1f}/day · "
                   f"{telegram.esc(sizes)}")
    if len(ranked) > MAX_STYLES:
        out.append(f"... and {len(ranked) - MAX_STYLES} more styles")
    out += ["", "<i>size stock (days left at the current pace); OUT = 0 in stock. "
                "Sold = every channel, cancelled orders left out.</i>"]
    return "\n".join(out)


def run(args) -> int:
    today = date.today()
    days = int(num("STOCK_SALE_DAYS", 14))
    min_sold, cover = int(num("STOCK_MIN_SOLD", 7)), float(num("STOCK_DAYS", 3))
    client = OmsGuruClient.from_env()
    client.login()
    blobs = []
    for wh in list(client.warehouses()) or [None]:
        if wh:
            client.set_warehouse(wh)
        blobs.append(client.export_inventory(wait_minutes=num("STOCK_EXPORT_WAIT_MINUTES", 12)))
    stock = stock_of(blobs)
    lines, _ = order_lines(client, [today - timedelta(days=i) for i in range(1, days + 1)])
    sold = sold_of(lines)
    risk = at_risk(sold, stock, days, min_sold, cover)
    log.info("Stock: %d SKUs in the inventory, %d sold in %d days, %d at risk (%d out of stock)",
             len(stock), len(sold), days, len(risk), sum(1 for r in risk if r["stock"] == 0))
    if not risk:
        log.info("Nothing at risk: nothing sent")
        return 0
    text = message(risk, days, cover, today)
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py stock", description="SKUs that sell and are running out")
    p.add_argument("--no-telegram", action="store_true", help="print the alert instead of sending it")
    return run_bot("Stock-out alert", p, run, argv)
