"""Daily sale report: OMSGuru (all channels) + Flipkart FBF + Myntra SJIT -> SALE REPORT.xlsx -> Telegram.

What is written for each report day:
  OMS blocks (AZ/FX/FBA, MEESHO, AJIO B2C, FLIPKART, MYNTRA, NYKAA, SNAPDEAL): GROSS by order date
  FBF : GROSS and Cancel from Flipkart Seller Hub (Flipkart warehouses only)
  SJIT: GROSS and Cancel from the Myntra Seller_Orders_Report, fetched from the partner portal
        with a saved login session and/or dropped by hand into the SJIT Drive folder;
        when no report covers the day, SJIT is skipped (0 written only into empty cells)
RETURN is entered by hand and never touched. A source that fails is skipped and
reported on Telegram; the rest of the report is still written.
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from ..core import dashboard, telegram
from ..core.app import run_bot
from ..core.config import ROOT, csv_list, env, num, output_dir
from ..core.drive import DriveFile
from ..core.files import replace_atomically
from ..portals.omsguru import OmsGuruClient
from .oms_sales import OMS_BLOCKS, gross_by_day
from .workbook import SaleWorkbook

BOOK_NAME = "SALE REPORT.xlsx"
REPORT_ORDER = ["AZ/FX/FBA", "COCOBLU", "MEESHO", "AJIO B2C", "FLIPKART", "FBF", "MYNTRA", "SJIT", "NYKAA"]
TODAY_ORDER = [b for b in REPORT_ORDER if b != "SJIT"]  # the SJIT report has no data for today
SHORT = {"AZ/FX/FBA": "Amazon", "COCOBLU": "Cocoblu", "AJIO B2C": "Ajio", "FBF": "FBF", "SJIT": "SJIT"}
log = logging.getLogger("bindal.sale")


def report_days(args) -> list[date]:
    today = date.today()
    if args.from_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else today - timedelta(days=1)
    else:
        end = today - timedelta(days=1)
        start = end - timedelta(days=args.days - 1)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ------------------------------------------------------------------ sources

def oms_source(days):
    client = OmsGuruClient.from_env()
    client.login()
    return gross_by_day(client, days)


def fbf_source(days):
    from ..portals.flipkart import DEFAULT_FBF_LOCATIONS, FlipkartClient
    user, pwd = env("FLIPKART_EMAIL"), env("FLIPKART_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("FLIPKART_EMAIL / FLIPKART_PASSWORD not set")
    fk = FlipkartClient(user, pwd)
    fk.login()
    locations = csv_list("FBF_LOCATIONS") or DEFAULT_FBF_LOCATIONS
    return fk.fbf_units(days, locations)


def sjit_source(days: list[date], notes: list[str], alerts: list[str]):
    """Myntra SJIT: the Seller_Orders_Report fetched from the partner portal with the saved
    session (MYNTRA_SESSION_JSON), plus any report dropped by hand into the SJIT Drive folder.
    The portal report wins for the lines it holds; every day it was requested for counts as
    covered. Session trouble goes into `alerts` (sent on Telegram as its own message)."""
    from ..portals import myntra as mp
    from . import sjit
    files: list[tuple[str, bytes]] = []
    fetched: set[date] = set()
    portal = myntra_portal(alerts)
    if portal and days[-1] < date.today():
        try:
            extra_from = days[-1] - timedelta(days=num("SJIT_FETCH_DAYS", 30) - 1)
            files, fetched = portal.reports(days, extra_from, wait_minutes=num("SJIT_WAIT_MINUTES", 20))
            for name, data in files:
                (output_dir("sale") / name).write_bytes(data)
            mp.save_to_worker(env, portal.cookies())
        except mp.SessionExpired as exc:
            alerts.append(f"Myntra portal session expired ({exc})")
        except Exception as exc:  # noqa: BLE001 - the dropped reports still count
            log.warning("SJIT portal fetch failed: %s", exc)
            notes.append(f"SJIT portal fetch failed ({str(exc)[:80]})")

    folder = env("SJIT_DRIVE_FOLDER_ID")
    try:
        if folder:
            files += sjit.files_from_drive(folder)
        else:
            local = Path(env("SJIT_LOCAL_DIR") or ROOT / "sjit")
            files += [(p.name, p.read_bytes()) for p in sorted(local.glob("*"), key=lambda p: p.stat().st_mtime,
                                                               reverse=True)
                      if p.suffix.lower() in (".csv", ".zip")] if local.is_dir() else []
    except Exception as exc:  # noqa: BLE001
        if not files:
            raise
        log.warning("SJIT drop folder not read: %s", exc)
    if not files:
        raise RuntimeError("no SJIT report file found")
    gross, cancel, covered = sjit.parse(files)
    return gross, cancel, covered | fetched


def myntra_portal(alerts: list[str]):
    """The partner portal client with the saved session, checked; None (with the reason in
    `alerts` when it is session trouble) when there is no working session."""
    from ..portals import myntra as mp
    text = mp.session_text(env, ROOT / "myntra_session.json")
    if not text:
        log.info("No Myntra session saved: SJIT only from dropped reports")
        return None
    try:
        session = mp.parse_session(text)
        portal = mp.MyntraPortal(session)
        portal.check()
    except mp.SessionExpired as exc:
        alerts.append(f"Myntra portal session expired ({exc})")
        return None
    warning = mp.expiry_warning(session, num("MYNTRA_SESSION_WARN_DAYS", 3),
                                life_days=num("MYNTRA_SESSION_LIFE_DAYS", 15))
    if warning:
        alerts.append(warning)
    return portal


def run_sjit_request(args) -> int:
    """Morning run: ask the partner portal for the Seller_Orders_Report of the coming sale
    run's days, so it is ready by then. Reports already there are not asked for again.
    Telegram only hears about session trouble."""
    from ..portals import myntra as mp
    days = report_days(args)
    alerts: list[str] = []
    portal = myntra_portal(alerts)
    if portal:
        job = portal.request(days)
        log.info("SJIT report %s for %s..%s", f"job {job.get('id')} requested" if job else "already on the portal",
                 days[0], days[-1])
        mp.save_to_worker(env, portal.cookies())
    keep_other_accounts_alive(alerts)
    if alerts:
        print(session_alert(alerts)) if args.no_telegram else telegram.send(session_alert(alerts))
    return 0


def session_alert(alerts: list[str]) -> str:
    return "\n".join([
        "🔐 <b>Myntra partner portal session</b>",
        *(f"• {telegram.esc(a)}" for a in alerts),
        "",
        "Renew it: log in on the Myntra partner portal with that account, copy the <code>cookie:</code> "
        "request header (DevTools > Network > a partnersapi request), then run "
        "<code>python scripts/myntra_session.py ACCOUNT</code> (MAIN / DBCC / FMCC).",
    ])


def keep_other_accounts_alive(alerts: list[str]) -> None:
    """Use the saved DBCC / FMCC sessions once (only the monthly return analysis needs them) and
    save the cookies the portal renews: a session left unused runs out within a day."""
    from ..portals import myntra as mp
    for account in (a for a in mp.ACCOUNTS if a != "MAIN"):
        text = mp.from_worker(env, account)
        if not text:
            continue
        try:
            portal = mp.MyntraPortal(mp.parse_session(text))
            portal.check()
            mp.save_to_worker(env, portal.cookies(), account)
            log.info("Myntra %s session renewed", account)
        except mp.SessionExpired as exc:
            alerts.append(f"Myntra {account} session expired ({exc})")
        except Exception as exc:  # noqa: BLE001 - never stops the sale report
            log.warning("Myntra %s session not checked: %s", account, exc)


# ------------------------------------------------------------------ telegram

def _notes(notes: list[str]) -> list[str]:
    return ["", "⚠️ <b>Notes</b>", *(f"• {telegram.esc(n)}" for n in notes)] if notes else []


def _sale_table(rows: list[tuple[str, int, int | None]]) -> str:
    """Channel | Gross | Cancel, 22 characters wide (fits a phone)."""
    lines = [f"{'Channel':<10}{'Gross':>6}{'Cncl':>6}"]
    lines += [f"{name[:10]:<10}{g:>6}{(c if c else '-'):>6}" for name, g, c in rows]
    lines.append(f"{'Total':<10}{sum(g for _, g, _ in rows):>6}")
    return "<pre>" + telegram.esc("\n".join(lines)) + "</pre>"


def telegram_report(book: SaleWorkbook, day: date, notes: list[str]) -> str:
    rows = [(SHORT.get(b, b.title()), book.read(day, b, "GROSS"), book.read(day, b, "CANCEL")) for b in REPORT_ORDER]
    month = book.month_gross(day)
    mrows = [(SHORT.get(b, b.title()), month.get(b, 0), None) for b in REPORT_ORDER]
    return "\n".join([
        f"📈 <b>Daily Sale · {day:%d %b %Y}</b>",
        "",
        f"<b>📅 {day:%d %b}</b>",
        _sale_table(rows),
        "",
        f"<b>🗓 {day:%B} 1-{day.day}</b>",
        _sale_table(mrows),
        *_notes(notes),
    ])


def today_report(counts: dict[str, int], cancels: dict[str, int], notes: list[str]) -> str:
    rows = [(SHORT.get(b, b.title()), counts.get(b, 0), cancels.get(b)) for b in TODAY_ORDER]
    return "\n".join([
        f"📊 <b>Today's Sale · {datetime.now():%d %b, %I:%M %p}</b>",
        "<i>so far today, by order time</i>",
        "",
        _sale_table(rows),
        *_notes(notes),
    ])


def dashboard_data(book: SaleWorkbook, day: date, notes: list[str]) -> dict:
    """Day-wise GROSS / Cancel per channel for the report day's month and the month before."""
    first = day.replace(day=1)
    prev = (first - timedelta(days=1)).replace(day=1)
    days = []
    for d in (prev + timedelta(days=i) for i in range((day - prev).days + 1)):
        try:
            gross = {SHORT.get(b, b.title()): book.read(d, b, "GROSS") for b in REPORT_ORDER}
            cancel = {SHORT.get(b, b.title()): book.read(d, b, "CANCEL") for b in REPORT_ORDER}
        except Exception:   # noqa: BLE001 - a month without a sheet is just left out
            continue
        days.append({"date": d, "gross": gross, "cancel": cancel})
    return {"day": day, "channels": [SHORT.get(b, b.title()) for b in REPORT_ORDER], "days": days, "notes": notes}


