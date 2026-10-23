"""Myntra courier scorecard (6:30 PM): pickups of the Myntra accounts against the dispatch SLA.

Pickup = the Seller_Orders_Report's "fmpu date" (first-mile pickup) of each account (MAIN, DBCC,
FMCC; reports already on the partner portal are reused - the Myntra SPF run asks for them every
morning). Deadline = OMSGuru's SLA Date of the same order, matched on the tracking number (OMSGuru's
AWB for Myntra). Orders of the last MYNTRA_TAT_DAYS (7) complete days (the SLA passed), cancelled
left out. Late on our side = packed after the SLA. Telegram only, nothing written.
"""
from __future__ import annotations

import argparse
import io
import logging
import time
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd

from ..core import telegram
from ..core.app import run_bot
from ..core.config import csv_list, env, num
from ..portals.omsguru import OmsGuruClient
from ..sale.oms_sales import order_lines

log = logging.getLogger("bindal.myntra_tat")

ACCOUNTS = ["MAIN", "DBCC", "FMCC"]


def report_frame(files: list[tuple[str, bytes]], account: str) -> pd.DataFrame:
    """One row per order line of the account's Seller_Orders_Report files (newest file wins)."""
    frames = []
    for _, data in files:
        if data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                data = z.read(next(n for n in z.namelist() if n.lower().endswith(".csv")))
        df = pd.read_csv(io.BytesIO(data), dtype=str, low_memory=False)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).drop_duplicates("order line id", keep="first")
    out = pd.DataFrame({
        "account": account,
        "awb": df["order tracking number"].fillna("").astype(str).str.strip().str.upper(),
        "courier": df["courier code"].fillna("?").astype(str).str.strip().str.upper(),
        "status": df["order status"].fillna("").astype(str),
        "created": pd.to_datetime(df["created on"], errors="coerce"),
        "packed": pd.to_datetime(df["packed on"], errors="coerce"),
        "pickup": pd.to_datetime(df["fmpu date"], errors="coerce"),
        "cancelled": pd.to_datetime(df["cancelled on"], errors="coerce")})
    return out


def score(rep: pd.DataFrame, sla: dict[str, datetime], first: date, last: date, now: datetime) -> pd.DataFrame:
    """The orders of first..last with an SLA, each with on_time / late / ours (packed late)."""
    df = rep[rep["created"].dt.date.between(first, last) & rep["cancelled"].isna() & (rep["awb"] != "")].copy()
    df["sla"] = df["awb"].map(sla)
    df = df.dropna(subset=["sla"]).drop_duplicates(["account", "awb"])
    df["on_time"] = df["pickup"].notna() & (df["pickup"] <= df["sla"])
    df["late"] = ~df["on_time"] & ((df["pickup"].notna()) | (df["sla"] < now))
    df["ours"] = df["late"] & (df["packed"].isna() | (df["packed"] > df["sla"]))
    return df


def message(df: pd.DataFrame, first: date, last: date, matched: int, total: int) -> str:
    lines = [f"🛍 <b>Myntra Courier Pickup · {first:%d %b} - {last:%d %b}</b>",
             "<i>pickup (fmpu) against the OMSGuru SLA, by account and courier</i>"]
    table = [f"{'Acct':<5}{'Courier':<8}{'Ord':>5}{'Late':>5}{'OnT%':>5}"]   # 28 wide: a phone
    for (acct, courier), g in sorted(df.groupby(["account", "courier"]), key=lambda kv: (kv[0][0], -len(kv[1]))):
        n = int(g["on_time"].sum() + g["late"].sum())
        pct = f"{round(100 * g['on_time'].sum() / n)}%" if n else "-"
        table.append(f"{acct:<5}{courier[:7]:<8}{len(g):>5}{int(g['late'].sum()):>5}{pct:>5}")
    n = int(df["on_time"].sum() + df["late"].sum())
    table.append(f"{'Total':<13}{len(df):>5}{int(df['late'].sum()):>5}"
                 + (f"{round(100 * df['on_time'].sum() / n)}%" if n else "-").rjust(5))
    late = df[df["late"]]
    lines += ["<pre>" + telegram.esc("\n".join(table)) + "</pre>",
              f"Late: <b>{len(late)}</b> · our side (packed after the SLA) {int(late['ours'].sum())} · "
              f"courier side {len(late) - int(late['ours'].sum())}",
              f"<i>{matched} of {total} report orders found in OMSGuru with an SLA</i>"]
    return "\n".join(lines)


def run(args) -> int:
    from ..portals import myntra as mp
    from ..return_analysis.sources import myntra_portal
    now = datetime.now()
    last = now.date() - timedelta(days=2)                  # every SLA passed
    first = last - timedelta(days=int(num("MYNTRA_TAT_DAYS", 7)) - 1)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    reps, notes = [], []
    for account in csv_list("MYNTRA_TAT_ACCOUNTS") or ACCOUNTS:
        try:
            try:
                portal = myntra_portal(account)
            except mp.SessionExpired:
                raise
            except Exception as exc:  # noqa: BLE001 - the portal answers 500 now and then: once more
                log.warning("Myntra %s portal check failed (%s): trying again", account, exc)
                time.sleep(20)
                portal = myntra_portal(account)
            files, covered = portal.reports(days, wait_minutes=num("MYNTRA_TAT_WAIT_MINUTES", 15))
            mp.save_to_worker(env, portal.cookies(), account)
            reps.append(report_frame(files, account))
            if set(days) - covered:
                notes.append(f"{account}: report does not cover {len(set(days) - covered)} of the days")
        except Exception as exc:  # noqa: BLE001 - the other accounts still count
            log.warning("Myntra %s report not read: %s", account, exc)
            notes.append(f"{account}: report not read ({str(exc)[:80]})")
    rep = pd.concat([r for r in reps if not r.empty], ignore_index=True) if reps else pd.DataFrame()
    if rep.empty:
        raise RuntimeError("No Myntra order report could be read: " + "; ".join(notes))
    client = OmsGuruClient.from_env()
    client.login()
    lines, _ = order_lines(client, [first - timedelta(days=1) + timedelta(days=i) for i in range(len(days) + 2)])
    sla = {l["awb"]: l["sla"] for l in lines if l["awb"] and l["sla"]}
    total = int((rep["created"].dt.date.between(first, last) & rep["cancelled"].isna()).sum())
    df = score(rep, sla, first, last, now)
    text = message(df, first, last, len(df), total) + ("\n\n⚠️ " + "\n⚠️ ".join(telegram.esc(n) for n in notes) if notes else "")
    log.info("Myntra TAT: %d orders scored of %d, %d late", len(df), total, int(df["late"].sum()))
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py myntra-tat", description="Myntra courier pickup scorecard")
    p.add_argument("--no-telegram", action="store_true", help="print the scorecard instead of sending it")
    return run_bot("Myntra courier scorecard", p, run, argv)
