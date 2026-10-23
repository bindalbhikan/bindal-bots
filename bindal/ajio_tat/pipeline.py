"""Daily run: OMSGuru -> courier tracking -> TAT rules -> Google Sheet -> Telegram."""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..awb_tracker import CourierRegistry, Job, track_all
from ..core import dashboard, telegram
from ..core.app import run_bot
from ..core.config import ROOT, csv_list, env, num, output_dir
from ..portals.omsguru import OmsGuruClient
from .tat_engine import (OPEN_PICKUP, apply_status, apply_tracking,
                         needs_oms_refresh, build_report, merge_master, normalize_master,
                         prepare_tracking, summarize, write_excel)

KEEP_BACKUPS = 30
MASTER_NAME = "AJIO_TAT_MASTER.xlsx"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
log = logging.getLogger("bindal.ajio_tat")


def _key(s):
    return s.astype(str).str.strip().str.upper().str.replace(r"\.0$", "", regex=True)


def ajio_order_report(first, last) -> pd.DataFrame:
    """The AJIO Dropship order report for order dates first..last, read 30 days at a time."""
    from ..portals.ajio import AjioClient
    client = AjioClient(env("AJIO_USERNAME"), env("AJIO_PASSWORD"))
    client.login()
    frames, a = [], first
    while a <= last:
        b = min(a + timedelta(days=29), last)
        data = client.order_report(a, b)
        if data:
            frames.append(pd.read_excel(io.BytesIO(data), dtype=str))
        a = b + timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def ajio_report(df, errors: list[str], since=None):
    """From the AJIO Dropship order report: Cust Order No (AJIO's FN... order number, matched on
    the seller order = Order ID) where missing, and the Pickup Time: AJIO's "Pickup Timestamp"
    (the report's last column, matched on AWB, else seller invoice) is THE pickup time, so it
    replaces the courier tracking's time wherever AJIO has one; courier tracking only fills
    orders AJIO shows no pickup for yet. Read 30 days at a time over the order dates from `since`
    (the OMSGuru window) plus older orders still missing either; an AJIO failure is noted and
    the courier / OMSGuru values stand."""
    if not env("AJIO_USERNAME"):
        return df
    no_pick = df["Pickup Time"].isna() & df["AWB"].notna()
    no_cust = df["Cust Order No"].isna()
    recent = df["Order Time"] >= pd.Timestamp(since) if since is not None else False
    want = (no_pick | no_cust | (recent & df["AWB"].notna())) & df["Order Time"].notna()
    if not want.any():
        return df
    first, last = df.loc[want, "Order Time"].min().date(), df.loc[want, "Order Time"].max().date()
    try:
        rep = ajio_order_report(first, last)
    except Exception as exc:  # noqa: BLE001 - courier / OMSGuru values stand
        log.exception("AJIO order report not read")
        errors.append(f"AJIO order report not read: {str(exc)[:100]}")
        return df
    if rep.empty:
        return df
    df = df.copy()

    named = rep.dropna(subset=["Cust Order No"])
    cust = named.groupby(_key(named["FWD Seller Order NO"]))["Cust Order No"].first()
    cust_awb = named.groupby(_key(named["FWD AWB"]))["Cust Order No"].first()
    got = _key(df["Order ID"]).map(cust).fillna(_key(df["AWB"]).map(cust_awb))
    fill_cust = want & no_cust & got.notna()
    df.loc[fill_cust, "Cust Order No"] = got[fill_cust]

    rep["ts"] = pd.to_datetime(rep["Pickup Timestamp"].astype(str).str.replace(" IST", "", regex=False),
                               format="%a %b %d %H:%M:%S %Y", errors="coerce")
    picked = rep.dropna(subset=["ts"])
    by_awb = picked.groupby(_key(picked["FWD AWB"]))["ts"].min()
    by_inv = picked.groupby(_key(picked["Seller Invoice No"]))["ts"].min()
    found = _key(df["AWB"]).map(by_awb).fillna(_key(df["Invoice No"]).map(by_inv))
    fill = want & found.notna()
    changed = fill & ~no_pick & (pd.to_datetime(df["Pickup Time"], errors="coerce") != found)
    df.loc[fill, "Pickup Time"] = found[fill]
    log.info("AJIO order report %s..%s: %d lines, %d pickup times from AJIO (%d new, %d replacing the "
             "courier's time), %d Cust Order Nos filled", first, last, len(rep), int(fill.sum()),
             int((fill & no_pick).sum()), int(changed.sum()), int(fill_cust.sum()))
    return df


