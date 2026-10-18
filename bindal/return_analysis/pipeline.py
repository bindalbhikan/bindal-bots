"""Monthly sale vs return analysis: Myntra MAIN / DBCC / FMCC, Ajio, Flipkart -> Drive -> Telegram.

On the 15th at 2:00 PM IST (worker cron) for the month before: sale = that month, returns =
its 1st to the 14th of this month. Inputs are fetched from the portals (sources.py), the reports
are built with the rules of the hand-run tool (engine.py) and written to the Drive folder
RETURN ANALYSIS: Output/<MON-YY>/ (reports) and Template/ (flat CSVs). A portal whose input
cannot be fetched is left out and named on Telegram; --drive-input uses the files in the Drive
folder's Input/ instead (dropped there by hand). --request (9:00 AM on the 15th) only asks the
portals for the slow reports, so the 2 PM run finds them ready.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from ..core import dashboard, telegram
from ..core.app import run_bot
from ..core.config import env, num, output_dir
from ..core.drive import DriveFolder
from . import sources

log = logging.getLogger("bindal.returns")

DEFAULT_FOLDER = "1FQMg6atQgB1v6fGW1aIWoaZp5yFoM9bx"     # BINDAL EXPORTS/RETURN ANALYSIS
MYNTRA_ACCOUNTS = ["MAIN", "DBCC", "FMCC"]
SHORT = {"MYNTRA MAIN": "Myn MAIN", "MYNTRA DBCC": "Myn DBCC", "MYNTRA FMCC": "Myn FMCC", "AJIO": "Ajio",
         "FLIPKART": "Flipkart", "MYNTRA COMBINED": "Myntra", "ALL PORTAL": "ALL"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def report_month(value: str, today: date) -> date:
    """First day of the report month: YYYY-MM, else the month before today."""
    if value:
        return datetime.strptime(value, "%Y-%m").date()
    first = today.replace(day=1)
    return (first.replace(year=first.year - 1, month=12) if first.month == 1
            else first.replace(month=first.month - 1))


def _short_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:120]}" if not str(exc).strip() else str(exc)[:150]


# ------------------------------------------------------------------ inputs

def fetch_inputs(inputs: Path, sale, ret, parts, notes: list[str], alerts: list[str]) -> None:
    """Every portal into inputs/, side by side. A failed portal leaves no folder (engine skips it)."""
    from ..sale import myntra_portal as mp
    wait = num("RETURNS_WAIT_MINUTES", 45)
    folders = {f"MYNTRA {a}": inputs / "Myntra" / a for a in MYNTRA_ACCOUNTS}
    folders.update(AJIO=inputs / "Ajio", FLIPKART=inputs / "Flipkart")
    tasks = {f"MYNTRA {a}": (sources.fetch_myntra, (a, sale, parts, folders[f"MYNTRA {a}"], wait))
             for a in MYNTRA_ACCOUNTS}
    tasks["AJIO"] = (sources.fetch_ajio, (sale, ret, folders["AJIO"]))
    tasks["FLIPKART"] = (sources.fetch_flipkart, (sale, ret, folders["FLIPKART"], wait))
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {name: pool.submit(fn, *args) for name, (fn, args) in tasks.items()}
    for name, fut in futures.items():
        folder = folders[name]
        try:
            log.info("%s inputs: %s", name, fut.result())
        except mp.SessionExpired as exc:
            alerts.append(f"Myntra {name.split()[-1]} session expired ({exc})")
            notes.append(f"{name} left out: session expired")
            shutil.rmtree(folder, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 - one portal never stops the others
            log.exception("%s inputs failed", name)
            notes.append(f"{name} left out: {_short_error(exc)}")
            shutil.rmtree(folder, ignore_errors=True)


def drive_inputs(root: DriveFolder, inputs: Path) -> None:
    """The Drive folder's Input/ tree (files dropped by hand) into inputs/."""
    src = root.subfolder("Input")
    if not src:
        raise RuntimeError("the Drive folder has no Input folder")
    for portal, subs in [("Myntra", MYNTRA_ACCOUNTS), ("Ajio", [None]), ("Flipkart", [None])]:
        folder = src.subfolder(portal)
        for sub in subs:
            part = folder.subfolder(sub) if folder and sub else folder
            if part:
                part.download_all(inputs / portal / sub if sub else inputs / portal)


# ------------------------------------------------------------------ telegram

