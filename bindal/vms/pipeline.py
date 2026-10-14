"""Daily Myntra VMS report: OMSGuru (SLA-date orders) -> VMS packing scans -> month sheet -> Telegram.

For each SLA date:
  ORDER COUNT = Myntra orders of that channel whose SLA date is that day and that have an AWB
  VMS COUNT   = those orders whose Myntra AWB has a scan in VMS
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from ..core import telegram
from ..core.app import run_bot
from ..core.config import env, num, output_dir
from ..core.drive import DriveFile
from ..core.files import replace_atomically
from ..core.omsguru import OmsGuruClient
from .myntra import MYNTRA_AWB, list_rows, myntra_awbs
from .vms import VmsClient
from .workbook import CODES, VmsWorkbook, sheet_month

BOOK_NAME = "MYNTRA ORDER ID WISE VMS.xlsx"
log = logging.getLogger("bindal.vms")


def channels() -> dict[str, str]:
    """OMSGuru channel id -> report code."""
    pairs = [p.split(":") for p in env("MYNTRA_CHANNELS", "45111:TLPL,47460:MMRJ,49234:BOAT").split(",") if ":" in p]
    return {k.strip(): v.strip() for k, v in pairs}


def report_dates(args) -> list[date]:
    today = date.today()
    if args.from_date:
        start = datetime.strptime(args.from_date, "%Y-%m-%d").date()
        end = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else today
    else:
        start, end = today - timedelta(days=args.days), today
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ------------------------------------------------------------------ data

def collect(dates: list[date], awb_cache: dict[str, str]):
    """Per-order frame for the report dates, plus new order -> Myntra AWB pairs and warnings."""
    chans = channels()
    lookback = num("SLA_LOOKBACK_DAYS", 10)
    client = OmsGuruClient.from_env()
    client.login()
    start = datetime.combine(min(dates) - timedelta(days=lookback), datetime.min.time())
    end = datetime.combine(max(dates) + timedelta(days=1), datetime.min.time())
    log.info("Listing Myntra orders with order date %s -> %s", start.date(), end.date())
    rows = list_rows(client, start, end, list(chans))

    df = rows[rows["SLA Date"].isin([pd.Timestamp(d) for d in dates])
              & rows["Channel Id"].isin(list(chans)) & (rows["AWB"] != "")].copy()
    df["Code"] = df["Channel Id"].map(chans)
    log.info("%d orders with an AWB on the report SLA dates", len(df))

    # Shadowfax / Delhivery orders: the Myntra AWB is only on the shipping label
    other = df[~df["AWB"].str.match(MYNTRA_AWB.pattern)]
    todo = [o for o in other["Order Id"] if o not in awb_cache]
    found, errors = myntra_awbs(client, todo) if todo else ({}, [])
    new_pairs = [(oid, df.loc[df["Order Id"] == oid, "AWB"].iloc[0], awb) for oid, awb in found.items()]
    mapped = {**awb_cache, **found}
    df["Myntra AWB"] = [mapped.get(o, a) for o, a in zip(df["Order Id"], df["AWB"])]

    vms = VmsClient(env("VMS_USERNAME"), env("VMS_PASSWORD"))
    if not vms.username or not vms.password:
        raise RuntimeError("VMS_USERNAME / VMS_PASSWORD not set")
    vms.login()
    scanned = vms.scanned_awbs(min(dates) - timedelta(days=lookback), date.today())
    df["In VMS"] = df["Myntra AWB"].isin(scanned) | df["AWB"].isin(scanned)
    return df, new_pairs, errors


def day_counts(df: pd.DataFrame, day: date) -> dict[str, tuple[int, int]]:
    part = df[df["SLA Date"] == pd.Timestamp(day)]
    return {c: (int((part["Code"] == c).sum()), int(((part["Code"] == c) & part["In VMS"]).sum())) for c in CODES}


# ------------------------------------------------------------------ telegram

def _table(counts: dict[str, tuple[int, int]]) -> str:
    lines = [f"{'Code':<5} {'Order':>5} {'VMS':>5} {'%':>6}"]
    to = tv = 0
    for c in CODES:
        o, v = counts.get(c, (0, 0))
        to, tv = to + o, tv + v
        lines.append(f"{c:<5} {o:>5} {v:>5} {(v / o * 100 if o else 0):>5.1f}%")
    lines.append(f"{'Total':<5} {to:>5} {tv:>5} {(tv / to * 100 if to else 0):>5.1f}%")
    return "<pre>" + telegram.esc("\n".join(lines)) + "</pre>"


def telegram_report(per_day: dict[date, dict], month: dict[str, tuple[int, int]], today: date) -> str:
    lines = [f"<b>📹 Myntra VMS - {today:%d %b %Y}</b>"]
    for d, counts in per_day.items():
        lines += ["", f"<b>📅 SLA {d:%d %b}</b>", _table(counts)]
    lines += ["", f"<b>🗓 {today:%B} total (1-{today.day})</b>", _table(month)]
    return "\n".join(lines)


def open_book(args) -> tuple[DriveFile | None, Path]:
    """The one workbook: the Drive file (worked on in a temp folder, uploaded in place, Drive keeps
    its version history) or, with --no-drive, the local file at VMS_XLSX_PATH."""
    if env("VMS_DRIVE_FILE_ID") and not args.no_drive:
        drive = DriveFile(env("VMS_DRIVE_FILE_ID"))
        return drive, drive.download(Path(tempfile.mkdtemp(prefix="vms_")) / BOOK_NAME, need_edit=True)
    if not env("VMS_XLSX_PATH"):
        raise RuntimeError("Set VMS_DRIVE_FILE_ID, or VMS_XLSX_PATH for a --no-drive run")
    path = Path(env("VMS_XLSX_PATH"))
    if not path.exists():
        raise RuntimeError(f"Workbook not found: {path}")
    return None, path


def run_snapshot(args) -> int:
    """Picture of a whole month sheet, sent to Telegram - the workbook is not changed."""
    from ..core.snapshot import sheet_png
    if args.snapshot and args.snapshot != "current":
        year, month = datetime.strptime(args.snapshot, "%Y-%m").timetuple()[:2]
    else:
        year, month = date.today().year, date.today().month
    _, path = open_book(args)
    title = next((ws.title for ws in VmsWorkbook(path).wb.worksheets if sheet_month(ws.title) == (year, month)), None)
    if title is None:
        raise RuntimeError(f"No sheet for {month:02d}-{year} in {BOOK_NAME}")
    png = sheet_png(path, title, output_dir("vms"))
    log.info("Sheet picture: %s", png)
    if not args.no_telegram:
        telegram.send_file(png, f"<b>📊 Myntra VMS · {telegram.esc(title)}</b>")
        telegram.send_file(png, "Full size", as_document=True)  # photos are downscaled by Telegram
    return 0


# ------------------------------------------------------------------ main

def run(args) -> int:
    if args.snapshot:
        return run_snapshot(args)
    dates = report_dates(args)
    drive, book_path = open_book(args)
    book = VmsWorkbook(book_path)

    df, new_pairs, errors = collect(dates, book.awb_cache())
    per_day = {d: day_counts(df, d) for d in dates}
    if not args.only_no_vms:
        for d, counts in per_day.items():
            book.write_day(d, counts)
            log.info("SLA %s: %s", d, counts)
    missing = df[~df["In VMS"]].sort_values(["SLA Date", "Code", "Myntra AWB"])
    book.write_no_vms(dates, [(t.date(), c, a) for t, c, a in
                              zip(missing["SLA Date"], missing["Code"], missing["Myntra AWB"])])
    log.info("NO VMS: %d orders on %d SLA date(s)", len(missing), len(dates))
    book.save_awb_cache(new_pairs)

    replace_atomically(book.save, book_path)
    if drive:
        drive.upload(book_path)
    log.info("Workbook saved: %s", book_path)

    errors_log = output_dir("vms") / "errors.log"
    errors_log.write_text("\n".join(errors), encoding="utf-8")   # emptied on a clean run
    if errors:
        log.warning("%d label warnings, see %s", len(errors), errors_log)

    month = VmsWorkbook(book_path).month_totals(dates[-1])
    text = telegram_report({d: per_day[d] for d in dates[-2:]}, month, dates[-1])
    if errors:
        text += f"\n⚠️ {len(errors)} shipping label(s) not readable: Myntra AWB unknown, counted as NO VMS"
    if args.no_telegram:
        print(text)
    else:
        telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py vms", description="Myntra VMS report")
    p.add_argument("--days", type=int, default=num("VMS_REPORT_DAYS", 1),
                   help="SLA dates to refresh: today plus this many previous days (default 1)")
    p.add_argument("--from", dest="from_date", help="first SLA date to refresh, YYYY-MM-DD (back-fill)")
    p.add_argument("--to", dest="to_date", help="last SLA date to refresh, YYYY-MM-DD (default today)")
    p.add_argument("--only-no-vms", action="store_true",
                   help="refresh only the NO VMS sheet, leave the month-sheet counts untouched (for back-fills)")
    p.add_argument("--snapshot", nargs="?", const="current", metavar="YYYY-MM",
                   help="send a picture of a month sheet to Telegram (default: this month)")
    p.add_argument("--no-drive", action="store_true", help="use the local workbook instead of the Drive file")
    p.add_argument("--no-telegram", action="store_true", help="do not send the Telegram message")
    return run_bot("Myntra VMS report", p, run, argv)