def erp_labels(df, since: datetime, errors: list[str]):
    """ERP Label Time / ERP Label By from the ERP packing report (Shipping Label Print), portal
    AJIO, whose "Order ID" is the AWB. Read from the OMSGuru window's start (a week at least).
    An ERP failure is noted and the old values stay."""
    if not env("ERP_USERNAME"):
        log.info("ERP not configured: label prints not checked")
        return df
    from ..portals.erp import Erp
    from .tat_engine import apply_erp_labels
    first = min(since.date(), (datetime.now() - timedelta(days=7)).date())
    try:
        erp = Erp.from_env()
        erp.login()
        labels: dict[str, tuple] = {}
        for line in erp.packed("AJIO", first):
            awb = str(line.get("Order ID") or "").strip().upper()
            when = line.get("Created On") or line.get("Voucher Date")
            if awb and when is not None and (awb not in labels or str(when) < str(labels[awb][0])):
                labels[awb] = (when, line.get("Created By") or "")
    except Exception as exc:  # noqa: BLE001 - the TAT report still goes out
        log.warning("ERP label prints not read: %s", exc)
        errors.append(f"ERP label prints not read: {exc}")
        return df
    df, n = apply_erp_labels(df, labels)
    log.info("ERP: %d AJIO label prints since %s, %d new in the master", len(labels), first, n)
    return df


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
    # in a Google Drive for desktop folder (a local run from G:) the swapped-in file came out
    # broken once (bad CRC in docProps/core.xml): never upload such a file
    try:
        bad = zipfile.ZipFile(path).testzip()
    except zipfile.BadZipFile as exc:
        bad = str(exc)
    if bad:
        raise RuntimeError(f"Saved master is damaged ({bad}), not uploaded: {path}")
    log.info("Master xlsx saved: %s", path)


# ------------------------------------------------------------------ steps

def warehouse_ids() -> list[str]:
    return csv_list("OMSGURU_WAREHOUSE_IDS")


def csv_list_of(text: str | None) -> list[str]:
    return list(dict.fromkeys(t.upper() for t in re.split(r"[\s,]+", text or "") if t))


def seller_orders(numbers: list[str]) -> list[str]:
    """AJIO numbers as OMSGuru knows them: a customer order number (FN... / EX...) becomes the
    seller order number through the AJIO order report of the last ORDERS_LOOKBACK_DAYS (180);
    seller order numbers, invoices and AWBs are kept as they are."""
    cust = [n for n in numbers if re.fullmatch(r"[A-Z]{2}\d{8,}", n)]
    if not cust:
        return numbers
    today = datetime.now().date()
    rep = ajio_order_report(today - timedelta(days=num("ORDERS_LOOKBACK_DAYS", 180)), today)
    to_seller = (rep.dropna(subset=["Cust Order No"]).drop_duplicates("Cust Order No")
                 .set_index("Cust Order No")["FWD Seller Order NO"]) if len(rep) else pd.Series(dtype=str)
    missing = [n for n in cust if n not in to_seller.index]
    if missing:
        log.warning("Not in the AJIO order report: %s", ", ".join(missing))
    return [n for n in numbers if n not in cust] + [to_seller[n] for n in cust if n in to_seller.index]


def fetch_oms(start: datetime, end: datetime, skip_channel_orders: set[str], extra: list[str] = ()):
    """The window's AJIO orders; extra: order numbers to read too, whatever their date (--orders)."""
    client = OmsGuruClient.from_env(workers=num("OMSGURU_WORKERS", 6))
    client.login()
    channels = csv_list("AJIO_CHANNEL_IDS", "57285,34958")
    log.info("Listing AJIO orders %s -> %s (channels %s)", start, end, channels)
    o, hist, errors = client.fetch_window(start, end, channels, warehouse_ids(), skip_channel_orders)
    if extra:
        terms, ids = seller_orders(list(extra)), []
        for wh in warehouse_ids() or list(client.warehouses()) or [None]:
            if wh:
                client.set_warehouse(wh)
            for t in terms:
                ids += [i for i in client.search(t, "order") if i not in ids]
        log.info("--orders: %d numbers, %d OMSGuru orders found", len(extra), len(ids))
        o2, h2, e2 = client.fetch_orders(ids)
        o, hist, errors = pd.concat([o, o2], ignore_index=True), pd.concat([hist, h2], ignore_index=True), errors + e2
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


