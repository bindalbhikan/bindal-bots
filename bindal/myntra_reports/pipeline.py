"""Myntra daily reports (8 AM): yesterday's Seller_Orders_Report, Seller_Returns_Report and
Return_Delivery_Report, and today's Seller_Listings_Report, of each account (MAIN / DBCC / FMCC).

The four are different reports, so they are asked for together (the 15-minute cooldown is per
report); a job already on the portal for the same dates (or a listings snapshot of today) is reused.
The CSVs go to Drive AGENT WORK/MYNTRA DAILY REPORTS/<yesterday>/ ("MAIN Orders 2026-10-23.csv")
and Telegram gets one message: per account the rows of each report and the folder link.
"""
from __future__ import annotations

import argparse
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..core import telegram
from ..core.app import run_bot
from ..core.config import csv_list, env, num, output_dir

log = logging.getLogger("bindal.myntra_reports")

ACCOUNTS = ["MAIN", "DBCC", "FMCC"]
AGENT_WORK = "1uO-aOha_QKkI3f19EA7sweYPgkjMukZI"   # Drive BINDAL EXPORTS/AGENT WORK
FOLDER = "MYNTRA DAILY REPORTS"
# label -> report id; None dates = snapshot report
REPORTS = {"Orders": 4, "Returns": 12, "Return Delivery": 84, "Listings": 14}
SNAPSHOT = {"Listings"}


def _job(portal, label: str, rid: int, day: date) -> dict:
    """A job of the report for yesterday (Listings: a snapshot asked for today), reused when the
    portal already has one."""
    from ..portals import myntra as mp
    if label not in SNAPSHOT:
        return portal.ensure(rid, day, day)
    today = date.today()
    for j in portal.jobs(100):
        status = str(j.get("status") or "").upper()
        created = mp._ts(j.get("createdOn"))
        if j.get("reportId") == rid and created == today and not any(f in status for f in mp.FAILED):
            log.info("Report %s of today already on the portal (job %s, %s)", rid, j.get("id"), status)
            return j
    return portal.schedule(None, None, rid)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    """The column, whatever its case in the CSV; empty when the report has no such column."""
    for c in df.columns:
        if str(c).strip().lower() == name:
            return df[c].astype(str).str.strip()
    return pd.Series(dtype=str)


def summary(label: str, df: pd.DataFrame) -> str:
    """One short line about a report's rows."""
    n = len(df)
    if label == "Orders":
        amount = pd.to_numeric(_col(df, "final amount"), errors="coerce").sum()
        cancelled = int((_col(df, "order status") == "F").sum())
        return f"{n} lines, ₹{amount:,.0f}, {cancelled} cancelled"
    if label in ("Returns", "Return Delivery"):
        kinds = _col(df, "type").value_counts()
        return f"{n} rows" + (" (" + ", ".join(f"{k} {v}" for k, v in kinds.items()) + ")" if len(kinds) else "")
    if label == "Listings":
        active = int((_col(df, "listing status") == "A").sum())
        return f"{n} SKUs, {active} active"
    return f"{n} rows"


def fetch_account(account: str, day: date, dest: Path, wait_minutes: int) -> tuple[list[tuple[str, Path, str]], list[str]]:
    """The account's four reports into dest: [(label, file, summary, {type: rows, "": all rows})] and
    notes for reports not got."""
    from ..portals import myntra as mp
    from ..return_analysis.sources import myntra_portal
    portal = myntra_portal(account)
    jobs, notes, got = {}, [], []
    for label, rid in REPORTS.items():
        try:
            jobs[label] = _job(portal, label, rid, day)
        except Exception as exc:  # noqa: BLE001 - the other reports still come
            if isinstance(exc, mp.SessionExpired):
                raise
            log.warning("%s %s not asked for: %s", account, label, exc)
            notes.append(f"{account} {label}: {str(exc)[:100]}")
    for label, job in jobs.items():
        try:
            _, data = portal.collect(job, wait_minutes)
            stamp = date.today() if label in SNAPSHOT else day
            path = dest / f"{account} {label} {stamp}.csv"
            path.write_bytes(data)
            df = pd.read_csv(io.BytesIO(data), low_memory=False)
            got.append((label, path, summary(label, df), _col(df, "type").value_counts().to_dict() | {"": len(df)}))
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, mp.SessionExpired):
                raise
            log.warning("%s %s not downloaded: %s", account, label, exc)
            notes.append(f"{account} {label}: {str(exc)[:100]}")
    mp.save_to_worker(env, portal.cookies(), account)
    return got, notes