def run_today(args) -> int:
    """Today so far, channel-wise, Telegram only - the workbook is not touched."""
    today = date.today()
    notes: list[str] = []
    gross, unmapped = oms_source([today])
    counts = {b: gross[(today, b)] for b in OMS_BLOCKS}
    cancels: dict[str, int] = {}
    if unmapped:
        notes.append("unmapped OMS channels: " + ", ".join(f"{k} {v}" for k, v in unmapped.items()))
    try:
        fg, fc = fbf_source([today])
        counts["FBF"], cancels["FBF"] = fg[today], fc[today]
    except Exception as exc:
        log.warning("FBF skipped: %s", exc)
        notes.append(f"FBF skipped ({str(exc)[:80]})")
    log.info("Today's GROSS: %s", counts)
    text = today_report(counts, cancels, notes)
    print(text) if args.no_telegram else telegram.send(text)
    dashboard.publish("sale_today", {"at": datetime.now(), "notes": notes,
                                     "gross": {SHORT.get(b, b.title()): counts.get(b, 0) for b in TODAY_ORDER},
                                     "cancel": {SHORT.get(b, b.title()): cancels.get(b, 0) for b in TODAY_ORDER}})
    return 0


def snapshot_month(value: str, default: date) -> tuple[int, int]:
    """'current' -> default's month, 'YYYY-MM' -> that month."""
    if value and value != "current":
        y, m = datetime.strptime(value, "%Y-%m").timetuple()[:2]
        return y, m
    return default.year, default.month