# Telegram on a phone shows ~28 monospace characters per line: every <pre> table stays within that.
SHORT_COURIER = {"BLUEDART": "BluDart", "DELHIVERY": "Delhvry", "SHADOWFAX": "Shdwfax",
                 "XPRESSBEES": "XpresBz", "EKART": "Ekart", "VALMO": "Valmo", "UNKNOWN": "?"}


def short_courier(name) -> str:
    key = str(name or "UNKNOWN").upper().replace(" ", "")
    for k, v in SHORT_COURIER.items():
        if k in key:
            return v
    return str(name).title()[:7]


def _summary_block(part: pd.DataFrame, title: str, now: pd.Timestamp) -> list[str]:
    """Order count, processing on time / late / pending, and a narrow per-courier table:
    Done = picked up, Pend = pickup pending: processed but not picked and the TAT deadline has passed,
    Due = processed, not picked, deadline not reached yet, Proc = not processed."""
    proc = part["Process Status"].value_counts()
    lines = ["", f"<b>{title} · {len(part)} orders</b>",
             f"✅ {proc.get('On Time', 0)} on time · ⏰ {proc.get('Late', 0)} late · "
             f"⏳ {proc.get('Not Processed', 0)} pending"]
    if part.empty:
        return lines
    rows = []
    for courier, g in part.groupby(part["Courier"].fillna("UNKNOWN"), sort=True):
        not_proc = g["Process Status"] == "Not Processed"
        picked = g["Pickup Status"].isin(PICKED)
        waiting = ~picked & ~not_proc
        overdue = pickup_pending_mask(g, now)
        rows.append((short_courier(courier), int(picked.sum()),
                     int(overdue.sum()), int((waiting & ~overdue).sum()), int(not_proc.sum())))
    table = [f"{'Courier':<8}{'Done':>5}{'Pend':>5}{'Due':>5}{'Proc':>5}"]
    table += [f"{c:<8}{d:>5}{p:>5}{x:>5}{n:>5}" for c, d, p, x, n in rows]
    table.append(f"{'Total':<8}" + "".join(f"{sum(r[i] for r in rows):>5}" for i in range(1, 5)))
    lines.append("<pre>" + telegram.esc("\n".join(table)) + "</pre>")
    return lines


def telegram_report(df):
    """Mobile-friendly summary: yesterday, today, and the current month to date
    (by order date, cancelled orders left out)."""
    now = pd.Timestamp.now()
    today = now.normalize()
    live = df[df["Pickup Status"] != "Cancelled"]
    order_day = live["Order Time"].dt.normalize()
    lines = [f"📦 <b>AJIO Dispatch · {today:%d %b %Y}</b>"]
    for day in (today - pd.Timedelta(days=1), today):
        lines += _summary_block(live[order_day == day], f"📅 {day:%d %b}", now)
    month_start = today.replace(day=1)
    lines += _summary_block(live[(order_day >= month_start) & (order_day <= today)],
                            f"🗓 {today:%B} 1-{today.day}", now)
    lines += ["", "<i>Done = picked up",
              "Pend = not picked up, TAT passed",
              "Due = not picked up, still within TAT",
              "Proc = processing pending</i>"]
    return "\n".join(lines)


