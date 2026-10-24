"""Morning report (9:30 AM): yesterday's sale per channel against the same weekday a week before,
with cancels, on Telegram, and a few lines on it from the agent's free model.

Every OMSGuru channel on its own line (the Myntra accounts apart), plus Flipkart FBF (Seller Hub)
and Myntra SJIT (partner portal report), as in the sale report. Nothing is written anywhere.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from ultron.core.config import channel_line  # noqa: F401 - other bots import it from here

from ..core import telegram
from ..core.app import run_bot
from ..core.config import num
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import units_by_channel
from ..sale.pipeline import fbf_source, sjit_source

log = logging.getLogger("bindal.morning")

ORDER = ["Myntra MAIN", "Myntra PPMP", "Myntra DBCC", "Myntra FMCC", "SJIT", "Ajio", "Flipkart", "FBF",
         "Amazon", "Amazon SC", "COCOBLU", "Meesho", "Nykaa"]

COMMENT = """You are the e-commerce manager of Bindal Exports (women's apparel on Myntra, AJIO,
Flipkart, Amazon, Meesho, Nykaa). Below is yesterday's sale in units per channel against the same
weekday one week before, with cancelled units. Write 2 or 3 short lines for the owner in plain
English: the biggest change and on which channel, anything worrying (a channel at
or near zero, cancels high), and one thing to check today. Numbers exact, no markdown, no greeting.
Say only what the numbers show; do not guess causes you cannot see."""


def oms_counts(days: list[date]) -> tuple[dict, dict]:
    """{line: {day: units}}, {line: {day: cancelled units}} for the OMSGuru channels."""
    client = OmsGuruClient.from_env()
    client.login()
    units, cancel, names = units_by_channel(client, days)
    gross: dict = {}
    canc: dict = {}
    for (day, chid), qty in units.items():
        line = channel_line(names.get(chid, chid or "(blank)"))
        gross.setdefault(line, {}).setdefault(day, 0)
        gross[line][day] += qty
        canc.setdefault(line, {}).setdefault(day, 0)
        canc[line][day] += cancel[(day, chid)]
    return gross, canc


def change(now: int, before: int) -> str:
    if not before:
        return "new" if now else "-"
    pct = round((now - before) * 100 / before)
    return f"{pct:+d}%"


def table(gross: dict, canc: dict, day: date, before: date) -> str:
    lines = sorted(gross, key=lambda n: (ORDER.index(n) if n in ORDER else len(ORDER), n))
    out = [f"{'Channel':<11}{format(day, '%d%b'):>6}{format(before, '%d%b'):>6}{'±':>5}"]   # 28 wide: a phone
    tot_now = tot_before = tot_cancel = 0
    for n in lines:
        now, prev = gross[n].get(day, 0), gross[n].get(before, 0)
        if not now and not prev:
            continue
        tot_now, tot_before, tot_cancel = tot_now + now, tot_before + prev, tot_cancel + canc.get(n, {}).get(day, 0)
        out.append(f"{n[:11]:<11}{now:>6}{prev:>6}{change(now, prev):>5}")
    out.append(f"{'Total':<11}{tot_now:>6}{tot_before:>6}{change(tot_now, tot_before):>5}")
    pct = f" ({tot_cancel * 100 // tot_now}%)" if tot_now else ""
    return "<pre>" + telegram.esc("\n".join(out)) + "</pre>" + f"\nCancelled yesterday: <b>{tot_cancel}</b>{pct}"


def drops(gross: dict, day: date, before: date) -> list[str]:
    """Sudden-drop alerts: a channel that sold DROP_MIN_UNITS (20)+ units on the same weekday last
    week and sold nothing yesterday, or fell by DROP_PCT (40) % or more."""
    least, pct = num("DROP_MIN_UNITS", 20), num("DROP_PCT", 40)
    out = []
    for n in sorted(gross, key=lambda n: -gross[n].get(before, 0)):
        now, prev = gross[n].get(day, 0), gross[n].get(before, 0)
        if prev < least:
            continue
        if now == 0:
            out.append(f"🔴 {n}: no sale yesterday ({prev} on {before:%a}) - check the listing / account")
        elif (prev - now) * 100 >= pct * prev:
            out.append(f"⚠️ {n}: down {round((prev - now) * 100 / prev)}% ({prev} → {now})")
    return out


def comment(text_table: str) -> str:
    from ultron.core import llm
    try:
        data = llm.chat([{"role": "system", "content": COMMENT}, {"role": "user", "content": text_table}],
                        max_tokens=1500)
        return (data["choices"][0]["message"].get("content") or "").strip()
    except llm.LLMError as exc:
        log.warning("No comment: %s", exc)
        return ""


def run(args) -> int:
    day = date.fromisoformat(args.day) if args.day else date.today() - timedelta(days=1)
    before = day - timedelta(days=7)
    notes: list[str] = []
    gross, canc = oms_counts([before, day])

    try:
        fg, fc = fbf_source([before, day])
        gross["FBF"] = {before: fg[before], day: fg[day]}
        canc["FBF"] = {before: fc[before], day: fc[day]}
    except Exception as exc:  # noqa: BLE001 - the rest still goes out
        log.warning("FBF skipped: %s", exc)
        notes.append(f"FBF not read ({str(exc)[:60]})")
    try:
        alerts: list[str] = []
        sg, sc, covered = sjit_source([before, day], notes, alerts)
        if day in covered:
            gross["SJIT"] = {before: sg[before], day: sg[day]}
            canc["SJIT"] = {before: sc[before], day: sc[day]}
        else:
            notes.append("SJIT report for yesterday not ready yet")
        notes += alerts
    except Exception as exc:  # noqa: BLE001
        log.warning("SJIT skipped: %s", exc)
        notes.append(f"SJIT not read ({str(exc)[:60]})")

    body = table(gross, canc, day, before)
    alerts = drops(gross, day, before)
    if alerts:
        body += "\n\n" + "\n".join(telegram.esc(a) for a in alerts)
    plain = body.replace("<pre>", "").replace("</pre>", "").replace("<b>", "").replace("</b>", "")
    note = comment(f"Yesterday {day:%a %d %b}, compared with {before:%a %d %b}:\n{plain}") if not args.no_comment else ""
    text = "\n".join([f"☀️ <b>Morning report · {day:%a %d %b}</b>",
                      f"<i>yesterday vs last {before:%a} ({before:%d %b}), units</i>", "", body,
                      *(["", "🧠 " + telegram.md(note)] if note else []),
                      *(["", "⚠️ " + "\n⚠️ ".join(telegram.esc(n) for n in notes)] if notes else [])])
    log.info("Morning report: %s", {n: v.get(day, 0) for n, v in gross.items()})
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py morning", description="Morning sale report on Telegram")
    p.add_argument("--day", help="report day YYYY-MM-DD (default yesterday)")
    p.add_argument("--no-comment", action="store_true", help="leave out the model's lines")
    p.add_argument("--no-telegram", action="store_true", help="print the report instead of sending it")
    return run_bot("Morning report", p, run, argv)