def open_book(args) -> tuple[DriveFile | None, Path]:
    """The one workbook: the Drive file (worked on in a temp folder) or, with --no-drive, SALE_XLSX_PATH."""
    if env("SALE_DRIVE_FILE_ID") and not args.no_drive:
        drive = DriveFile(env("SALE_DRIVE_FILE_ID"))
        return drive, drive.download(Path(tempfile.mkdtemp(prefix="sale_")) / BOOK_NAME, need_edit=True)
    if not env("SALE_XLSX_PATH"):
        raise RuntimeError("Set SALE_DRIVE_FILE_ID, or SALE_XLSX_PATH for a --no-drive run")
    path = Path(env("SALE_XLSX_PATH"))
    if not path.exists():
        raise RuntimeError(f"Workbook not found: {path}")
    return None, path


def run_snapshot(args) -> int:
    """Picture of a whole month sheet, sent to Telegram - the workbook is not changed."""
    from ..core.snapshot import sheet_png
    year, month = snapshot_month(args.snapshot, date.today() - timedelta(days=1))
    _, path = open_book(args)
    sheets = dict(SaleWorkbook(path)._month_sheets())
    ws = sheets.get((year, month))
    if ws is None:
        raise RuntimeError(f"No sheet for {month:02d}-{year} in {BOOK_NAME}")
    png = sheet_png(path, ws.title, output_dir("sale"))
    log.info("Sheet picture: %s", png)
    if not args.no_telegram:
        caption = f"<b>📊 Sale Report · {telegram.esc(ws.title)}</b>"
        telegram.send_file(png, caption)
        telegram.send_file(png, "Full size", as_document=True)  # photos are downscaled by Telegram
    return 0