def pickup_pending_messages(df, now=None) -> list[str]:
    """Second Telegram message: every processed AWB still waiting for pickup (last TRACK_DAYS days),
    TAT passed ("Over") first, then still within TAT ("Due"), with a short ready-to-copy mail
    for the courier / AJIO. The mail is plain text (wraps on a phone, pastes cleanly); only the
    AWB list is a table. Empty list when nothing is waiting."""
    now = now or pd.Timestamp.now()
    since = now.normalize() - pd.Timedelta(days=num("TRACK_DAYS", 15))
    pend = df[awaiting_pickup_mask(df) & (df["Order Time"] >= since)].copy()
    if pend.empty:
        return []
    pend["_over"] = pend["Dispatch Deadline"] < now
    pend = pend.sort_values(["_over", "Courier", "RTS Time"], ascending=[False, True, True])
    n_over = int(pend["_over"].sum())
    pob = env("POB_ID", "DV00348890")
    rows = [f"{str(r['AWB'])[:15]:<15} {short_courier(r['Courier'])[:7]:<7} {'Over' if r['_over'] else 'Due'}"
            for _, r in pend.iterrows()]
    per_msg = 100  # keeps each message under Telegram's 4096-character limit
    msgs = []
    for i in range(0, len(rows), per_msg):
        part = rows[i:i + per_msg]
        first, last = i == 0, i + per_msg >= len(rows)
        head = ([f"📮 <b>Pickup Pending · {len(pend)} AWB</b>",
                 f"🔴 Over (TAT passed) {n_over} · 🟡 Due {len(pend) - n_over}",
                 "",
                 f"<b>Subject:</b> Pickup Pending - POB ID {pob} - {len(pend)} shipments",
                 "",
                 "Dear Team,",
                 "Below shipments are packed and ready, but pickup is still pending. "
                 "Kindly arrange the pickup today on priority.",
                 f"POB ID: {pob}"] if first
                else [f"📮 <b>Pickup Pending</b> (contd. {i // per_msg + 1})"])
        table = "<pre>" + telegram.esc(f"{'AWB':<15} {'Courier':<7} TAT\n" + "\n".join(part)) + "</pre>"
        tail = ["Regards,", "Bindal Exports"] if last else []
        msgs.append("\n".join(head + [table] + tail))
    return msgs


SCORE_COMMENT = """You are the e-commerce manager of Bindal Exports, an AJIO seller. Below is the courier
pickup scorecard of the last 7 order days. Write 2 short lines for the owner in plain English: which
courier is the problem and how bad, and whether the delay is on our side (processing late) or the
courier's (pickup late). Numbers exact, no markdown, no greeting; say only what the numbers show."""


