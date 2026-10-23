"""Weekly business review (Monday 10:15 AM): the last 7 days against the 7 before them.

* per channel: units, order value (Rs, the order lines' Total) and cancel %
* the top styles by units and the styles that fell most (sold 10+ the week before)
* label prints per packer (ERP report 62), this week and last
* a few lines from the agent's free model

OMSGuru order lines (every channel) and the ERP; Telegram only, nothing written.
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from datetime import date, datetime, timedelta

from ..core import telegram
from ..core.app import run_bot
from ..morning.pipeline import channel_line, change
from ..portals.erp import DISPATCH_HEADERS, DISPATCH_REPORT, Erp
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import CANCELLED, order_lines
from ..stock.pipeline import style_size

log = logging.getLogger("bindal.weekly")

TOP = 10
COMMENT = """You are the e-commerce head of Bindal Exports (women's apparel on Myntra, AJIO, Flipkart,
Amazon, Meesho, Nykaa). Below is the weekly business review: this week against last week per
channel (units, order value, cancel %), the top styles, the styles that fell most and packer output.
Write 3 or 4 short lines for the owner in plain English: the overall trend, the channel and styles
that need attention, and two concrete actions for this week. Numbers exact, no markdown, no
greeting; say only what the data shows. A style that sold less may have run out of stock or lost
its listing: say to check its stock and listing first; never suggest discounts, price changes or
staffing changes."""


def weeks(today: date) -> tuple[list[date], list[date]]:
    """(this week = the last 7 complete days, last week = the 7 before)."""
    this = [today - timedelta(days=i) for i in range(1, 8)]
    return this, [d - timedelta(days=7) for d in this]


def by_channel(lines: list[dict], names: dict[str, str], this: list[date], last: list[date]) -> list[tuple]:
    """[(channel, units now, units before, value now, value before, cancel % now)], biggest first."""
    units, value, canc = Counter(), Counter(), Counter()
    tw, lw = set(this), set(last)
    for l in lines:
        wk = "now" if l["day"] in tw else "before" if l["day"] in lw else None
        if not wk:
            continue
        c = channel_line(names.get(l["channel"], l["channel"]))
        units[(c, wk)] += l["qty"]
        value[(c, wk)] += l.get("value") or 0
        if wk == "now" and CANCELLED.match(l["status"]):
            canc[c] += l["qty"]
    chans = sorted({c for c, _ in units}, key=lambda c: -units[(c, "now")])
    return [(c, units[(c, "now")], units[(c, "before")], value[(c, "now")], value[(c, "before")],
             round(100 * canc[c] / units[(c, "now")]) if units[(c, "now")] else 0) for c in chans]


def by_style(lines: list[dict], this: list[date], last: list[date]) -> tuple[list, list]:
    """(top styles by units this week, styles that fell most), each [(style, now, before)]."""
    now, before = Counter(), Counter()
    tw, lw = set(this), set(last)
    for l in lines:
        if CANCELLED.match(l["status"]) or (l["day"] not in tw and l["day"] not in lw):
            continue
        for sku, qty in l.get("skus") or []:
            (now if l["day"] in tw else before)[style_size(sku)[0]] += qty
    top = [(s, n, before[s]) for s, n in now.most_common(TOP)]
    fell = sorted(((s, now[s], b) for s, b in before.items() if b >= 10 and now[s] < b),
                  key=lambda t: t[1] - t[2])[:TOP]
    return top, fell


def packers(erp_rows: list[dict], this: list[date], last: list[date]) -> list[tuple[str, int, int]]:
    """[(packer, label prints this week, last week)] from ERP report 62 lines."""
    count = Counter()
    tw, lw = set(this), set(last)
    for r in erp_rows:
        when = r.get("Created On")
        if not isinstance(when, datetime):
            continue
        wk = "now" if when.date() in tw else "before" if when.date() in lw else None
        if wk:
            count[(str(r.get("Created By") or "?"), wk)] += 1
    names = sorted({p for p, _ in count}, key=lambda p: -count[(p, "now")])
    return [(p, count[(p, "now")], count[(p, "before")]) for p in names]


def rupees(v: float) -> str:
    return f"{v / 1e5:.2f}L" if v >= 1e5 else f"{v / 1e3:.0f}K"


def message(ch: list, top: list, fell: list, pk: list, this: list[date], last: list[date]) -> str:
    # two narrow tables (28 characters at most) instead of one wide one: no wrapping on a phone
    t = [f"{'Channel':<11}{'Units':>6}{'±':>6}{'Cn%':>5}"]
    tv = [f"{'Channel':<11}{'Value':>8}{'±':>6}"]
    for c, n, b, v, vb, cp in ch:
        t.append(f"{c[:11]:<11}{n:>6}{change(n, b):>6}{cp:>5}")
        tv.append(f"{c[:11]:<11}{rupees(v):>8}{change(round(v), round(vb)):>6}")
    n, b = sum(r[1] for r in ch), sum(r[2] for r in ch)
    v, vb = sum(r[3] for r in ch), sum(r[4] for r in ch)
    t.append(f"{'Total':<11}{n:>6}{change(n, b):>6}")
    tv.append(f"{'Total':<11}{rupees(v):>8}{change(round(v), round(vb)):>6}")
    out = [f"📊 <b>Weekly review · {this[-1]:%d %b} - {this[0]:%d %b}</b>",
           f"<i>against {last[-1]:%d %b} - {last[0]:%d %b}; units and order value by order date</i>",
           "", "<b>Units</b> (Cn% = cancelled)",
           "<pre>" + telegram.esc("\n".join(t)) + "</pre>",
           "<b>Order value</b>",
           "<pre>" + telegram.esc("\n".join(tv)) + "</pre>",
           f"Order value {rupees(v)} ({change(round(v), round(vb))} on {rupees(vb)})", "",
           "<b>Top styles</b> (units, ± last week)",
           *[f"• {telegram.esc(s)} {n} ({change(n, b)})" for s, n, b in top]]
    if fell:
        out += ["", "<b>Fell most</b> (units now / last week)",
                *[f"• {telegram.esc(s)} {n} / {b}" for s, n, b in fell]]
    if pk:
        out += ["", "<b>Packers</b> (ERP label prints, this week / last)",
                *[f"• {telegram.esc(p)} {n} / {b}" for p, n, b in pk]]
    return "\n".join(out)


def run(args) -> int:
    today = date.today()
    this, last = weeks(today)
    client = OmsGuruClient.from_env()
    client.login()
    lines, names = order_lines(client, this + last)
    ch = by_channel(lines, names, this, last)
    top, fell = by_style(lines, this, last)
    pk = []
    try:
        erp = Erp.from_env()
        erp.login()
        pk = packers(erp.report(DISPATCH_REPORT, DISPATCH_HEADERS,
                                where=[("TRANS_DATE", ">=", f"{last[-1]:%Y-%m-%d}T00:00:00")]), this, last)
    except Exception as exc:  # noqa: BLE001 - the review goes out without the packers
        log.warning("Packers not read: %s", exc)
    text = message(ch, top, fell, pk, this, last)
    try:
        from ..agent import llm
        plain = text.replace("<pre>", "").replace("</pre>", "").replace("<b>", "").replace("</b>", "")
        plain = plain.replace("<i>", "").replace("</i>", "")
        note = (llm.chat([{"role": "system", "content": COMMENT}, {"role": "user", "content": plain}],
                         max_tokens=2000)["choices"][0]["message"].get("content") or "").strip()
        if note:
            text += "\n\n🧠 " + telegram.md(note)
    except Exception as exc:  # noqa: BLE001
        log.warning("No comment: %s", exc)
    log.info("Weekly: %d channels, %d order lines", len(ch), len(lines))
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py weekly", description="Weekly business review")
    p.add_argument("--no-telegram", action="store_true", help="print the review instead of sending it")
    return run_bot("Weekly review", p, run, argv)
