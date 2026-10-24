"""Myntra courier OTPs (11 AM): today's PICKUP and RETURN one-time codes of the TLPL, MMRJ and BOAT
warehouses, from the partner portal's Orders > OTC page (api/location/otc), to Telegram.

Each OMSGuru channel is one Myntra seller account and its Surat warehouse (the warehouse code
C45111W17453 names channel 45111): TLPL = MAIN 46148, MMRJ = DBCC 50829, BOAT = FMCC 56765
(MYNTRA_OTP_SITES, code:account:warehouse). A courier with no trip today has no code ("-").

The codes go to Telegram and into the team's "Daily OTP 2026" Google Sheet (MYNTRA_OTP_SHEET_ID),
one row per code in its own layout: DATE, PORTAL, COUNT (left empty, filled in by hand), OTP,
STATUS (PICKUP / RETURN), Name (BOT) and the courier as the team writes it (MYE = EK, MYS = FL).
A code the sheet already has for that date, portal and status is not added again. Nothing is logged.
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


ACCOUNT_NAMES = {"TLPL": "Myntra Main", "MMRJ": "Myntra DBCC", "BOAT": "Myntra FF X MUR CC"}


def message(rows, couriers, notes, now: datetime) -> str:
    """Plain lines, one per account and trip, each code named by its courier as the team writes it
    (EK / FL): it still reads right when copied out of Telegram into WhatsApp."""
    out = [f"🔐 <b>Myntra courier OTP · {now:%d %b %Y}</b>"]
    last = None
    for code, label, codes in rows:
        if code != last:
            out += ["", f"<b>{telegram.esc(code)}</b> ({telegram.esc(ACCOUNT_NAMES.get(code, code))})"]
            last = code
        parts = [f"{COURIER_TAGS.get(c, c)}: {codes[c] if codes.get(c, '-') != '-' else 'no trip'}"
                 for c in sorted(codes, key=lambda c: COURIER_TAGS.get(c, c))]
        out.append(f"{label}: " + " | ".join(telegram.esc(p) for p in parts))
    out += ["", "Pickup = forward pickup OTP, Return = return handover OTP"]
    if notes:
        out += [""] + ["⚠️ " + telegram.esc(n) for n in notes]
    return "\n".join(out)


SHEET_ID = "1dKfym74siyYHPM-JgisDq_2LcOckiqod59aibOAHbAA"
PORTAL_NAMES = {"TLPL": "Myntra_Main_TLPL", "MMRJ": "Myntra_DBCC_MMRJ", "BOAT": "Myntra_FF X MUR CC_BOAT"}
COURIER_TAGS = {"MYE": "EK", "MYS": "FL"}


def sheet_rows(rows, now: datetime, existing: list[list[str]]) -> list[list[str]]:
    """The sheet's new rows: one per code, none the sheet already has (same date, portal, OTP, status)."""
    have = {(r[0].strip(), r[1].strip(), r[3].strip(), r[4].strip().upper())
            for r in (list(map(str, x)) + [""] * 5 for x in existing)}
    day, out = f"{now:%d-%m-%Y}", []
    for code, label, codes in rows:
        for courier, otc in codes.items():
            if otc in (None, "", "-"):
                continue
            row = [day, PORTAL_NAMES.get(code, f"Myntra_{code}"), "", str(otc), label.upper(), "BOT",
                   COURIER_TAGS.get(courier, courier)]
            if (row[0], row[1], row[3], row[4]) not in have:
                have.add((row[0], row[1], row[3], row[4]))
                out.append(row)
    return out


def write_sheet(rows, now: datetime) -> int:
    """Append today's codes to the OTP sheet; the number of rows added."""
    import gspread
    from ..core.gauth import credentials
    ws = gspread.authorize(credentials(["https://www.googleapis.com/auth/spreadsheets"])).open_by_key(
        env("MYNTRA_OTP_SHEET_ID") or SHEET_ID).sheet1
    new = sheet_rows(rows, now, ws.get_all_values())
    if new:
        ws.append_rows(new, value_input_option="RAW")       # RAW: an OTP keeps its leading zeros
    return len(new)


def run(args) -> int:
    rows, couriers, notes = read_codes()
    now = datetime.now()
    if rows and not args.no_sheet:
        try:
            log.info("OTP sheet: %d rows added", write_sheet(rows, now))
        except Exception as exc:  # noqa: BLE001 - the Telegram message still goes out
            log.warning("OTP sheet not updated: %s", exc)
            notes.append(f"OTP sheet not updated: {type(exc).__name__} {str(exc)[:80]}")
    text = message(rows, couriers, notes, now)
    print(text) if args.no_telegram else telegram.send(text)
    return 0 if rows else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py myntra-otp", description="Myntra courier pickup / return OTPs")
    p.add_argument("--no-telegram", action="store_true", help="print the message instead of sending it")
    p.add_argument("--no-sheet", action="store_true", help="leave the Daily OTP sheet alone")
    return run_bot("Myntra courier OTP", p, run, argv)