def courier_scorecard(df: pd.DataFrame, now: pd.Timestamp | None = None) -> str:
    """Third Telegram message: per courier, orders of the last COURIER_DAYS (7) complete order
    days, pickups on time / late / overdue and the on-time %; who was late on the last day's NOT
    CORRECT orders (our processing or the courier's pickup); and an alert for a courier under
    COURIER_ALERT_PCT (70) % on time COURIER_ALERT_DAYS (3) order days in a row. "" when no orders.

    A day is complete when every deadline of its orders has passed: an order of day D is due by
    D + 1 (10:10 rule), so at the 5:30 PM run the last complete day is the day before yesterday."""
    now = now or pd.Timestamp.now()
    yday = now.normalize() - pd.Timedelta(days=2)       # the last complete order day
    first = yday - pd.Timedelta(days=num("COURIER_DAYS", 7) - 1)
    live = df[df["Pickup Status"] != "Cancelled"].copy()
    live["_day"] = live["Order Time"].dt.normalize()
    live = live[(live["_day"] >= first) & (live["_day"] <= yday)]
    if live.empty:
        return ""
    live["_on"] = live["Pickup Status"] == "On Time"
    live["_late"] = (live["Pickup Status"] == "Late") | pickup_pending_mask(live, now)
    live["_c"] = live["Courier"].fillna("UNKNOWN").map(short_courier)

    def pct(g) -> int | None:
        n = int(g["_on"].sum() + g["_late"].sum())
        return round(100 * g["_on"].sum() / n) if n else None

    rows, alerts = [], []
    days = [first + pd.Timedelta(days=i) for i in range((yday - first).days + 1)]
    limit, run_len = num("COURIER_ALERT_PCT", 70), int(num("COURIER_ALERT_DAYS", 3))
    for courier, g in sorted(live.groupby("_c"), key=lambda kv: -len(kv[1])):
        y = pct(g[g["_day"] == yday])
        rows.append(f"{courier[:7]:<7}{len(g):>5}{int(g['_late'].sum()):>5}"
                    f"{(str(pct(g)) + '%') if pct(g) is not None else '-':>6}{(str(y) + '%') if y is not None else '-':>6}")
        streak = []
        for d in reversed(days):
            gd = g[g["_day"] == d]
            p = pct(gd)
            if p is None or len(gd) < 5 or p >= limit:
                break
            streak.append(p)
        if len(streak) >= run_len:
            alerts.append(f"⚠️ {courier}: pickup on time under {limit:.0f}% for {len(streak)} days in a row "
                          f"({', '.join(f'{p}%' for p in reversed(streak))})")
    tot = pct(live)
    table = [f"{'Courier':<7}{'Ord':>5}{'Late':>5}{'OnT%':>6}{format(yday, '%d%b'):>6}", *rows,
             f"{'Total':<7}{len(live):>5}{int(live['_late'].sum()):>5}{(str(tot) + '%') if tot is not None else '-':>6}"]
    bad = live[(live["_day"] == yday) & (live["TAT Result"] == "NOT CORRECT")]
    ours = int((bad["Process Status"] != "On Time").sum())
    lines = [f"🚚 <b>AJIO Courier Pickup · {first:%d %b} - {yday:%d %b}</b>",
             "<i>order days whose deadlines have all passed</i>",
             "<pre>" + telegram.esc("\n".join(table)) + "</pre>",
             f"NOT CORRECT on {yday:%d %b}: <b>{len(bad)}</b> · our side (processing late) {ours} · "
             f"courier side (pickup late) {len(bad) - ours}",
             *alerts,
             "<i>Late = picked after the deadline or still waiting past it; OnT% = on time / (on time + late); "
             "picked with no time known is left out</i>"]
    try:
        from ..agent import llm
        plain = "\n".join(table) + f"\nNOT CORRECT on {yday:%d %b}: {len(bad)}, ours {ours}, courier {len(bad) - ours}\n" + "\n".join(alerts)
        note = (llm.chat([{"role": "system", "content": SCORE_COMMENT}, {"role": "user", "content": plain}],
                         max_tokens=1200)["choices"][0]["message"].get("content") or "").strip()
        if note:
            lines += ["", "🧠 " + telegram.esc(note)]
    except Exception as exc:  # noqa: BLE001 - the scorecard goes out without the lines
        log.warning("No scorecard comment: %s", exc)
    return "\n".join(lines)


def dashboard_data(df: pd.DataFrame) -> dict:
    """Day-wise TAT for the last 45 order days, the month's courier table, and every AWB
    still waiting for pickup."""
    now = pd.Timestamp.now()
    live = df[df["Pickup Status"] != "Cancelled"].copy()
    live["_day"] = live["Order Time"].dt.normalize()
    days = []
    for day, g in live[live["_day"] >= now.normalize() - pd.Timedelta(days=44)].groupby("_day"):
        proc, res = g["Process Status"].value_counts(), g["TAT Result"].value_counts()
        days.append({"date": day.date(), "orders": len(g),
                     "on_time": int(proc.get("On Time", 0)), "late": int(proc.get("Late", 0)),
                     "not_processed": int(proc.get("Not Processed", 0)),
                     "picked": int(g["Pickup Status"].isin(PICKED).sum()),
                     "pickup_overdue": int(pickup_pending_mask(g, now).sum()),
                     "correct": int(res.get("CORRECT", 0)), "not_correct": int(res.get("NOT CORRECT", 0)),
                     "pending": int(res.get("PENDING", 0))})
    month = live[live["_day"] >= now.normalize().replace(day=1)]
    couriers = []
    for courier, g in month.groupby(month["Courier"].fillna("UNKNOWN")):
        not_proc = g["Process Status"] == "Not Processed"
        picked = g["Pickup Status"].isin(PICKED)
        overdue = pickup_pending_mask(g, now)
        couriers.append({"courier": short_courier(courier), "orders": len(g), "done": int(picked.sum()),
                         "pend": int(overdue.sum()), "due": int((~picked & ~not_proc & ~overdue).sum()),
                         "proc": int(not_proc.sum())})
    since = now.normalize() - pd.Timedelta(days=num("TRACK_DAYS", 15))
    pend = df[awaiting_pickup_mask(df) & (df["Order Time"] >= since)].sort_values("Order Time")
    pickup = [{"awb": str(r["AWB"]), "courier": short_courier(r["Courier"]), "over": bool(r["Dispatch Deadline"] < now),
               "order_time": r["Order Time"], "ready": r["RTS Time"] if pd.notna(r["RTS Time"]) else None}
              for _, r in pend.iterrows()][:500]
    return {"at": now, "days": days, "couriers": couriers, "pickup_pending": pickup}


