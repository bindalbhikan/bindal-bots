"""Every 3 hours: OMSGuru Push Inventory on every channel with inventory sync on.

The same as Channel > List Channels > (channel) > Related Actions > Push Inventory, one channel
after the other. After the run Telegram gets one message, channel by channel: pushed or failed.
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
    if args.dry_run:
        log.info("dry run: nothing pushed")
        return 0
    lines, failed = [], 0
    for c in channels:
        label = f"{c['channel']} ({c['name'] or c['id']})"
        try:
            log.info("%s: %s", label, client.push_inventory(c["id"]) or "pushed")
            lines.append(f"✅ {telegram.esc(label)}")
        except Exception as exc:  # noqa: BLE001 - the other channels are still pushed
            log.exception("%s: push failed", label)
            failed += 1
            lines.append(f"❌ {telegram.esc(label)}: {telegram.esc(str(exc)[:150])}")
    head = (f"⚠️ <b>OMSGuru inventory push</b>: {failed} of {len(channels)} channels failed" if failed
            else f"📦 <b>OMSGuru inventory push</b>: all {len(channels)} channels pushed")
    msg = f"{head}\n\n" + "\n".join(lines)
    print(msg) if args.no_telegram else telegram.send(msg)
    return 1 if failed else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py inv-push", description="OMSGuru Push Inventory on every channel")
    p.add_argument("--dry-run", action="store_true", help="list the channels only, push nothing")
    p.add_argument("--no-telegram", action="store_true", help="print the summary instead of sending it")
    return run_bot("OMSGuru inventory push", p, run, argv)