# ------------------------------------------------------------------ main

def run(args) -> int:
    if args.snapshot:
        return run_snapshot(args)
    if args.today:
        return run_today(args)
    if args.sjit_request:
        return run_sjit_request(args)
    days = report_days(args)
    log.info("Report days: %s", ", ".join(f"{d}" for d in days))
    drive, path = open_book(args)
    book = SaleWorkbook(path)
    notes: list[str] = []

    # OMSGuru: required - without it there is no report
    gross, unmapped = oms_source(days)
    if not sum(gross.values()):
        raise RuntimeError(f"OMSGuru returned no orders for {days[0]} - {days[-1]}; workbook left unchanged")
    for d in days:
        values = {block: gross[(d, block)] for block in OMS_BLOCKS}
        if not book.has_block(d, "COCOBLU"):        # sheets from before Oct 2026: Cocoblu sat inside Amazon
            values["AZ/FX/FBA"] += values.pop("COCOBLU", 0)
        for block, value in values.items():
            book.write(d, block, "GROSS", value)
    log.info("OMS GROSS written: %s", {f"{d}": {b: gross[(d, b)] for b in OMS_BLOCKS} for d in days})
    if unmapped:
        notes.append("unmapped OMS channels: " + ", ".join(f"{k} {v}" for k, v in unmapped.items()))

    # Flipkart FBF: optional
    try:
        fg, fc = fbf_source(days)
        for d in days:
            book.write(d, "FBF", "GROSS", fg[d])
            book.write(d, "FBF", "CANCEL", fc[d])
    except Exception as exc:
        log.warning("FBF skipped: %s", exc)
        notes.append(f"FBF skipped ({str(exc)[:80]})")

    # Myntra SJIT: optional, 0 into empty cells when the report is missing
    alerts: list[str] = []
    try:
        sg, sc, covered = sjit_source(days, notes, alerts)
    except Exception as exc:
        sg, sc, covered = Counter(), Counter(), set()
        log.warning("SJIT report not available: %s", exc)
    keep_other_accounts_alive(alerts)
    missing = [d for d in days if d not in covered]
    for d in days:
        if d in covered:
            book.write(d, "SJIT", "GROSS", sg[d])
            book.write(d, "SJIT", "CANCEL", sc[d])
        else:
            book.write(d, "SJIT", "GROSS", 0, only_if_empty=True)
            book.write(d, "SJIT", "CANCEL", 0, only_if_empty=True)
    if alerts:
        notes += alerts
        print(session_alert(alerts)) if args.no_telegram else telegram.send(session_alert(alerts))
    if missing:
        notes.append("SJIT report not ready for " + ", ".join(f"{d:%d %b}" for d in missing) + " (taken as 0)")

    for ws in {book.month_sheet(d.year, d.month) for d in days}:
        book.repair_formulas(ws)
    replace_atomically(book.save, path)
    if drive:
        drive.upload(path)
    log.info("Sale report saved (%s)", "Drive" if drive else path)

    saved = SaleWorkbook(path)
    text = telegram_report(saved, days[-1], notes)
    print(text) if args.no_telegram else telegram.send(text)
    dashboard.publish("sale", dashboard_data(saved, days[-1], notes))
    for n in notes:
        log.warning(n)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py sale", description="Daily sale report")
    p.add_argument("--days", type=int, default=num("SALE_REPORT_DAYS", 2),
                   help="report days ending yesterday (default 2 = day before yesterday + yesterday)")
    p.add_argument("--from", dest="from_date", help="first day to refresh, YYYY-MM-DD (back-fill)")
    p.add_argument("--to", dest="to_date", help="last day to refresh, YYYY-MM-DD (default yesterday)")
    p.add_argument("--today", action="store_true",
                   help="today so far, channel-wise, Telegram only (workbook not touched)")
    p.add_argument("--sjit-request", action="store_true",
                   help="only ask the Myntra partner portal for the SJIT report of the report days")
    p.add_argument("--snapshot", nargs="?", const="current", metavar="YYYY-MM",
                   help="send a picture of a month sheet to Telegram (default: yesterday's month)")
    p.add_argument("--no-drive", action="store_true", help="use SALE_XLSX_PATH instead of the Drive file")
    p.add_argument("--no-telegram", action="store_true", help="do not send the Telegram message")
    return run_bot("Daily Sale report", p, run, argv, key="sale")