def run(args) -> int:
    day = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    accounts = csv_list("MYNTRA_REPORT_ACCOUNTS") or ACCOUNTS
    dest = output_dir("myntra-reports") / f"{day}"
    dest.mkdir(parents=True, exist_ok=True)
    wait = num("MYNTRA_REPORT_WAIT_MINUTES", 40)
    results: dict[str, list] = {}
    notes: list[str] = []
    with ThreadPoolExecutor(len(accounts)) as pool:
        futures = {a: pool.submit(fetch_account, a, day, dest, wait) for a in accounts}
        for a, f in futures.items():
            try:
                results[a], more = f.result()
                notes += more
            except Exception as exc:  # noqa: BLE001 - the other accounts still go out
                log.exception("%s reports failed", a)
                results[a] = []
                notes.append(f"{a}: {str(exc)[:150]}")
    files = [p for got in results.values() for _, p, _, _ in got]
    if not files:
        raise RuntimeError("no Myntra report downloaded: " + "; ".join(notes))

    link = ""
    if args.no_upload:
        notes.append(f"not uploaded to Drive (test run): {dest}")
    else:
        from ..core.drive import DriveFolder
        folder = (DriveFolder(env("AGENT_WORK_FOLDER_ID", AGENT_WORK)).subfolder(FOLDER, create=True)
                  .subfolder(f"{day}", create=True))
        for p in files:
            try:
                folder.put(p, "text/csv")
            except Exception as exc:  # noqa: BLE001 - the other files are still saved
                log.exception("%s not uploaded", p.name)
                notes.append(f"{p.name} not saved to Drive: {str(exc)[:100]}")
        link = f"https://drive.google.com/drive/folders/{folder.folder_id}"

    out = [f"📊 <b>Myntra daily reports · {day:%d %b %Y}</b>"]
    # yesterday's return deliveries of all accounts together
    delivered: dict[str, int] = {}
    for got in results.values():
        for label, _, _, counts in got:
            if label == "Return Delivery":
                for k, v in counts.items():
                    delivered[k] = delivered.get(k, 0) + v
    if delivered:
        split = ", ".join(f"{k} {v}" for k, v in delivered.items() if k)
        out += ["", f"📦 <b>Total return delivery: {delivered['']}</b>" + (f" ({telegram.esc(split)})" if split else "")]
    for a in accounts:
        out += ["", f"<b>{telegram.esc(a)}</b>"]
        out += [f"• {telegram.esc(label)}: {telegram.esc(s)}" for label, _, s, _ in results[a]] or ["• no report"]
    if link:
        out += ["", f'📁 <a href="{link}">AGENT WORK / {FOLDER} / {day}</a>']
    if notes:
        out += [""] + ["⚠️ " + telegram.esc(n) for n in notes]
    msg = "\n".join(out)
    print(msg) if args.no_telegram else telegram.send(msg)
    return 1 if notes and not args.no_upload else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py myntra-reports",
                                description="Myntra orders / returns / return delivery / listings reports, daily")
    p.add_argument("--date", help="report day YYYY-MM-DD (default yesterday)")
    p.add_argument("--no-upload", action="store_true", help="keep the files in output/, not on Drive")
    p.add_argument("--no-telegram", action="store_true", help="print the summary instead of sending it")
    return run_bot("Myntra daily reports", p, run, argv)
