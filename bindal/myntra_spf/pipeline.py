"""Myntra SPF master, every day: returns of Myntra MAIN / DBCC / FMCC with claim aging, tickets,
payouts and ERP receipts, in Drive SPF/MYNTRA/MYNTRA SPF MASTER.xlsx.

The rules are those of the hand-run tool (engine.py, unchanged). The bot builds its input/
folder in a work folder:
  * Myntra partner portal, per account (saved sessions): Seller_Returns_Report and
    Return_Delivery_Report for the last RETURN_DAYS, Seller_Orders_Report for the last
    ORDER_DAYS (one month per job; the same report again only after the 15-minute cooldown)
  * ERP: GR GRN report and the dispatch (packing) report
  * Drive SPF/MYNTRA/input: what is still dropped by hand - each account's Ticket1 and payment
    exports, MANUAL RETURN and LEGACY MASTER
The previous master (from Drive) is the base, so a run only adds and updates rows. The new
master replaces the Drive file in place (Drive keeps the old versions).
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..core import dashboard, telegram
from ..core.app import run_bot
from ..core.config import env, num, output_dir
from ..core.drive import DriveFile, DriveFolder

log = logging.getLogger("bindal.myntra_spf")

MASTER_NAME = "MYNTRA SPF MASTER.xlsx"
ACCOUNTS = ["MAIN", "DBCC", "FMCC"]
DELIVERY_REPORT_ID = 84            # Return_Delivery_Report
COOLDOWN_SECONDS = 15 * 60 + 45


# ------------------------------------------------------------------ Myntra portal

def _windows(today: date) -> dict[int, list[tuple[date, date]]]:
    """report id -> date ranges of at most 30 days, ending yesterday."""
    from ..sale import myntra_portal as mp
    end = today - timedelta(days=1)

    def split(days: int) -> list[tuple[date, date]]:
        out, stop = [], end
        while len(out) * 30 < days:
            start = max(stop - timedelta(days=29), end - timedelta(days=days - 1))
            out.append((start, stop))
            stop = start - timedelta(days=1)
        return list(reversed(out))
    returns = split(num("MYNTRA_SPF_RETURN_DAYS", 30))
    return {mp.RETURNS_REPORT_ID: returns, DELIVERY_REPORT_ID: returns,
            mp.REPORT_ID: split(num("MYNTRA_SPF_ORDER_DAYS", 60))}


def fetch_account(account: str, today: date, dest: Path, notes: list[str]) -> None:
    """The account's returns, delivery and order reports into dest. Different reports are asked
    for together; the same report again only after the cooldown. Jobs for the same dates are reused."""
    from ..return_analysis.sources import myntra_portal
    from ..sale import myntra_portal as mp
    portal = myntra_portal(account)
    queues = [[(rid, a, b) for a, b in ranges] for rid, ranges in _windows(today).items()]
    jobs, step = [], 0
    while any(step < len(q) for q in queues):
        asked = False
        for q in queues:
            if step >= len(q):
                continue
            rid, a, b = q[step]
            job = mp.exact_job(portal.jobs(100), rid, a, b)
            if not job:
                try:
                    job = portal.schedule(a, b, rid)
                    asked = True
                except Exception as exc:  # noqa: BLE001 - that report only
                    notes.append(f"{account} report {rid} {a}..{b}: {str(exc)[:80]}")
                    continue
            jobs.append(job)
        step += 1
        if asked and any(step < len(q) for q in queues):
            log.info("%s: waiting for the report cooldown", account)
            time.sleep(COOLDOWN_SECONDS)
    dest.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        try:
            name, data = portal.collect(job, wait_minutes=num("MYNTRA_SPF_WAIT_MINUTES", 45))
            (dest / name).write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{account} report job {job.get('id')}: {str(exc)[:80]}")
    mp.save_to_worker(env, portal.cookies(), account)


# ------------------------------------------------------------------ ERP

def fetch_erp(dest: Path) -> list[str]:
    """GR GRN report and dispatch report as the xlsx files the engine recognises by their headers."""
    from ..core.erp import Erp
    erp = Erp.from_env()
    erp.login()
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for name, rows in [("GR GRN Report.xlsx", erp.grn()), ("Dispatch.xlsx", erp.dispatch())]:
        pd.DataFrame(rows).to_excel(dest / name, index=False)
        out.append(f"{name}: {len(rows)} rows")
    return out


# ------------------------------------------------------------------ Drive

def hand_inputs(folder: DriveFolder, dest: Path) -> list[str]:
    """From Drive SPF/MYNTRA/input: the files still dropped by hand (tickets, payments, manual
    returns, legacy master). The portal report folders are fetched fresh instead."""
    src = folder.subfolder("input")
    if not src:
        return []
    got = []
    for name, item in src.children().items():
        if item["mimeType"] != DriveFolder.FOLDER:
            continue
        sub = DriveFolder(item["id"], folder.http)
        if name in ACCOUNTS:
            for fname, f in sub.children().items():     # Ticket1 / payment files at the account root
                if f["mimeType"] != DriveFolder.FOLDER and fname.lower().endswith((".xlsx", ".csv")):
                    sub.download(fname, dest / name / fname)
                    got.append(f"{name}/{fname}")
        elif name in ("MANUAL RETURN", "LEGACY MASTER"):
            for p in sub.download_all(dest / name):
                got.append(f"{name}/{p.name}")
    return got


# ------------------------------------------------------------------ run

def run(args) -> int:
    if not env("MYNTRA_SPF_FOLDER_ID"):
        raise RuntimeError("MYNTRA_SPF_FOLDER_ID (Drive folder SPF/MYNTRA) not set")
    folder = DriveFolder(env("MYNTRA_SPF_FOLDER_ID"))
    found = folder.children().get(MASTER_NAME)
    if not found:
        raise RuntimeError(f"{MASTER_NAME} not found in the Drive folder SPF/MYNTRA")
    work = Path(tempfile.mkdtemp(prefix="myntra_spf_"))
    inp = work / "input"
    master = DriveFile(found["id"])
    master.download(work / MASTER_NAME, need_edit=not args.no_upload)
    notes: list[str] = []
    log.info("Hand inputs from Drive: %s", hand_inputs(folder, inp))

    today = date.today()
    tasks = {a: (fetch_account, (a, today, inp / a, notes)) for a in ACCOUNTS}
    with ThreadPoolExecutor(max_workers=len(tasks) + 1) as pool:
        futures = {name: pool.submit(fn, *a) for name, (fn, a) in tasks.items()}
        erp = pool.submit(fetch_erp, inp)
    for name, fut in futures.items():
        try:
            fut.result()
        except Exception as exc:  # noqa: BLE001 - the master is still rebuilt from the rest
            log.exception("Myntra %s reports failed", name)
            notes.append(f"Myntra {name} reports not fetched: {str(exc)[:100]}")
    try:
        log.info("ERP: %s", erp.result())
    except Exception as exc:  # noqa: BLE001
        log.exception("ERP reports failed")
        notes.append(f"ERP GRN / dispatch not read: {str(exc)[:100]}")

    # the engine runs in the work folder as if it were its own
    os.environ["MYNTRA_SPF_DIR"] = str(work)
    import importlib
    from . import engine
    engine = importlib.reload(engine)             # picks up MYNTRA_SPF_DIR
    engine.backup = lambda: None                  # Drive keeps the versions
    argv, sys.argv = sys.argv, ["myntra_spf", "--in-transit", "y"]
    try:
        engine.main()
    finally:
        sys.argv = argv
    out = output_dir("myntra_spf")
    shutil.copy2(work / MASTER_NAME, out / MASTER_NAME)
    if args.no_upload:
        notes.append("not uploaded to Drive (test run): output/myntra_spf")
    else:
        master.upload(work / MASTER_NAME)
    text = summary_text(work / MASTER_NAME, notes)
    print(text) if args.no_telegram else telegram.send(text)
    dashboard.publish("myntra_spf", {"notes": notes})
    return 0


def summary_text(path: Path, notes: list[str]) -> str:
    """Rows per account sheet of the new master."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True)
    lines = [f"{'Account':<8}{'Rows':>8}"]
    for name in ACCOUNTS:
        if name in wb.sheetnames:
            lines.append(f"{name:<8}{max(wb[name].max_row - 2, 0):>8}")
    out = ["🧾 <b>Myntra SPF master updated</b>", "<i>returns, tickets, payouts, ERP receipts</i>", "",
           "<pre>" + telegram.esc("\n".join(lines)) + "</pre>", "",
           "📁 Drive: SPF / MYNTRA / MYNTRA SPF MASTER.xlsx"]
    if notes:
        out += ["", "⚠️ <b>Notes</b>", *(f"• {telegram.esc(n)}" for n in notes)]
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py myntra-spf", description="Myntra SPF master")
    p.add_argument("--no-upload", action="store_true", help="keep the master in output/myntra_spf only (test run)")
    p.add_argument("--no-telegram", action="store_true", help="print the summary instead of sending it")
    return run_bot("Myntra SPF master", p, run, argv, key="myntra_spf")
