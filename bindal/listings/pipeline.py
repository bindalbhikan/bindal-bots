"""Listing health (11:30 AM): OMSGuru's channel listings against its stock, channels with inventory
sync on only.

* in stock but 0 listed on the channel (and not capped): the stock is not reaching the channel
* listed more than in stock: the channel can sell what is not there (oversell, cancels)
* capped at 0 (Max Listed Qty 0) while in stock: on purpose or forgotten
* sync failures of the last LISTING_FAIL_DAYS (7) days, the Failed tab, and the UnManaged count

Telegram only, nothing written.
"""
from __future__ import annotations

import argparse
import io
import logging
import re
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd

from ..core import telegram
from ..core.app import run_bot
from ..core.config import num
from ..morning.pipeline import channel_line
from ..portals.omsguru import OmsGuruClient
from ..stock.pipeline import stock_of

log = logging.getLogger("bindal.listings")

FAIL_AT = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4} \d{1,2}:\d{2}:\d{2} [AP]M)")
LISTED = 15                                 # rows listed per problem


def table_of(blob: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(blob), dtype=str, low_memory=False)
    out = pd.DataFrame({
        "channel_id": df["Channel ID"].astype(str).str.strip(),
        "channel": df["Channel Name"].map(lambda n: channel_line(str(n))),
        "listing": df["Channel Listing SKU Code"].fillna(df["Channel Listing Id"]).astype(str),
        "sku": df["Product SkuCode"].astype(str).str.strip().str.upper(),
        "listed": pd.to_numeric(df["Channel Listed Qty"], errors="coerce").fillna(0).astype(int),
        "max": pd.to_numeric(df["Channel Max Listed Qty"], errors="coerce").fillna(-1).astype(int),
        "failure": df["Failure Reason"].fillna("").astype(str)})
    return out


def failed_since(text: str, since: datetime) -> bool:
    m = FAIL_AT.match(text or "")
    try:
        return bool(m) and datetime.strptime(m.group(1), "%B %d, %Y %I:%M:%S %p") >= since
    except ValueError:
        return False


def check(listings: pd.DataFrame, stock: Counter, active: set[str], since: datetime) -> dict:
    df = listings[listings["channel_id"].isin(active)].copy()
    df["stock"] = [max(stock.get(s, 0), 0) for s in df["sku"]]
    instock = df["stock"] > 0
    return {
        "listings": len(df),
        "not_listed": df[instock & (df["max"] != 0) & (df["listed"] == 0)],
        "over": df[df["listed"] > df["stock"]],
        "capped": df[instock & (df["max"] == 0)],
        "failed": df[df["failure"].map(lambda f: failed_since(f, since))],
    }


def message(res: dict, failed_tab: list[dict], unmanaged: int, now: datetime, days: int) -> str:
    def per_channel(part: pd.DataFrame) -> str:
        return " · ".join(f"{telegram.esc(c)} {n}" for c, n in part["channel"].value_counts().items())

    def rows(part: pd.DataFrame, show) -> list[str]:
        """One line per channel + SKU (a SKU can have several listings on one channel)."""
        n = part.groupby(["channel", "sku"]).size()
        uniq = part.drop_duplicates(["channel", "sku"])
        return [f"• {telegram.esc(r.channel)} · {telegram.esc(r.sku)} · {show(r)}"
                + (f" · {n[(r.channel, r.sku)]} listings" if n[(r.channel, r.sku)] > 1 else "")
                for r in uniq.head(LISTED).itertuples()] + (
                    [f"... and {len(uniq) - LISTED} more"] if len(uniq) > LISTED else [])

    out = [f"🏪 <b>Listing health · {now:%d %b}</b>",
           f"<i>{res['listings']:,} listings on channels with inventory sync on, against OMSGuru stock</i>"]
    nl, ov = res["not_listed"], res["over"].sort_values("listed", ascending=False)
    out += ["", f"📭 <b>In stock, 0 on the channel: {len(nl)}</b>" + (f" ({per_channel(nl)})" if len(nl) else "")]
    out += rows(nl.sort_values("stock", ascending=False), lambda r: f"stock {r.stock}")
    out += ["", f"⚠️ <b>Listed more than in stock: {len(ov)}</b>" + (f" ({per_channel(ov)})" if len(ov) else ""),
            *rows(ov, lambda r: f"listed {r.listed}, stock {r.stock}")]
    fl = res["failed"]
    out += ["", f"❌ <b>Sync failures in the last {days} days: {len(fl)}</b>" + (f" ({per_channel(fl)})" if len(fl) else "")]
    reasons = Counter(re.sub(r"^.*?[AP]M:", "", f).strip()[:70] for f in fl["failure"])
    out += [f"• {telegram.esc(r)}: {n}" for r, n in reasons.most_common(5)]
    if failed_tab:
        out += ["", f"🚫 <b>Failed listings tab: {len(failed_tab)}</b>",
                *[f"• {telegram.esc(channel_line(f['channel']))} · {telegram.esc(f['sku'])} · "
                  f"{telegram.esc(re.sub(r'^.*?[AP]M:', '', f['failure'])[:80])}" for f in failed_tab[:LISTED]]]
    cp = res["capped"]
    out += ["", f"🔒 Capped at 0 while in stock: {len(cp)}" + (f" ({per_channel(cp)})" if len(cp) else ""),
            f"🔗 Not mapped to an OMSGuru SKU (UnManaged): {unmanaged:,}",
            "", "<i>Fix: push inventory / check the mapping for 📭, re-sync or lower the listed qty for ⚠️.</i>"]
    return "\n".join(out)


def run(args) -> int:
    now = datetime.now()
    days = int(num("LISTING_FAIL_DAYS", 7))
    client = OmsGuruClient.from_env()
    client.login()
    active = {c["id"] for c in client.channels() if c["inventory_sync"]}
    warehouses = list(client.warehouses()) or [None]
    blobs = []
    for wh in warehouses:
        if wh:
            client.set_warehouse(wh)
        blobs.append(client.export_inventory(wait_minutes=num("STOCK_EXPORT_WAIT_MINUTES", 12)))
    stock = stock_of(blobs)
    if warehouses[0]:
        client.set_warehouse(warehouses[0])     # the listing pages show the current warehouse's listings
    listings = table_of(client.export_listings(wait_minutes=num("LISTING_EXPORT_WAIT_MINUTES", 15)))
    if listings.empty:
        raise RuntimeError("The channel listing export came back empty")
    res = check(listings, stock, active, now - timedelta(days=days))
    failed_tab, unmanaged = client.failed_listings(), client.unmanaged_count()
    log.info("Listings: %d checked, %d in stock not listed, %d over stock, %d capped, %d failed lately, "
             "%d in the Failed tab, %d unmanaged", res["listings"], len(res["not_listed"]), len(res["over"]),
             len(res["capped"]), len(res["failed"]), len(failed_tab), unmanaged)
    text = message(res, failed_tab, unmanaged, now, days)
    print(text) if args.no_telegram else telegram.send(text)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py listings", description="Channel listings against OMSGuru stock")
    p.add_argument("--no-telegram", action="store_true", help="print the report instead of sending it")
    return run_bot("Listing health", p, run, argv)
