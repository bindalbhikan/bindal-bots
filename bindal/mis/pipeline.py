"""Monthly MIS (the 2nd at 11 AM): last month per channel against the month before, and a plan for
this month from the agent's free model.

Per channel, from OMSGuru order lines by order date: units, order value (the lines' "Total", Rs),
cancelled share, and - when MIS_RATES gives the channel's commission % and shipping per unit -
commission, shipping and the net after them. MIS_RATES is a JSON object kept as a GitHub variable
(the repo is public), e.g. {"Flipkart": {"commission": 22, "shipping": 70}, "Myntra": {...}};
a key matches every channel line starting with it ("Myntra" = MAIN, PPMP, DBCC, FMCC).
Telegram only, nothing written.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import date, timedelta

from ..core import telegram
from ..core.app import run_bot
from ..core.config import env
from ..morning.pipeline import channel_line, change
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import CANCELLED, order_lines
from ..weekly.pipeline import rupees

log = logging.getLogger("bindal.mis")

PLAN = """You are the e-commerce head of Bindal Exports (women's apparel on Myntra, AJIO, Flipkart,
Amazon, Meesho, Nykaa). Below is last month's MIS per channel against the month before (units,
order value, cancel %, and commission / shipping / net where the rates are known). Write a short
plan for this month for the owner in plain English, 5 to 7 lines: where the business grew or fell,
which channels to push and which to fix, and 3 concrete actions with the numbers behind them.
A channel that sold less may have had stock or listing problems: say to check those first. Use only
the numbers in the data, copied exactly; never work out new ones and never invent budgets, ad spend,
targets or causes. No markdown tables, no greeting."""


def months(today: date) -> tuple[list[date], list[date]]:
    """(last month's days, the month before's days)."""
    end = today.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    span = lambda a, b: [a + timedelta(days=i) for i in range((b - a).days + 1)]  # noqa: E731
    return span(start, end), span(prev_start, prev_end)


def rates_for(channel: str, rates: dict) -> dict | None:
    for key in sorted(rates, key=len, reverse=True):
        if channel.lower().startswith(key.lower()):
            return rates[key]
    return None


def mis(lines: list[dict], names: dict[str, str], now_days: list[date], prev_days: list[date], rates: dict) -> list[dict]:
    units, value, cunits, cvalue = Counter(), Counter(), Counter(), Counter()
    tw, lw = set(now_days), set(prev_days)
    for l in lines:
        wk = "now" if l["day"] in tw else "prev" if l["day"] in lw else None
        if not wk:
            continue
        c = channel_line(names.get(l["channel"], l["channel"]))
        units[(c, wk)] += l["qty"]
        value[(c, wk)] += l.get("value") or 0
        if CANCELLED.match(l["status"]):
            cunits[(c, wk)] += l["qty"]
            cvalue[(c, wk)] += l.get("value") or 0
    out = []
    for c in sorted({c for c, _ in units}, key=lambda c: -value[(c, "now")]):
        row = {"channel": c, "units": units[(c, "now")], "units_prev": units[(c, "prev")],
               "value": value[(c, "now")], "value_prev": value[(c, "prev")],
               "cancel_pct": round(100 * cunits[(c, "now")] / units[(c, "now")]) if units[(c, "now")] else 0,
               "net_sale": value[(c, "now")] - cvalue[(c, "now")]}
        r = rates_for(c, rates)
        if r:
            kept = units[(c, "now")] - cunits[(c, "now")]
            row["commission"] = row["net_sale"] * float(r.get("commission", 0)) / 100
            row["shipping"] = kept * float(r.get("shipping", 0))
            row["net"] = row["net_sale"] - row["commission"] - row["shipping"]
        out.append(row)
    return out


def message(rows: list[dict], now_days: list[date], prev_days: list[date], rates: dict) -> str:
    # narrow tables (28 characters at most) instead of one wide one: no wrapping on a phone
    t = [f"{'Channel':<11}{'Value':>8}{'±':>6}"]
    u = [f"{'Channel':<11}{'Units':>6}{'±':>6}{'Cn%':>5}"]
    for r in rows:
        t.append(f"{r['channel'][:11]:<11}{rupees(r['value']):>8}{change(round(r['value']), round(r['value_prev'])):>6}")
        u.append(f"{r['channel'][:11]:<11}{r['units']:>6}{change(r['units'], r['units_prev']):>6}{r['cancel_pct']:>5}")
    tv, tp = sum(r["value"] for r in rows), sum(r["value_prev"] for r in rows)
    t.append(f"{'Total':<11}{rupees(tv):>8}{change(round(tv), round(tp)):>6}")
    tu, tup = sum(r["units"] for r in rows), sum(r["units_prev"] for r in rows)
    u.append(f"{'Total':<11}{tu:>6}{change(tu, tup):>6}")
    out = [f"📑 <b>Monthly MIS · {now_days[0]:%B %Y}</b>",
           f"<i>against {prev_days[0]:%B}; order value by order date (OMSGuru), cancelled included in Value</i>",
           "", "<b>Order value</b>",
           "<pre>" + telegram.esc("\n".join(t)) + "</pre>",
           f"Sale after cancels: <b>{rupees(sum(r['net_sale'] for r in rows))}</b>",
           "", "<b>Units</b> (Cn% = cancelled)",
           "<pre>" + telegram.esc("\n".join(u)) + "</pre>"]
    priced = [r for r in rows if "net" in r]
    if priced:
        def lakh(v: float) -> str:
            return f"{v / 1e5:.1f}L" if abs(v) >= 1e5 else f"{v / 1e3:.0f}K"
        n = [f"{'Channel':<11}{'Sale':>6}{'Comm':>5}{'Net':>6}"]
        n += [f"{r['channel'][:11]:<11}{lakh(r['net_sale']):>6}{lakh(r['commission']):>5}{lakh(r['net']):>6}"
              for r in priced]
        out += ["", "<b>After commission and shipping</b> (MIS_RATES)", "<pre>" + telegram.esc("\n".join(n)) + "</pre>",
                f"Shipping: {rupees(sum(r['shipping'] for r in priced))} · Net = Sale - Comm - Shipping"]
    missing = [r["channel"] for r in rows if "net" not in r]
    if missing:
        out.append(f"<i>No commission / shipping rate for: {telegram.esc(', '.join(missing))} "
                   "(set the MIS_RATES repo variable)</i>")
    return "\n".join(out)


def run(args) -> int:
    today = date.fromisoformat(args.day) if args.day else date.today()
    now_days, prev_days = months(today)
    try:
        rates = json.loads(env("MIS_RATES") or "{}")
    except ValueError:
        rates = {}
        log.warning("MIS_RATES is not valid JSON: rates left out")
    client = OmsGuruClient.from_env()
    client.login()
    lines, names = order_lines(client, prev_days + now_days)
    rows = mis(lines, names, now_days, prev_days, rates)
    text = message(rows, now_days, prev_days, rates)
    try:
        from ultron.core import llm
        plain = text.replace("<pre>", "").replace("</pre>", "").replace("<b>", "").replace("</b>", "")
        plain = plain.replace("<i>", "").replace("</i>", "")
        note = (llm.chat([{"role": "system", "content": PLAN}, {"role": "user", "content": plain}],
                         max_tokens=2500)["choices"][0]["message"].get("content") or "").strip()
        if note:
            text += "\n\n🧭 <b>Plan for this month</b>\n" + telegram.md(note)
    except Exception as exc:  # noqa: BLE001
        log.warning("No plan: %s", exc)
    log.info("MIS: %d channels, %d order lines", len(rows), len(lines))
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py mis", description="Monthly MIS and this month's plan")
    p.add_argument("--day", help="a day of the month after the one reported, YYYY-MM-DD (default today)")
    p.add_argument("--no-telegram", action="store_true", help="print the MIS instead of sending it")
    return run_bot("Monthly MIS", p, run, argv)
