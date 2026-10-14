"""Daily run: OMSGuru -> courier tracking -> TAT rules -> Google Sheet -> Telegram."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..awb_tracker import CourierRegistry, Job, track_all
from ..core import telegram
from ..core.app import run_bot
from ..core.config import ROOT, csv_list, env, num, output_dir
from ..core.omsguru import OmsGuruClient
from .tat_engine import (OPEN_PICKUP, apply_status, apply_tracking,
                         needs_oms_refresh, build_report, merge_master, normalize_master,
                         prepare_tracking, summarize, write_excel)

KEEP_BACKUPS = 30
log = logging.getLogger("bindal.ajio_tat")


def master_xlsx() -> Path:
    return Path(env("TAT_XLSX_PATH") or ROOT / "data" / "AJIO_TAT_MASTER.xlsx")


def read_master_xlsx(path: Path):
    if not path.exists():
        return normalize_master(pd.DataFrame()), None
    m = normalize_master(pd.read_excel(path, "_data", dtype=str))
    try:
        as_of = pd.to_datetime(pd.read_excel(path, "_meta").loc[0, "as_of"])
    except Exception:
        as_of = None
    return m, as_of


def save_master_xlsx(df, path: Path, note: str, as_of) -> None:
    """Backup the current master, write a temp file, verify it, then swap it in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        bdir = path.parent / "backup"
        bdir.mkdir(exist_ok=True)
        shutil.copy2(path, bdir / f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
        for old in sorted(bdir.glob(f"{path.stem}_*.xlsx"))[:-KEEP_BACKUPS]:
            old.unlink()
    tmp = path.with_name(path.stem + "_tmp.xlsx")
    write_excel(df, tmp, note, as_of)
    if len(read_master_xlsx(tmp)[0]) != len(df):
        raise RuntimeError(f"New master failed verification; old master left unchanged ({tmp})")
    try:
        os.replace(tmp, path)
    except PermissionError:
        raise RuntimeError(f"Master file is open in Excel - close it and run again: {path}")
    log.info("Master xlsx saved: %s", path)


# ------------------------------------------------------------------ steps

def warehouse_ids() -> list[str]:
    return csv_list("OMSGURU_WAREHOUSE_IDS")


def fetch_oms(start: datetime, end: datetime, skip_channel_orders: set[str]):
    client = OmsGuruClient.from_env(workers=num("OMSGURU_WORKERS", 6))
    client.login()
    channels = csv_list("AJIO_CHANNEL_IDS", "57285,34958")
    log.info("Listing AJIO orders %s -> %s (channels %s)", start, end, channels)
    o, hist, errors = client.fetch_window(start, end, channels, warehouse_ids(), skip_channel_orders)
    log.info("%d AJIO order lines fetched", len(o))
    hist["ts"] = pd.to_datetime(hist["Timestamp"], format="%B %d, %Y %I:%M:%S %p", errors="coerce")
    return o, hist, errors


def oms_window(args) -> tuple[datetime, datetime]:
    """OMSGuru order-date window: yesterday + today by default (--days 1), or --from/--to."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = (datetime.strptime(args.to_date, "%Y-%m-%d") if args.to_date else today) + timedelta(days=1)
    start = datetime.strptime(args.from_date, "%Y-%m-%d") if args.from_date else today - timedelta(days=args.days)
    return start, end


def track(pairs: pd.DataFrame):
    """pairs: AWB + Courier columns, one row per AWB to track."""
    jobs = [Job(a, p) for a, p in zip(pairs["AWB"], pairs["Courier"])]
    if not jobs:
        return prepare_tracking([], []), 0, []
    cfg_path = ROOT / "tracking_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    if env("SHADOWFAX_TOKEN"):
        cfg.setdefault("couriers", {}).setdefault("SHADOWFAX", {})["token"] = env("SHADOWFAX_TOKEN")
    log.info("Tracking %d AWBs (pickup not confirmed yet)", len(jobs))
    results = track_all(jobs, CourierRegistry(cfg), workers=num("TRACK_WORKERS", 8), progress=False)
    summary = [r.summary_row() for r in results]
    events = [e for r in results for e in r.event_rows()]
    # NOT_FOUND is normal for a fresh AWB the courier has not manifested yet: not an error
    failed = [f"{r.partner} {r.awb}: {r.error}" for r in results
              if not r.ok and r.error and r.status != "NOT_FOUND"]
    return prepare_tracking(summary, events), len(jobs), failed


def awbs_to_track(o: pd.DataFrame, old_status: pd.DataFrame) -> pd.DataFrame:
    """AWBs whose pickup is still open: orders just read from OMS, plus master orders
    still 'Pending Pickup' from the last TRACK_DAYS days. Picked / cancelled ones are skipped."""
    fresh = pd.DataFrame({
        "AWB": o["AWB / Shipment Tracker"].astype(str).str.extract(r"^\s*([A-Za-z0-9]+)")[0],
        "Courier": o["Shipping Company"], "Status": o["Item Status"].astype(str)})
    fresh = fresh[~fresh["Status"].str.contains("cancel", case=False)]
    since = pd.Timestamp.now().normalize() - pd.Timedelta(days=num("TRACK_DAYS", 15))
    is_open = old_status["Pickup Status"].isin(OPEN_PICKUP)
    pend = old_status[is_open & (old_status["Order Time"] >= since)]
    pend = pend[["AWB", "Courier"]]
    picked = set(old_status.loc[~is_open, "AWB"].astype(str))
    pairs = pd.concat([fresh[["AWB", "Courier"]], pend]).dropna(subset=["AWB"])
    pairs = pairs[(pairs["AWB"].astype(str).str.len() > 3) & ~pairs["AWB"].astype(str).isin(picked)]
    return pairs.drop_duplicates("AWB")


# ------------------------------------------------------------------ report

PICKED = ["On Time", "Late", "Picked (Time N/A)"]


def awaiting_pickup_mask(df: pd.DataFrame) -> pd.Series:
    """Processed, not picked up, not cancelled."""
    return (~df["Pickup Status"].isin(PICKED + ["Cancelled"])
            & (df["Process Status"] != "Not Processed"))


def pickup_pending_mask(df: pd.DataFrame, now: pd.Timestamp) -> pd.Series:
    """Awaiting pickup and the TAT deadline has passed."""
    return awaiting_pickup_mask(df) & (df["Dispatch Deadline"] < now)


def _summary_block(part: pd.DataFrame, title: str, now: pd.Timestamp) -> list[str]:
    """Order count, processing on time / late / pending, and a narrow per-courier table:
    Done = picked up, Pend = pickup pending: processed but not picked and the TAT deadline has passed,
    Due = processed, not picked, deadline not reached yet, Proc = not processed."""
    proc = part["Process Status"].value_counts()
    lines = ["", f"<b>{title} · {len(part)} orders</b>",
             f"✅ On time {proc.get('On Time', 0)}  ⏰ Late {proc.get('Late', 0)}  "
             f"⏳ Pending {proc.get('Not Processed', 0)}"]
    if part.empty:
        return lines
    rows = []
    for courier, g in part.groupby(part["Courier"].fillna("UNKNOWN"), sort=True):
        not_proc = g["Process Status"] == "Not Processed"
        picked = g["Pickup Status"].isin(PICKED)
        waiting = ~picked & ~not_proc
        overdue = pickup_pending_mask(g, now)
        rows.append((str(courier).title()[:10], int(picked.sum()),
                     int(overdue.sum()), int((waiting & ~overdue).sum()), int(not_proc.sum())))
    table = [f"{'Courier':<10} {'Done':>4} {'Pend':>4} {'Due':>4} {'Proc':>4}"]
    table += [f"{c:<10} {d:>4} {p:>4} {x:>4} {n:>4}" for c, d, p, x, n in rows]
    table.append(f"{'Total':<10} " + " ".join(f"{sum(r[i] for r in rows):>4}" for i in range(1, 5)))
    lines.append("<pre>" + telegram.esc("\n".join(table)) + "</pre>")
    return lines


def telegram_report(df):
    """Mobile-friendly summary: yesterday, today, and the current month to date
    (by order date, cancelled orders left out)."""
    now = pd.Timestamp.now()
    today = now.normalize()
    live = df[df["Pickup Status"] != "Cancelled"]
    order_day = live["Order Time"].dt.normalize()
    lines = [f"<b>📦 AJIO Dispatch - {today:%d %b %Y}</b>"]
    for day in (today - pd.Timedelta(days=1), today):
        lines += _summary_block(live[order_day == day], f"📅 {day:%d %b}", now)
    month_start = today.replace(day=1)
    lines += _summary_block(live[(order_day >= month_start) & (order_day <= today)],
                            f"🗓 {today:%B} total (1-{today.day})", now)
    lines += ["", "<i>Done = pickup ho gaya",
              "Pend = Pickup Pending (pickup nahi hua, TAT nikal gaya)",
              "Due = pickup nahi hua, TAT abhi baaki hai",
              "Proc = processing pending</i>"]
    return "\n".join(lines)


def pickup_pending_messages(df, now=None) -> list[str]:
    """Second Telegram message: every processed AWB still waiting for pickup (last TRACK_DAYS days),
    TAT passed ("Over") first, then still within TAT ("Due"), with a short ready-to-copy mail
    for the courier / AJIO. Empty list when nothing is waiting."""
    now = now or pd.Timestamp.now()
    since = now.normalize() - pd.Timedelta(days=num("TRACK_DAYS", 15))
    pend = df[awaiting_pickup_mask(df) & (df["Order Time"] >= since)].copy()
    if pend.empty:
        return []
    pend["_over"] = pend["Dispatch Deadline"] < now
    pend = pend.sort_values(["_over", "Courier", "RTS Time"], ascending=[False, True, True])
    n_over = int(pend["_over"].sum())
    pob = env("POB_ID", "DV00348890")
    rows = []
    for _, r in pend.iterrows():
        ready = f"{r['RTS Time']:%d-%b}" if pd.notna(r["RTS Time"]) else "-"
        tat = "Over" if r["_over"] else "Due"
        rows.append(f"{str(r['AWB']):<16} {str(r['Courier']).title()[:10]:<10} {ready:<6} {tat}")
    per_msg = 120  # keeps each message under Telegram's 4096-character limit
    msgs = []
    for i in range(0, len(rows), per_msg):
        part = rows[i:i + per_msg]
        head = (f"Subject: Pickup Pending - POB ID {pob} - {len(pend)} shipments\n\n"
                "Dear Team,\n\n"
                "Below shipments are packed and ready, but pickup is still pending.\n"
                "Kindly arrange the pickup today on priority.\n\n"
                f"POB ID: {pob}\n\n") if i == 0 else ""
        tail = "\n\nRegards,\nBindal Exports" if i + per_msg >= len(rows) else ""
        body = head + f"{'AWB':<16} {'Courier':<10} {'Ready':<6} TAT\n" + "\n".join(part) + tail
        title = (f"<b>📮 Pickup Pending · {len(pend)} AWB</b>\n"
                 f"Over (TAT nikal gaya) {n_over} · Due (TAT baaki) {len(pend) - n_over}\n"
                 if i == 0 else f"<b>📮 Pickup Pending (contd. {i // per_msg + 1})</b>\n")
        msgs.append(title + "<pre>" + telegram.esc(body) + "</pre>")
    return msgs


# ------------------------------------------------------------------ main

def run(args) -> int:
    mode = env("RUN_MODE", "local")
    out = output_dir("ajio_tat")
    sheet = None
    sheet_id = env("TAT_GOOGLE_SHEET_ID")
    if sheet_id and not args.no_sheet:
        from .sheets import MasterSheet
        sheet = MasterSheet(sheet_id)
    drive = None
    drive_id = env("TAT_DRIVE_FILE_ID")
    if drive_id and not args.no_drive:
        from ..core.drive import DriveFile
        drive = DriveFile(drive_id)
    drive_copy = out / "drive" / "AJIO_TAT_MASTER.xlsx"

    # 1) master state
    if args.seed_xlsx:
        old = normalize_master(pd.read_excel(args.seed_xlsx, "_data", dtype=str))
        old_as_of = None
        log.info("Seeding from %s: %d orders", args.seed_xlsx, len(old))
        if sheet:
            cur, _ = sheet.read_master()
            old = pd.concat([old, cur]).drop_duplicates("Invoice No", keep="last")
    elif drive:
        old, old_as_of = read_master_xlsx(drive.download(drive_copy, need_edit=True))
    elif sheet:
        old, old_as_of = sheet.read_master()
    else:
        old, old_as_of = read_master_xlsx(master_xlsx())
    log.info("Master loaded: %d orders", len(old))

    # 2) OMSGuru, date-wise: open order pages only for orders that are new or still open
    old_status = apply_status(old, pd.Timestamp.now()) if len(old) else old.assign(**{"Pickup Status": None})
    complete = set(old_status.loc[~needs_oms_refresh(old_status), "Order ID"].dropna().astype(str))
    start, end = oms_window(args)
    o, hist, errors = fetch_oms(start, end, complete)

    # 3) courier tracking, only where pickup is not confirmed yet
    (summ, ev), n_awbs, track_errors = track(awbs_to_track(o, old_status))
    errors += track_errors
    if o.empty:
        new, as_of = normalize_master(pd.DataFrame()), pd.Timestamp.now()
    else:
        new, as_of = build_report(o, hist, summ, ev)

    # 4) merge + rules
    merged, stats = merge_master(old, new)
    merged = apply_tracking(merged, summ, ev)
    if len(ev):
        as_of = max(as_of, ev["ts"].max())
    if old_as_of is not None and pd.notna(old_as_of):
        as_of = max(as_of, old_as_of)
    df = apply_status(merged, as_of)
    s = summarize(df)
    note = (f"OMSGuru {start:%d-%b} to {end - timedelta(days=1):%d-%b} ({len(o)} lines read) "
            f"+ courier tracking ({n_awbs} open AWBs)")
    log.info("Master after merge: %d (new %d, updated %d, kept %d)", len(df), stats["new"],
             stats["updated"], stats["kept (not in input)"])

    # 5) outputs
    if drive:
        if not drive.meta:  # seeded run: fetch Drive's current file (backup + change check on upload)
            drive.download(drive_copy, need_edit=True)
        save_master_xlsx(df, drive_copy, note, as_of)
        drive.upload(drive_copy)
    if args.xlsx or not (sheet or drive):
        save_master_xlsx(df, master_xlsx(), note, as_of)
    if sheet:
        sheet.write_all(df, as_of, note, s)
        sheet.append_run_log([pd.Timestamp.now(), mode, len(o), n_awbs, len(df), stats["new"], stats["updated"],
                              s["tat"]["CORRECT"], s["tat"]["NOT CORRECT"], s["tat"]["PENDING"],
                              s["tat"]["CHECK"], round(s["tat_pct"], 4), len(errors)])
    (out / "errors.log").write_text("\n".join(errors), encoding="utf-8")   # emptied on a clean run
    report = telegram_report(df)
    if errors:
        report += (f"\n\u26a0\ufe0f {len(errors)} order page / tracking lookups failed "
                   f"(see the run's errors.log); those orders keep their last known status")
    if args.no_telegram:
        print(report)
    else:
        telegram.send(report)
        for msg in pickup_pending_messages(df):
            telegram.send(msg)
    print(df["TAT Result"].value_counts().to_string())
    return 0


def probe(args) -> int:
    """Login + list only: checks credentials and page layout without writing anything."""
    client = OmsGuruClient.from_env()
    client.login()
    end = datetime.now() + timedelta(days=1)
    channels = csv_list("AJIO_CHANNEL_IDS", "57285,34958")
    whs = warehouse_ids() or list(client.warehouses()) or [None]
    sample = []
    for wh in whs:
        if wh:
            client.set_warehouse(wh)
        ids = client.list_order_ids(end - timedelta(days=args.days + 1), end, channels)
        print(f"Warehouse {wh or 'current'}: {len(ids)} AJIO orders in the last {args.days} days")
        if ids and not sample:
            sample = ids[:3]
            o, h, err = client.fetch_orders(sample)
            print(o[["Input Identifier", "Channel Order ID", "Item Status", "Order Date", "Invoice Date",
                 "Dispatch Log", "AWB / Shipment Tracker", "Shipping Company"]].to_string())
            print(f"{len(h)} status-history rows; errors: {err}")
    print("Login OK.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py ajio-tat", description="AJIO dispatch TAT bot")
    p.add_argument("--days", type=int, default=num("TAT_LOOKBACK_DAYS", 1),
                   help="OMSGuru window: today plus this many previous days (default 1 = yesterday + today)")
    p.add_argument("--from", dest="from_date", help="OMSGuru order date from, YYYY-MM-DD (overrides the automatic window)")
    p.add_argument("--to", dest="to_date", help="OMSGuru order date to, YYYY-MM-DD (default today)")
    p.add_argument("--xlsx", action="store_true", help="also write the xlsx master (data/AJIO_TAT_MASTER.xlsx or TAT_XLSX_PATH)")
    p.add_argument("--no-sheet", action="store_true", help="skip Google Sheets (local xlsx master only)")
    p.add_argument("--no-drive", action="store_true", help="ignore TAT_DRIVE_FILE_ID (use the local xlsx)")
    p.add_argument("--no-telegram", action="store_true", help="do not send the Telegram message")
    p.add_argument("--seed-xlsx", help="one-time: load an existing AJIO_TAT_MASTER.xlsx into the master")
    p.add_argument("--probe", action="store_true", help="test OMSGuru login + listing only")
    return run_bot("AJIO Dispatch TAT report", p, lambda a: probe(a) if a.probe else run(a), argv)
