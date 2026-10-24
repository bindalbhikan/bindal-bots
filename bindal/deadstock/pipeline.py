"""Dead and slow stock (monthly, the 1st at 11 AM): units sitting in the warehouse that do not sell.

Stock = the OMSGuru inventory export of every warehouse; sold = units on order lines of the last
DEAD_SALE_DAYS (90) complete order days, every channel, cancelled lines left out.
* dead: DEAD_MIN_STOCK (5)+ in stock and nothing sold in those days
* slow: sold something, but the stock lasts DEAD_SLOW_DAYS (180)+ days at that pace
Listed by style, most units first, with a few lines from the agent's free model on what to do
(liquidate, bundle, push on a channel). Telegram only, nothing written; the styles are listed in
the message when they fit in one, otherwise every SKU comes in an attached Excel.
"""
from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from datetime import date, timedelta

from ..core import telegram
from ..core.app import run_bot
from ..core.config import num
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import order_lines
from ..stock.pipeline import size_key, sold_of, stock_of, style_size

log = logging.getLogger("bindal.deadstock")

COMMENT = """You are the e-commerce head of Bindal Exports (women's apparel on Myntra, AJIO, Flipkart,
Amazon, Meesho, Nykaa). Below is this month's dead stock (in stock, nothing sold in 90 days) and slow
stock (more than 180 days of stock at the current pace), by style. Write 3 short lines for the owner
in plain English: how big the problem is (units), which styles to act on first, and what to do with
them (clearance price, bundle or combo, move to another channel, stop reorders). Numbers exact, no
markdown, no greeting."""


def classify(stock, sold, days: int, min_stock: int, slow_days: float) -> tuple[list, list]:
    """(dead, slow), each [{"sku", "stock", "sold", "days_left"}], most stock first."""
    dead, slow = [], []
    for sku, qty in stock.items():
        if qty < min_stock:
            continue
        n = sold.get(sku, 0)
        if n == 0:
            dead.append({"sku": sku, "stock": qty, "sold": 0, "days_left": None})
        else:
            left = qty / (n / days)
            if left >= slow_days:
                slow.append({"sku": sku, "stock": qty, "sold": n, "days_left": left})
    key = lambda r: (-r["stock"], r["sku"])  # noqa: E731
    return sorted(dead, key=key), sorted(slow, key=key)


def by_style(items: list[dict]) -> list[tuple[str, int, list[dict]]]:
    styles: dict[str, list[dict]] = defaultdict(list)
    for r in items:
        styles[style_size(r["sku"])[0]].append(r)
    return sorted(((s, sum(r["stock"] for r in rs), rs) for s, rs in styles.items()), key=lambda t: -t[1])


def section(title: str, items: list[dict], slow: bool) -> telegram.Listing:
    styles = by_style(items)
    lines, rows = [], []
    for style, units, rs in styles:
        rs = sorted(rs, key=lambda r: size_key(style_size(r["sku"])[1]))
        sizes = ", ".join(f"{style_size(r['sku'])[1] or r['sku']} {r['stock']}" for r in rs)
        extra = f" · sold {sum(r['sold'] for r in rs)}" if slow else ""
        lines.append(f"• <b>{telegram.esc(style)}</b> {units} units{extra} · {telegram.esc(sizes)}")
        rows += [{"Style": style, "SKU": r["sku"], "Size": style_size(r["sku"])[1], "Stock": r["stock"],
                  "Sold": r["sold"], **({"Days of stock": round(r["days_left"])} if slow else {})} for r in rs]
    return telegram.Listing(
        f"<b>{title}: {sum(r['stock'] for r in items)} units in {len(items)} SKUs, {len(styles)} styles</b>",
        lines, rows, "Slow" if slow else "Dead")


def run(args) -> int:
    today = date.today()
    days = int(num("DEAD_SALE_DAYS", 90))
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
    dead, slow = classify(stock, sold, days, int(num("DEAD_MIN_STOCK", 5)), num("DEAD_SLOW_DAYS", 180))
    log.info("Dead stock: %d SKUs in stock, %d sold in %d days, %d dead (%d units), %d slow (%d units)",
             sum(1 for q in stock.values() if q > 0), len(sold), days, len(dead), sum(r["stock"] for r in dead),
             len(slow), sum(r["stock"] for r in slow))
    out = [f"🧊 <b>Dead and slow stock · {today:%B %Y}</b>",
           f"<i>in stock against units sold in the last {days} days, every channel</i>", ""]
    out.append(section(f"Dead (nothing sold in {days} days)", dead, slow=False) if dead else "No dead stock.")
    out += ["", section(f"Slow ({num('DEAD_SLOW_DAYS', 180):.0f}+ days of stock)", slow, slow=True) if slow
            else "No slow stock."]
    try:
        from ultron.core import llm
        plain = telegram.render(out).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        note = (llm.chat([{"role": "system", "content": COMMENT}, {"role": "user", "content": plain[:12000]}],
                         max_tokens=1500)["choices"][0]["message"].get("content") or "").strip()
        if note:
            out += ["", "🧠 " + telegram.md(note)]
    except Exception as exc:  # noqa: BLE001
        log.warning("No comment: %s", exc)
    telegram.send_report(out, "Dead_stock", dry=args.no_telegram)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py deadstock", description="Dead and slow stock, monthly")
    p.add_argument("--no-telegram", action="store_true", help="print the report instead of sending it")
    return run_bot("Dead stock report", p, run, argv)
