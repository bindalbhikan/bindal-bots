"""Every 3 hours: OMSGuru Push Inventory on every channel with inventory sync on.

The same as Channel > List Channels > (channel) > Related Actions > Push Inventory, one channel
after the other. Telegram hears only of channels that failed (or of the run failing).
"""
from __future__ import annotations

import argparse
import logging

from ..core import telegram
from ..core.app import run_bot
from ..core.config import csv_list
from ..portals.omsguru import OmsGuruClient

log = logging.getLogger("bindal.inv_push")


def run(args) -> int:
    client = OmsGuruClient.from_env()
    client.login()
    skip = set(csv_list("INV_PUSH_SKIP"))           # channel ids never pushed
    channels = [c for c in client.channels() if c["inventory_sync"] and c["id"] not in skip]
    if not channels:
        raise RuntimeError("No OMSGuru channel with inventory sync on found (Channel > List Channels)")
    log.info("Pushing inventory on %d channels: %s", len(channels),
             ", ".join(f"{c['channel']} ({c['id']})" for c in channels))
    failed = []
    for c in channels:
        label = f"{c['channel']} ({c['id']})"
        if args.dry_run:
            log.info("dry run: %s not pushed", label)
            continue
        try:
            log.info("%s: %s", label, client.push_inventory(c["id"]) or "pushed")
        except Exception as exc:  # noqa: BLE001 - the other channels are still pushed
            log.exception("%s: push failed", label)
            failed.append(f"{label}: {str(exc)[:150]}")
    if failed:
        msg = (f"⚠️ <b>OMSGuru inventory push</b>: {len(failed)} of {len(channels)} channels failed\n\n"
               + "\n".join(f"• {telegram.esc(f)}" for f in failed))
        print(msg) if args.no_telegram else telegram.send(msg)
        return 1
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py inv-push", description="OMSGuru Push Inventory on every channel")
    p.add_argument("--dry-run", action="store_true", help="list the channels only, push nothing")
    p.add_argument("--no-telegram", action="store_true", help="print failures instead of sending them")
    return run_bot("OMSGuru inventory push", p, run, argv)
