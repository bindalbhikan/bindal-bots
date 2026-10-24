"""Evening summary (8 PM): what the bots did today, today's sale so far against the same weekday
last week, the orders still due today and not ready to ship, and a few lines from the agent's
free model on what to watch tomorrow. Telegram only, nothing written.
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from datetime import date, datetime, timedelta

from ultron.integrations import github

from ..core import telegram
from ..core.app import run_bot
from ..core.config import num
from ..morning.pipeline import channel_line, change
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import CANCELLED, order_lines
from ..sla.pipeline import at_risk

log = logging.getLogger("bindal.evening")

# workflow file -> what the summary calls it; the order they are listed in
BOTS = {"morning.yml": "Morning report", "stock.yml": "Stock-out alert", "sla.yml": "SLA warning",
        "sale.yml": "Sale report", "vms.yml": "Myntra VMS", "ajio-tat.yml": "AJIO TAT",
        "labels.yml": "Label check", "ajio-spf.yml": "AJIO SPF", "myntra-spf.yml": "Myntra SPF",
        "returns.yml": "Return analysis", "ajio-ads.yml": "AJIO ads", "inv-push.yml": "Inventory push",
        "myntra-reports.yml": "Myntra reports", "agent-watch.yml": "Agent checks"}

COMMENT = """You are the e-commerce manager of Bindal Exports (women's apparel on Myntra, AJIO,
Flipkart, Amazon, Meesho, Nykaa). Below is today's evening summary: how the automated reports ran,
today's sale so far against the same weekday last week, and orders still due today. Write 2 or 3
short lines for the owner in plain English: what went well or badly today and the one or two
things to watch tomorrow. Numbers exact, no markdown, no greeting; say only what the data shows."""


def runs_today(today: date) -> list[tuple[str, int, int, list[str]]]:
    """(bot, runs today, failed, failed run links) for every bot that ran today."""
    out = []
    for wf, name in BOTS.items():
        try:
            runs = [r for r in github.runs(wf, 30) if _local_day(r["started"]) == today]
        except Exception as exc:  # noqa: BLE001 - a workflow not there yet
            log.warning("%s: runs not read (%s)", wf, exc)
            continue
        done = [r for r in runs if r["status"] == "completed"]
        failed = [r for r in done if r["conclusion"] not in ("success", "skipped", "cancelled")]
        if done:
            out.append((name, len(done), len(failed), [r["url"] for r in failed]))
    return out


def _local_day(started: str | None) -> date | None:
    """GitHub times are UTC; the day in IST."""
    if not started:
        return None
    return (datetime.fromisoformat(started.replace("Z", "+00:00")) + timedelta(hours=5, minutes=30)).date()


def sale_table(lines: list[dict], names: dict[str, str], today: date, before: date, until: datetime) -> tuple[str, int, int]:
    """Today's units so far per channel against the same weekday last week up to the same time."""
    cut = until.time()
    units: Counter = Counter()
    cancel = 0
    for l in lines:
        if l["day"] not in (today, before) or l["ordered"].time() > cut:
            continue
        units[(channel_line(names.get(l["channel"], l["channel"])), l["day"])] += l["qty"]
        if l["day"] == today and CANCELLED.match(l["status"]):
            cancel += l["qty"]
    chans = sorted({c for c, _ in units}, key=lambda c: -units[(c, today)])
    rows = [f"{'Channel':<11}{format(today, '%d%b'):>6}{format(before, '%d%b'):>6}{'±':>5}"]   # 28 wide: a phone
    t_now = t_before = 0
    for c in chans:
        now, prev = units[(c, today)], units[(c, before)]
        t_now, t_before = t_now + now, t_before + prev
        rows.append(f"{c[:11]:<11}{now:>6}{prev:>6}{change(now, prev):>5}")
    rows.append(f"{'Total':<11}{t_now:>6}{t_before:>6}{change(t_now, t_before):>5}")
    return "\n".join(rows), t_now, cancel


def run(args) -> int:
    now = datetime.now()
    today, before = now.date(), now.date() - timedelta(days=7)
    client = OmsGuruClient.from_env()
    client.login()
    days = sorted({today - timedelta(days=i) for i in range(int(num("SLA_ORDER_DAYS", 8)))} | {before})
    lines, names = order_lines(client, days)
    table, total, cancel = sale_table(lines, names, today, before, now)
    risk = at_risk(lines, now)
    over = sum(1 for r in risk if r["sla"] < now)

    bots = runs_today(today)
    bot_lines = [f"{'❌' if f else '✅'} {telegram.esc(n)}: {k} run{'s' if k != 1 else ''}"
                 + (f", {f} failed" if f else "") for n, k, f, _ in bots]
    failed_links = [u for _, _, f, urls in bots for u in urls]

    out = [f"🌙 <b>Evening summary · {today:%a %d %b}</b>", "",
           f"<b>Sale so far</b> (units by {now:%I:%M %p}, against last {before:%a} at the same time)",
           "<pre>" + telegram.esc(table) + "</pre>",
           f"Cancelled today: {cancel}" + (f" ({cancel * 100 // total}%)" if total else ""), "",
           f"<b>Due today, not ready to ship:</b> {len(risk)} (SLA passed {over})", "",
           "<b>Bots today</b>", *(bot_lines or ["no runs found"]),
           *([f'<a href="{u}">failed run</a>' for u in failed_links[:5]])]
    try:
        from ultron.core import llm
        facts = (f"Sale so far today vs last week same time:\n{table}\nCancelled today {cancel}\n"
                 f"Due today not ready to ship {len(risk)}, SLA passed {over}\n"
                 f"Bots: {len(bots)} bots, {sum(k for _, k, _, _ in bots)} runs in all, "
                 f"{sum(f for _, _, f, _ in bots)} failed\n"
                 + "\n".join(f"{n}: {k} runs, {f} failed" for n, k, f, _ in bots))
        note = (llm.chat([{"role": "system", "content": COMMENT}, {"role": "user", "content": facts}],
                         max_tokens=1500)["choices"][0]["message"].get("content") or "").strip()
        if note:
            out += ["", "🧠 " + telegram.md(note)]
    except Exception as exc:  # noqa: BLE001 - the summary goes out without the lines
        log.warning("No comment: %s", exc)
    text = "\n".join(out)
    log.info("Evening: %d units today, %d due not RTS, %d bots, %d failed runs", total, len(risk), len(bots), len(failed_links))
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py evening", description="8 PM summary of the day")
    p.add_argument("--no-telegram", action="store_true", help="print the summary instead of sending it")
    return run_bot("Evening summary", p, run, argv)
