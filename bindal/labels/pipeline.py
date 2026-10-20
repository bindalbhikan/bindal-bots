"""Label check (6 PM): ERP shipping-label prints against OMSGuru, every channel, matched on AWB.

* printed for an order OMSGuru shows cancelled (Cancel Init / Cancelled ...): stop the parcel
* printed but still not ready to ship in OMSGuru (New / Pending / Processing / Packed)
* ready to ship in OMSGuru (orders of the last 3 days) with no ERP label print: packed outside
  the ERP, so its stock was not taken off there
* label prints per packer (ERP "Created By")

ERP report 62's "Order ID" is the AWB for every portal (Myntra: the Myntra tracking number, which
OMSGuru shows too); 20 Oct 2026: 1,599 of 1,764 prints matched. Telegram only, nothing written.
"""
from __future__ import annotations

import argparse
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta

from ..core import telegram
from ..core.app import run_bot
from ..core.config import num
from ..morning.pipeline import channel_line
from ..portals.erp import DISPATCH_HEADERS, DISPATCH_REPORT, Erp
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import order_lines
from ..sla.pipeline import OPEN

log = logging.getLogger("bindal.labels")

CANCELLED = re.compile(r"^\s*cancel", re.I)
MAX_LISTED = 25


def first_prints(rows: list[dict]) -> dict[str, dict]:
    """AWB -> its first ERP label print (the line with the earliest Created On)."""
    out: dict[str, dict] = {}
    for r in rows:
        awb = str(r.get("Order ID") or "").strip().upper()
        if awb and r.get("Created On") and (awb not in out or r["Created On"] < out[awb]["Created On"]):
            out[awb] = r
    return out


def check(prints: dict[str, dict], lines: list[dict], since: datetime, rts_from: date) -> dict:
    """The three mismatch lists and the packer counts. prints: every print of the ERP window;
    since: prints from then on are checked; rts_from: ready-to-ship orders from this order day on
    must have a print in the window."""
    by_awb = {l["awb"]: l for l in lines if l["awb"]}
    recent = {k: r for k, r in prints.items() if r["Created On"] >= since}
    matched = {k: by_awb[k] for k in recent if k in by_awb}
    cancelled = [(recent[k], l) for k, l in matched.items() if CANCELLED.match(l["status"])]
    not_rts = [(recent[k], l) for k, l in matched.items() if l["status"].strip().lower() in OPEN]
    no_label = [l for l in lines if l["status"].strip().lower() == "ready to ship" and l["awb"]
                and l["awb"] not in prints and l["day"] >= rts_from]
    packers = Counter(str(r.get("Created By") or "?") for r in recent.values())
    packer_cancel = Counter(str(p.get("Created By") or "?") for p, _ in cancelled)
    return {"recent": len(recent), "matched": len(matched), "cancelled": cancelled, "not_rts": not_rts,
            "no_label": no_label, "packers": packers, "packer_cancel": packer_cancel}


def message(res: dict, names: dict[str, str], now: datetime, since: datetime, rts_from: date) -> str:
    def chan(l):
        return channel_line(names.get(l["channel"], l["channel"]))

    def printed(items):
        return [f"• {telegram.esc(l['awb'])} · {telegram.esc(chan(l))} · {telegram.esc(l['status'])} · "
                f"{telegram.esc(str(p.get('Created By') or '?'))} {p['Created On']:%d %b %H:%M}"
                for p, l in items[:MAX_LISTED]] + ([f"... and {len(items) - MAX_LISTED} more"]
                                                   if len(items) > MAX_LISTED else [])

    out = [f"🏷 <b>Label check · {now:%d %b, %I:%M %p}</b>",
           f"ERP label prints since {since:%d %b}: <b>{res['recent']}</b> · found in OMSGuru {res['matched']}"]
    if res["cancelled"]:
        out += ["", f"🛑 <b>Printed for cancelled orders: {len(res['cancelled'])}</b> (check them; hold any not handed over yet)",
                *printed(res["cancelled"])]
    if res["not_rts"]:
        out += ["", f"⏳ <b>Printed, not ready to ship in OMSGuru: {len(res['not_rts'])}</b>", *printed(res["not_rts"])]
    if res["no_label"]:
        per = Counter(chan(l) for l in res["no_label"])
        out += ["", f"❓ <b>Ready to ship in OMSGuru, no ERP label: {len(res['no_label'])}</b> "
                f"(orders since {rts_from:%d %b}; packed outside the ERP, stock not taken off there)",
                " · ".join(f"{telegram.esc(c)} {n}" for c, n in per.most_common()),
                *[f"• {telegram.esc(l['awb'])} · {telegram.esc(chan(l))} · ordered {l['ordered']:%d %b}"
                  for l in res["no_label"][:MAX_LISTED]],
                *([f"... and {len(res['no_label']) - MAX_LISTED} more"] if len(res["no_label"]) > MAX_LISTED else [])]
    if not (res["cancelled"] or res["not_rts"] or res["no_label"]):
        out += ["", "✅ No mismatch between ERP label prints and OMSGuru."]
    table = [f"{'Packer':<14}{'Labels':>7}{'Cncl':>6}",
             *[f"{p[:14]:<14}{n:>7}{res['packer_cancel'].get(p, 0):>6}" for p, n in res["packers"].most_common()]]
    out += ["", f"👷 <b>Packers since {since:%d %b}</b>", "<pre>" + telegram.esc("\n".join(table)) + "</pre>",
            "<i>Cncl = labels printed for orders now cancelled</i>"]
    return "\n".join(out)


def run(args) -> int:
    now = datetime.now()
    today = now.date()
    since = datetime.combine(today - timedelta(days=1), datetime.min.time())   # yesterday and today
    rts_from = today - timedelta(days=int(num("LABEL_RTS_DAYS", 3)))
    window = today - timedelta(days=int(num("LABEL_ERP_DAYS", 5)))
    erp = Erp.from_env()
    erp.login()
    rows = erp.report(DISPATCH_REPORT, DISPATCH_HEADERS, where=[("TRANS_DATE", ">=", f"{window:%Y-%m-%d}T00:00:00")])
    prints = first_prints(rows)
    client = OmsGuruClient.from_env()
    client.login()
    lines, names = order_lines(client, [today - timedelta(days=i) for i in range(int(num("LABEL_ORDER_DAYS", 12)))])
    res = check(prints, lines, since, rts_from)
    log.info("Labels: %d prints since %s, %d matched, %d cancelled, %d not RTS, %d RTS without label",
             res["recent"], since, res["matched"], len(res["cancelled"]), len(res["not_rts"]), len(res["no_label"]))
    text = message(res, names, now, since, rts_from)
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py labels", description="ERP label prints against OMSGuru")
    p.add_argument("--no-telegram", action="store_true", help="print the check instead of sending it")
    return run_bot("Label check", p, run, argv)
