"""The return analysis inputs, fetched from the portals into the folder layout engine.py reads:

    Input/Myntra/<MAIN|DBCC|FMCC>/  *Seller_Orders_Report*.csv + *Seller_Returns_Report*.csv
    Input/Ajio/                     DropShipOrderReports*.xlsx + DropShipRtvReports*.xlsx
    Input/Flipkart/                 Sales Report + Returns (.xlsx)

Sale = the report month; returns = the report month's 1st to the next month's 14th.
Myntra allows one month per report job and refuses the same report again for 15 minutes,
so the second returns job waits; jobs already on the portal for the same dates are reused.
"""
from __future__ import annotations

import calendar
import logging
import time
from datetime import date, timedelta
from pathlib import Path

from ..core.config import env

log = logging.getLogger(__name__)

COOLDOWN_SECONDS = 15 * 60 + 45


def windows(month: date, today: date) -> tuple[tuple[date, date], tuple[date, date], list[tuple[date, date]]]:
    """(sale range, returns range, returns ranges of at most one month) for the report month."""
    first = month.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    nxt = last + timedelta(days=1)
    ret_end = min(nxt.replace(day=14), today - timedelta(days=1))
    if ret_end < last:
        raise ValueError(f"returns for {first:%b %Y} cannot be read before {nxt.replace(day=1)}")
    parts = [(first, last)] + ([(nxt, ret_end)] if ret_end >= nxt else [])
    return (first, last), (first, ret_end), parts


# ------------------------------------------------------------------ Myntra

def myntra_portal(account: str):
    from ..portals import myntra as mp
    text = mp.from_worker(env, account)
    if not text:
        raise mp.SessionExpired(f"no {account} session saved (python scripts/myntra/myntra_session.py {account})")
    portal = mp.MyntraPortal(mp.parse_session(text))
    portal.check()
    return portal


def request_myntra(account: str, sale: tuple[date, date], parts: list[tuple[date, date]]) -> list[str]:
    """Morning step (9 AM): ask for the first returns report and the orders report (two different
    reports, so no cooldown between them). Returns first: at the same minute the daily SJIT run
    asks MAIN for an orders report, so MAIN's orders request may have to wait out the cooldown.
    What was done, one line each."""
    from ..portals import myntra as mp
    portal = myntra_portal(account)
    done = []
    for report_id, (start, end) in [(mp.RETURNS_REPORT_ID, parts[0]), (mp.REPORT_ID, sale)]:
        job = portal.ensure(report_id, start, end)
        done.append(f"{account} report {report_id} {start}..{end}: job {job.get('id')} {job.get('status')}")
    mp.save_to_worker(env, portal.cookies(), account)
    return done


def fetch_myntra(account: str, sale: tuple[date, date], parts: list[tuple[date, date]], dest: Path,
                 wait_minutes: int = 45) -> list[str]:
    """Orders + returns reports of one account into dest; file names written."""
    from ..portals import myntra as mp
    portal = myntra_portal(account)
    jobs = [portal.ensure(mp.REPORT_ID, *sale)]
    asked_at = None                       # when this run requested a returns report
    for start, end in parts:
        if not mp.exact_job(portal.jobs(100), mp.RETURNS_REPORT_ID, start, end) and asked_at:
            pause = COOLDOWN_SECONDS - (time.time() - asked_at)
            if pause > 0:
                log.info("%s: waiting %d min for the returns report cooldown", account, pause // 60 + 1)
                time.sleep(pause)
        before = {j.get("id") for j in portal.jobs(100)}
        job = portal.ensure(mp.RETURNS_REPORT_ID, start, end)
        if job.get("id") not in before:
            asked_at = time.time()
        jobs.append(job)
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for job in jobs:
        name, data = portal.collect_or_regenerate(job, wait_minutes)
        (dest / name).write_bytes(data)
        names.append(name)
    mp.save_to_worker(env, portal.cookies(), account)
    return names


# ------------------------------------------------------------------ Ajio

def fetch_ajio(sale: tuple[date, date], ret: tuple[date, date], dest: Path) -> list[str]:
    from ..portals.ajio import AjioClient
    user, pwd = env("AJIO_USERNAME"), env("AJIO_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("AJIO_USERNAME / AJIO_PASSWORD not set")
    ajio = AjioClient(user, pwd)
    ajio.login()
    dest.mkdir(parents=True, exist_ok=True)
    names = [f"DropShipOrderReports {sale[0]} to {sale[1]}.xlsx"]
    (dest / names[0]).write_bytes(ajio.order_report(*sale))
    for start, end, blob in ajio.rtv_reports(*ret, days=30):
        names.append(f"DropShipRtvReports {start} to {end}.xlsx")
        (dest / names[-1]).write_bytes(blob)
    return names


# ------------------------------------------------------------------ Flipkart

def returns_complete(xlsx: bytes, start: date, slack_days: int = 7) -> tuple[bool, str]:
    """(complete, why not) for a Flipkart Returns report meant to start at `start`: it must have
    rows, and its earliest return request within slack_days of the start. Flipkart drops returns
    older than about four months, which leaves such a report empty or starting late."""
    import io

    import pandas as pd
    try:
        r = pd.read_excel(io.BytesIO(xlsx), sheet_name="Returns", dtype=str)
    except Exception as exc:  # noqa: BLE001
        return False, f"Returns sheet not readable ({exc})"
    if r.empty:
        return False, "Returns report is empty"
    # the date the engine uses: return_requested_date is blank in Flipkart's file
    when = r.get("return_approval_date")
    if when is None:
        when = r.get("return_requested_date")
    elif "return_requested_date" in r:
        when = when.fillna(r["return_requested_date"])
    first = pd.to_datetime(when, errors="coerce").min()
    if pd.isna(first) or first.date() > start + timedelta(days=slack_days):
        return False, f"Returns report starts {first:%d %b}" if not pd.isna(first) else "no return dates"
    return True, ""


def flipkart_client():
    from ..portals.flipkart import FlipkartClient
    user, pwd = env("FLIPKART_EMAIL"), env("FLIPKART_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("FLIPKART_EMAIL / FLIPKART_PASSWORD not set")
    fk = FlipkartClient(user, pwd)
    fk.login()
    return fk


def request_flipkart(sale: tuple[date, date], ret: tuple[date, date]) -> list[str]:
    fk = flipkart_client()
    return [f"Flipkart {name} {a}..{b}: {fk.request_report(name, a, b)}"
            for name, (a, b) in [("Sales Report", sale), ("Returns", ret)]]


def fetch_flipkart(sale: tuple[date, date], ret: tuple[date, date], dest: Path, wait_minutes: int = 45) -> list[str]:
    fk = flipkart_client()
    for name, (a, b) in [("Sales Report", sale), ("Returns", ret)]:
        fk.request_report(name, a, b)       # both asked first, so they are made side by side
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    for name, (a, b) in [("Sales Report", sale), ("Returns", ret)]:
        file_name, data = fk.report(name, a, b, wait_minutes)
        if name == "Returns" and not returns_complete(data, a)[0]:
            rid = fk.request_report(name, a, b, fresh=True)          # a new report, once
            file_name, data = fk._report(name, a, b, rid, wait_minutes, 30)
            ok, why = returns_complete(data, a)
            if not ok:   # without returns the month's Flipkart numbers would be wrong
                raise RuntimeError(f"Flipkart returns incomplete: {why}")
        (dest / file_name).write_bytes(data)
        names.append(file_name)
    return names