def summary_text(results: list[dict], sale, ret, notes: list[str]) -> str:
    lines = [f"{'Portal':<9}{'Sale':>6}{'Ret':>5}{'%':>5}"]
    for r in results:
        if r["portal"] == "MYNTRA COMBINED" or r["portal"] == "ALL PORTAL":
            lines.append("-" * 25)
        lines.append(f"{SHORT.get(r['portal'], r['portal'])[:9]:<9}{r['sold']:>6}{r['returned']:>5}{r['rate']:>5.1f}")
    tag = results[0]["month"] if results else f"{sale[0]:%b-%y}".upper()
    out = [f"📦 <b>Return Analysis · {telegram.esc(tag)}</b>",
           f"<i>sale {sale[0]:%d %b} - {sale[1]:%d %b} · returns {ret[0]:%d %b} - {ret[1]:%d %b}</i>", "",
           "<pre>" + telegram.esc("\n".join(lines)) + "</pre>", "",
           f"📁 Drive: RETURN ANALYSIS / Output / {telegram.esc(tag)}"]
    if notes:
        out += ["", "⚠️ <b>Notes</b>", *(f"• {telegram.esc(n)}" for n in notes)]
    return "\n".join(out)


def yearly_text(results: list[dict], year: int, notes: list[str], heading: str = "") -> str:
    """The year's table for Telegram (same layout as the monthly one)."""
    lines = [f"{'Portal':<9}{'Sale':>6}{'Ret':>5}{'%':>5}"]
    for r in results:
        if r["portal"] in ("MYNTRA COMBINED", "ALL PORTAL"):
            lines.append("-" * 25)
        lines.append(f"{SHORT.get(r['portal'], r['portal'])[:9]:<9}{r['sold']:>6}{r['returned']:>5}{r['rate']:>5.1f}")
    months = results[0].get("months", []) if results else []
    span = (f"{datetime.strptime(months[0], '%m-%Y'):%b} - {datetime.strptime(months[-1], '%m-%Y'):%b %Y}"
            if months else f"{year}")
    out = [f"📅 <b>{heading or 'Yearly Return Analysis'} · {year}</b>", f"<i>{span} ({len(months)} months)</i>", "",
           "<pre>" + telegram.esc("\n".join(lines)) + "</pre>", "",
           f"📁 Drive: RETURN ANALYSIS / Output / {year}"]
    if notes:
        out += ["", "⚠️ <b>Notes</b>", *(f"• {telegram.esc(n)}" for n in notes)]
    return "\n".join(out)


def send_yearly(args, results: list[dict], year: int, notes: list[str], heading: str = "") -> None:
    text = yearly_text(results, year, notes, heading)
    all_portal = next((r["report"] for r in results if r["portal"] == "ALL PORTAL"), None)
    if args.no_telegram:
        print(text)
        return
    telegram.send(text)
    if all_portal:
        telegram.send_document(all_portal, f"<b>{telegram.esc(Path(all_portal).stem)}</b>")


def run_backfill(args, root: DriveFolder, work: Path) -> int:
    """One-time build of a year's report: every month of the year whose returns window is over is
    fetched and built (its data kept for the yearly report), then the year is built."""
    from . import yearly
    year = int(args.backfill_year)
    notes: list[str] = []
    filled, results = yearly.backfill(root, year, work / "Supporting", work, date.today(), notes)
    if not results:
        raise RuntimeError(f"yearly report {year} could not be built: " + "; ".join(notes[:10]))
    keep = output_dir("returns")
    for r in results:
        shutil.copy2(r["report"], keep)
    notes.insert(0, "months built now: " + ", ".join(f"{m:%b}" for m in filled))
    send_yearly(args, results, year, notes, "Yearly Return Analysis (first build)")
    dashboard.publish("returns_year", {"year": year, "notes": notes,
                                       "portals": [{k: r[k] for k in ("portal", "sold", "returned", "rate")}
                                                   for r in results]})
    return 0


# ------------------------------------------------------------------ runs

def run_request(args, sale, ret, parts) -> int:
    """9:00 AM on the 15th: ask Myntra (orders + first returns report per account) and Flipkart
    for the reports, so the 2 PM run only collects them. Telegram hears only about trouble."""
    from ..sale import myntra_portal as mp
    from ..sale.pipeline import session_alert
    problems, alerts = [], []
    for account in MYNTRA_ACCOUNTS:
        try:
            for line in sources.request_myntra(account, sale, parts):
                log.info(line)
        except mp.SessionExpired as exc:
            alerts.append(f"Myntra {account} session expired ({exc})")
        except Exception as exc:  # noqa: BLE001
            log.exception("Myntra %s request failed", account)
            problems.append(f"Myntra {account}: {_short_error(exc)}")
    try:
        for line in sources.request_flipkart(sale, ret):
            log.info(line)
    except Exception as exc:  # noqa: BLE001
        log.exception("Flipkart request failed")
        problems.append(f"Flipkart: {_short_error(exc)}")
    msgs = [session_alert(alerts)] if alerts else []
    if problems:
        msgs.append("📦 <b>Return Analysis · report request</b>\n<i>the 2 PM run will try again</i>\n\n"
                    + "\n".join(f"• {telegram.esc(p)}" for p in problems))
    for m in msgs:
        print(m) if args.no_telegram else telegram.send(m)
    return 0


