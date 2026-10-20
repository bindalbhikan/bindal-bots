"""SLA warning (12 PM and 3 PM): orders on every channel whose dispatch SLA is today or already
passed and that are still not ready to ship (OMSGuru status New / Pending / Processing / Packed).

Telegram only, and only when there are such orders; nothing is written.
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from datetime import datetime, timedelta

from ..core import telegram
from ..core.app import run_bot
from ..core.config import num
from ..morning.pipeline import channel_line
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import order_lines

log = logging.getLogger("bindal.sla")

OPEN = {"new", "pending", "processing", "packed"}      # not ready to ship yet
MAX_LISTED = 40                                         # orders listed by number (Telegram length)


def at_risk(lines: list[dict], now: datetime) -> list[dict]:
    """Open lines whose SLA is today or earlier, overdue first, then by SLA."""
    end = datetime.combine(now.date(), datetime.max.time())
    out = [r for r in lines if r["status"].strip().lower() in OPEN and r["sla"] and r["sla"] <= end]
    return sorted(out, key=lambda r: (r["sla"] >= now, r["sla"], r["channel"]))


def message(risk: list[dict], names: dict[str, str], now: datetime) -> str:
    """Counts per channel (overdue / due later today), then the orders channel by channel."""
    def chan(r):
        return channel_line(names.get(r["channel"], r["channel"]))

    over = [r for r in risk if r["sla"] < now]
    per = Counter((chan(r), r["sla"] < now) for r in risk)
    chans = sorted({c for c, _ in per}, key=lambda c: -(per[(c, True)] + per[(c, False)]))
    table = [f"{'Channel':<12}{'Over':>6}{'Today':>7}",
             *[f"{c[:12]:<12}{per[(c, True)]:>6}{per[(c, False)]:>7}" for c in chans],
             f"{'Total':<12}{len(over):>6}{len(risk) - len(over):>7}"]
    listed = []
    shown = risk[:MAX_LISTED]
    for c in chans:
        mine = [r for r in shown if chan(r) == c]
        if mine:
            listed += ["", f"<b>{telegram.esc(c)}</b>"]
            listed += [f"• {telegram.esc(r['order'])} · {telegram.esc(r['status'])} · "
                       + ("<b>OVER</b>" if r["sla"] < now else f"due {r['sla']:%I:%M %p}") for r in mine]
    if len(risk) > MAX_LISTED:
        listed += ["", f"... and {len(risk) - MAX_LISTED} more"]
    return "\n".join([
        f"⏰ <b>SLA warning · {now:%d %b, %I:%M %p}</b>",
        f"<i>{len(risk)} order{'s' if len(risk) != 1 else ''} due today or overdue, not ready to ship yet</i>",
        "<pre>" + telegram.esc("\n".join(table)) + "</pre>",
        *listed,
        "",
        "<i>Over = SLA already passed; Today = due later today. Pack them and mark them ready to ship first.</i>"])


def run(args) -> int:
    now = datetime.now()
    days = [now.date() - timedelta(days=i) for i in range(int(num("SLA_ORDER_DAYS", 8)))]
    client = OmsGuruClient.from_env()
    client.login()
    lines, names = order_lines(client, days)
    risk = at_risk(lines, now)
    log.info("SLA: %d order lines read, %d due today or overdue and not ready to ship", len(lines), len(risk))
    if not risk:
        log.info("Nothing at risk: nothing sent")
        return 0
    text = message(risk, names, now)
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py sla", description="Orders due today that are not ready to ship")
    p.add_argument("--no-telegram", action="store_true", help="print the warning instead of sending it")
    return run_bot("SLA warning", p, run, argv)
