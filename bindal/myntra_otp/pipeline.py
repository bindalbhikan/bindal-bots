"""Myntra courier OTPs (11 AM): today's PICKUP and RETURN one-time codes of the TLPL, MMRJ and BOAT
warehouses, from the partner portal's Orders > OTC page (api/location/otc), to Telegram.

Each OMSGuru channel is one Myntra seller account and its Surat warehouse (the warehouse code
C45111W17453 names channel 45111): TLPL = MAIN 46148, MMRJ = DBCC 50829, BOAT = FMCC 56765
(MYNTRA_OTP_SITES, code:account:warehouse). A courier with no trip today has no code ("-").
The codes go to Telegram only: nothing is logged or written.
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from ..core import telegram
from ..core.app import run_bot
from ..core.config import env

log = logging.getLogger("bindal.myntra_otp")

SITES = "TLPL:MAIN:46148,MMRJ:DBCC:50829,BOAT:FMCC:56765"
TRIPS = (("PICKUP", "Pickup"), ("RETURN", "Return"))


def sites() -> list[tuple[str, str, str]]:
    return [tuple(s.strip() for s in p.split(":")) for p in (env("MYNTRA_OTP_SITES") or SITES).split(",")
            if p.count(":") == 2]


def read_codes(portal_of=None) -> tuple[list[tuple[str, str, dict[str, str]]], list[str], list[str]]:
    """[(code, trip label, {courier: otc or "-"})], the couriers seen, and notes for sites not read."""
    from ..portals import myntra as mp
    from ..return_analysis.sources import myntra_portal
    portal_of = portal_of or myntra_portal
    rows, couriers, notes = [], [], []
    for code, account, warehouse in sites():
        try:
            portal = portal_of(account)
            for trip, label in TRIPS:
                got = portal.otc(warehouse, trip)
                for o in got:
                    if o["courier"] not in couriers:
                        couriers.append(o["courier"])
                rows.append((code, label, {o["courier"]: o["otc"] or "-" for o in got}))
            if hasattr(portal, "cookies"):
                mp.save_to_worker(env, portal.cookies(), account)
        except Exception as exc:  # noqa: BLE001 - the other accounts still go out
            log.warning("%s (%s) OTPs not read: %s", code, account, str(exc)[:150])
            notes.append(f"{code}: not read ({str(exc)[:80]})")
    return rows, couriers, notes


def message(rows, couriers, notes, now: datetime) -> str:
    """A narrow table (fits a phone): one line per account and trip, one column per courier."""
    head = f"{'Acct':<5}{'Trip':<7}" + "".join(f"{c:<8}" for c in couriers)
    lines = [head.rstrip()] + [f"{code:<5}{label:<7}" + "".join(f"{codes.get(c, '-'):<8}" for c in couriers)
                               for code, label, codes in rows]
    out = [f"🔐 <b>Myntra courier OTP · {now:%d %b %Y}</b>",
           "<i>Pickup = forward pickup, Return = return handover; - = no trip today</i>"]
    if rows:
        out.append("<pre>" + telegram.esc("\n".join(line.rstrip() for line in lines)) + "</pre>")
    if notes:
        out += ["⚠️ " + telegram.esc(n) for n in notes]
    return "\n".join(out)


def run(args) -> int:
    rows, couriers, notes = read_codes()
    text = message(rows, couriers, notes, datetime.now())
    print(text) if args.no_telegram else telegram.send(text)
    return 0 if rows else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py myntra-otp", description="Myntra courier pickup / return OTPs")
    p.add_argument("--no-telegram", action="store_true", help="print the message instead of sending it")
    return run_bot("Myntra courier OTP", p, run, argv)