def run(args) -> int:
    today = date.today()
    month = report_month(args.month, today)
    sale, ret, parts = sources.windows(month, today) if not args.backfill_year else (None, None, None)
    if args.backfill_year:
        log.info("Backfill of the yearly report %s", args.backfill_year)
    else:
        log.info("Report month %s: sale %s..%s, returns %s..%s (%d returns job(s) per Myntra account)",
                 f"{month:%b %Y}", *sale, *ret, len(parts))
    if args.request:
        return run_request(args, sale, ret, parts)

    work = Path(tempfile.mkdtemp(prefix="returns_"))
    root = DriveFolder(env("RETURN_DRIVE_FOLDER_ID") or DEFAULT_FOLDER)
    supporting = root.subfolder("Supporting")
    if not supporting or not supporting.download("MasterSheet.xlsx", work / "Supporting" / "MasterSheet.xlsx"):
        raise RuntimeError("MasterSheet.xlsx not found in RETURN ANALYSIS/Supporting on Drive")
    supporting.download("Cost Sheet.xlsx", work / "Supporting" / "Cost Sheet.xlsx")   # optional
    if args.backfill_year:
        return run_backfill(args, root, work)

    notes, alerts = [], []
    inputs = work / "Input"
    if args.drive_input:
        drive_inputs(root, inputs)
        notes.append("inputs taken from the Drive Input folder")
    else:
        fetch_inputs(inputs, sale, ret, parts, notes, alerts)

    from .engine import build_all
    results, skipped = build_all(str(inputs), str(work / "Supporting"), str(work / "out"))
    for portal, reason in skipped:
        if not any(n.startswith(portal) for n in notes):
            notes.append(f"{portal} left out: {reason}")
    if not results:
        raise RuntimeError("no portal could be built: " + "; ".join(notes))

    tag = results[0]["month"]
    keep = output_dir("returns")
    for r in results:
        shutil.copy2(r["report"], keep)
    if args.no_upload:
        notes.append("not uploaded to Drive (test run): output/returns")
    else:
        out_folder = root.subfolder("Output", create=True).subfolder(tag, create=True)
        template_folder = root.subfolder("Template", create=True)
        for r in results:
            out_folder.put(Path(r["report"]), XLSX)
            if r["template"]:
                template_folder.put(Path(r["template"]), "text/csv")
    if inputs.exists():
        shutil.copytree(inputs, keep / "Input", dirs_exist_ok=True)
    log.info("Return analysis %s: %d reports%s", tag, len(results), "" if args.no_upload else " written to Drive")

    from ..sale.pipeline import session_alert
    text = summary_text(results, sale, ret, notes)
    all_portal = next((r["report"] for r in results if r["portal"] == "ALL PORTAL"), None)
    if args.no_telegram:
        print(text)
    else:
        telegram.send(text)
        if alerts:
            telegram.send(session_alert(alerts))
        if all_portal:
            telegram.send_document(all_portal, f"<b>{telegram.esc(Path(all_portal).stem)}</b>")
    if not args.no_upload:
        from . import yearly
        try:
            yearly.save_month_data(root, [r for r in results if r.get("df") is not None], work)
            year_results = yearly.rebuild_year(root, month.year, work / "Supporting", work)
            if year_results:
                send_yearly(args, year_results, month.year, [])
        except Exception as exc:  # noqa: BLE001 - the monthly report is already out
            log.exception("Yearly report not updated")
            msg = f"⚠️ <b>Yearly return analysis {month.year} not updated</b>\n\n<code>{telegram.esc(str(exc)[:300])}</code>"
            print(msg) if args.no_telegram else telegram.send(msg)
    dashboard.publish("returns", {"month": tag, "sale": sale, "returns": ret, "notes": notes,
                                  "portals": [{k: r[k] for k in ("portal", "sold", "returned", "rate")}
                                              for r in results]})
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py returns", description="Monthly sale vs return analysis")
    p.add_argument("--month", default="", metavar="YYYY-MM", help="report month (default: last month)")
    p.add_argument("--request", action="store_true",
                   help="only ask the portals for the slow reports (morning of the 15th)")
    p.add_argument("--drive-input", action="store_true",
                   help="use the files in the Drive folder's Input/ instead of fetching them")
    p.add_argument("--backfill-year", metavar="YYYY",
                   help="one-time build of that year's yearly report: fetch and build every finished month")
    p.add_argument("--no-upload", action="store_true", help="keep the reports in output/returns only (test run)")
    p.add_argument("--no-telegram", action="store_true", help="print the summary instead of sending it")
    return run_bot("Return analysis", p, run, argv, key="returns")
