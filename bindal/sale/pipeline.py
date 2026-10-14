"""Daily sale report: OMSGuru (all channels) + Flipkart FBF + Myntra SJIT -> SALE REPORT.xlsx -> Telegram.

What is written for each report day:
  OMS blocks (AZ/FX/FBA, MEESHO, AJIO B2C, FLIPKART, MYNTRA, NYKAA, SNAPDEAL): GROSS by order date
  FBF : GROSS and Cancel from Flipkart Seller Hub (Flipkart warehouses only)
  SJIT: GROSS and Cancel from the Myntra Seller_Orders_Report CSV in the SJIT Drive folder;
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

from ..core import telegram
from ..core.app import run_bot
from ..core.config import ROOT, csv_list, env, num, output_dir
from ..core.drive import DriveFile
from ..core.files import replace_atomically
from ..core.omsguru import OmsGuruClient
from .oms_sales import OMS_BLOCKS, gross_by_day
from .workbook import SaleWorkbook

BOOK_NAME = "SALE REPORT.xlsx"
REPORT_ORDER = ["AZ/FX/FBA", "MEESHO", "AJIO B2C", "FLIPKART", "FBF", "MYNTRA", "SJIT", "NYKAA", "SNAPDEAL"]
TODAY_ORDER = [b for b in REPORT_ORDER if b != "SJIT"]  # SJIT comes from a hand-dropped report
SHORT = {"AZ/FX/FBA": "Amazon", "AJIO B2C": "Ajio", "FBF": "FBF", "SJIT": "SJIT"}
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
    from .flipkart import DEFAULT_FBF_LOCATIONS, FlipkartClient
    user, pwd = env("FLIPKART_EMAIL"), env("FLIPKART_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("FLIPKART_EMAIL / FLIPKART_PASSWORD not set")
    fk = FlipkartClient(user, pwd)
    fk.login()
    locations = csv_list("FBF_LOCATIONS") or DEFAULT_FBF_LOCATIONS
    return fk.fbf_units(days, locations)


def sjit_source():
    """Myntra SJIT: the Seller_Orders_Report dropped by hand into the SJIT Drive folder.
    (The partner portal login has a reCAPTCHA, so it is not fetched automatically.)"""
    from . import sjit
    folder = env("SJIT_DRIVE_FOLDER_ID")
    if folder:
        files = sjit.files_from_drive(folder)
    else:
        local = Path(env("SJIT_LOCAL_DIR") or ROOT / "sjit")
        files = [(p.name, p.read_bytes()) for p in sorted(local.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
                 if p.suffix.lower() in (".csv", ".zip")] if local.is_dir() else []
    if not files:
        raise RuntimeError("no SJIT report file found")
    return sjit.parse(files)


# ------------------------------------------------------------------ telegram

def telegram_report(book: SaleWorkbook, day: date, notes: list[str]) -> str:
    esc = telegram.esc
    rows, total = [], 0
    for block in REPORT_ORDER:
        g, c = book.read(day, block, "GROSS"), book.read(day, block, "CANCEL")
        total += g
        rows.append(f"{SHORT.get(block, block.title())[:10]:<10} {g:>5} {c if c else '-':>5}")
    table = [f"{'Channel':<10} {'Gross':>5} {'Cncl':>5}", *rows, f"{'Total':<10} {total:>5}"]
    month = book.month_gross(day)
    mrows = [f"{SHORT.get(b, b.title())[:10]:<10} {month.get(b, 0):>6}" for b in REPORT_ORDER]
    mtable = [f"{'Channel':<10} {'Gross':>6}", *mrows, f"{'Total':<10} {sum(month.get(b, 0) for b in REPORT_ORDER):>6}"]
    lines = [f"<b>📈 Daily Sale - {day:%d %b %Y}</b>",
             "<pre>" + esc("\n".join(table)) + "</pre>",
             f"<b>🗓 {day:%B} total (1-{day.day})</b>",
             "<pre>" + esc("\n".join(mtable)) + "</pre>"]
    if notes:
        lines.append("⚠️ " + esc(" | ".join(notes)))
    return "\n".join(lines)


def today_report(counts: dict[str, int], cancels: dict[str, int], notes: list[str]) -> str:
    esc = telegram.esc
    rows = [f"{SHORT.get(b, b.title())[:10]:<10} {counts.get(b, 0):>5} {cancels.get(b) or '-':>5}"
            for b in TODAY_ORDER]
    total = sum(counts.get(b, 0) for b in TODAY_ORDER)
    table = [f"{'Channel':<10} {'Gross':>5} {'Cncl':>5}", *rows, f"{'Total':<10} {total:>5}"]
    lines = [f"<b>📊 Today's Sale - {datetime.now():%d %b %Y, %I:%M %p}</b>",
             "<pre>" + esc("\n".join(table)) + "</pre>"]
    if notes:
        lines.append("⚠️ " + esc(" | ".join(notes)))
    return "\n".join(lines)


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
        for block in OMS_BLOCKS:
            book.write(d, block, "GROSS", gross[(d, block)])
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
    try:
        sg, sc, covered = sjit_source()
    except Exception as exc:
        sg, sc, covered = Counter(), Counter(), set()
        log.warning("SJIT report not available: %s", exc)
    missing = [d for d in days if d not in covered]
    for d in days:
        if d in covered:
            book.write(d, "SJIT", "GROSS", sg[d])
            book.write(d, "SJIT", "CANCEL", sc[d])
        else:
            book.write(d, "SJIT", "GROSS", 0, only_if_empty=True)
            book.write(d, "SJIT", "CANCEL", 0, only_if_empty=True)
    if missing:
        notes.append("SJIT report not ready for " + ", ".join(f"{d:%d %b}" for d in missing) + " (taken as 0)")

    for ws in {book.month_sheet(d.year, d.month) for d in days}:
        book.repair_formulas(ws)
    replace_atomically(book.save, path)
    if drive:
        drive.upload(path)
    log.info("Sale report saved (%s)", "Drive" if drive else path)

    text = telegram_report(SaleWorkbook(path), days[-1], notes)
    print(text) if args.no_telegram else telegram.send(text)
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
    p.add_argument("--snapshot", nargs="?", const="current", metavar="YYYY-MM",
                   help="send a picture of a month sheet to Telegram (default: yesterday's month)")
    p.add_argument("--no-drive", action="store_true", help="use SALE_XLSX_PATH instead of the Drive file")
    p.add_argument("--no-telegram", action="store_true", help="do not send the Telegram message")
    return run_bot("Daily Sale report", p, run, argv)