# ------------------------------------------------------------------ main

def run(args) -> int:
    mode = env("RUN_MODE", "local")
    out = output_dir("ajio_tat")
    sheet = None
    sheet_id = env("TAT_GOOGLE_SHEET_ID")
    if sheet_id and not args.no_sheet:
        from .sheets import MasterSheet
        sheet = MasterSheet(sheet_id)
    drive = folder = None
    if not args.no_drive and env("TAT_DRIVE_FOLDER_ID"):
        # the master lives in a Drive folder (AJIO/Ajio Daily Order Tat Check Working) by name:
        # updated in place when it is there, created when it is not (e.g. moved or deleted)
        from ..core.drive import DriveFile, DriveFolder
        folder = DriveFolder(env("TAT_DRIVE_FOLDER_ID"))
        found = folder.children().get(MASTER_NAME)
        drive = DriveFile(found["id"]) if found else None
        if not found:
            log.info("No %s in the Drive folder yet: it will be created", MASTER_NAME)
    elif not args.no_drive and env("TAT_DRIVE_FILE_ID"):
        from ..core.drive import DriveFile
        drive = DriveFile(env("TAT_DRIVE_FILE_ID"))
    drive_copy = out / "drive" / MASTER_NAME

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
    elif folder and env("TAT_DRIVE_FILE_ID"):
        # no master in the folder yet: start from the old master file (readable even in the trash)
        from ..core.drive import DriveFile
        old_file = DriveFile(env("TAT_DRIVE_FILE_ID"))
        old, old_as_of = read_master_xlsx(old_file.download(out / "seed" / MASTER_NAME))
        log.info("Master seeded from the old file %s", old_file.name)
    elif folder:
        old, old_as_of = normalize_master(pd.DataFrame()), None
    elif sheet:
        old, old_as_of = sheet.read_master()
    else:
        old, old_as_of = read_master_xlsx(master_xlsx())
    log.info("Master loaded: %d orders", len(old))

    # 2) OMSGuru, date-wise: open order pages only for orders that are new or still open
    old_status = apply_status(old, pd.Timestamp.now()) if len(old) else old.assign(**{"Pickup Status": None})
    complete = set(old_status.loc[~needs_oms_refresh(old_status), "Order ID"].dropna().astype(str))
    start, end = oms_window(args)
    o, hist, errors = fetch_oms(start, end, complete, csv_list_of(args.orders))

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
    merged = ajio_report(erp_labels(merged, start, errors), errors, since=start)
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
    elif folder:
        save_master_xlsx(df, drive_copy, note, as_of)
        folder.put(drive_copy, XLSX)
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
        report += ("\n\n\u26a0\ufe0f <b>Notes</b>\n"
                   f"\u2022 {len(errors)} order page / tracking lookups failed; those orders keep their "
                   "last known status (list in the run's errors.log)")
    score = courier_scorecard(df)
    if args.no_telegram:
        print(report)
        print(score)
    else:
        telegram.send(report)
        if score:
            telegram.send(score)
        for msg in pickup_pending_messages(df):
            telegram.send(msg)
    dashboard.publish("ajio_tat", {**dashboard_data(df), "lookup_errors": len(errors)})
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
    p.add_argument("--orders", help="also add these AJIO orders, whatever their date: comma / space separated "
                                     "FN... / EX... customer order numbers, seller order numbers, invoices or AWBs")
    p.add_argument("--probe", action="store_true", help="test OMSGuru login + listing only")
    return run_bot("AJIO Dispatch TAT report", p, lambda a: probe(a) if a.probe else run(a), argv, key="ajio_tat")
