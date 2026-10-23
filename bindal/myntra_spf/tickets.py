"""Myntra SPF claims raised by the bot, on the policy's timelines (SPF POLICY v5, Annexure 1).

Two kinds, both raised the way the portal's bulk upload does it (Support > Seller Protection
Fund > Create New Claim > "Here"): an xlsx of order line ids per issue category.

  Return Not Received  (RTS_ORDER_WAS_NEVER_DELIVERED)
      Myntra shows the return delivered to us, but it never reached the warehouse. Policy:
      within 7 days of the delivery date. Raised from day MYNTRA_SPF_RNR_AFTER_DAYS (4) after
      delivery, so the GRN / OMSGuru have had time to book a return that did arrive.
  Delayed Returns      (DELAYED_DELIVERED_RETURN)
      Still in transit, or no return tracking at all. Policy: after 60 and up to 120 days from
      the return / RTO creation date. Raised from day MYNTRA_SPF_DELAY_AFTER_DAYS (61).

A row is claimed only when nothing says it is settled: no ticket on it (master TICKET ID or
any ticket on the portal for that order line, any status - the policy allows one claim per
order and treats repeat tickets as fraud), FINAL RETURN STATUS blank (not received in the ERP /
OMSGuru / manual sheet, no SPF payout, no clean settlement), not an SJIT / FBM order, and no
RETURN POD from the courier (a signed return handover).

Every ticket on the portal is also written into the work folder as a ticket report, so the
master's TICKET ID / STATUS columns follow the portal. A claim just raised gets
TICKET ID "BOT JOB <job id>" until its real ticket turns up on the next run.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from ..core.config import num

log = logging.getLogger(__name__)

RNR = "RTS_ORDER_WAS_NEVER_DELIVERED"
DELAYED = "DELAYED_DELIVERED_RETURN"
CATEGORY_NAME = {RNR: "Return Not Received", DELAYED: "Delayed Returns"}
DESCRIPTION = {
    RNR: "RETURN DELIVERED BUT PHYSICAL RETURN PARCEL NOT RECEIVED YET",
    DELAYED: "RETURN SHOWING IN TRANSIT, KINDLY CHECK AND RETURN THE PARCEL ASAP",
}
# The ticket report the engine reads (kind TICKET: ticketId + orderLineId columns)
TICKET_FILE = "SPF Tickets (portal).xlsx"
BOT_FILE = "SPF Tickets (raised by bot).xlsx"
TICKET_HEADER = ["ticketId", "orderLineId", "returnId", "issueCategory", "status", "createdOn"]


def _day(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def claim_of(r: dict, today: date, ticketed: set[str]) -> str | None:
    """The category to claim this master row under today, or None."""
    from .pipeline import final_status, not_worked
    from .engine import norm, is_delivered_status
    oli = norm(r.get("order_line_id"))
    if not oli or oli in ticketed or not _blank(r.get("TICKET ID")):
        return None
    if not_worked(r) or final_status(r):
        return None
    if str(r.get("RETURN POD") or "").strip().upper() == "YES":
        return None
    delivered = _day(r.get("deliver_to_seller_date"))
    if delivered or is_delivered_status(r.get("status")):
        if not delivered:
            return None                          # delivered without a date: the TAT cannot be told
        age = (today - delivered).days
        lo, hi = num("MYNTRA_SPF_RNR_AFTER_DAYS", 4), num("MYNTRA_SPF_RNR_MAX_DAYS", 7)
        return RNR if lo <= age <= hi else None
    created = _day(r.get("return_created_date"))
    if not created:
        return None
    age = (today - created).days
    lo, hi = num("MYNTRA_SPF_DELAY_AFTER_DAYS", 61), num("MYNTRA_SPF_DELAY_MAX_DAYS", 120)
    return DELAYED if lo <= age <= hi else None


def candidates(path: Path, today: date, ticketed: dict[str, set[str]],
               skip: set[tuple] = frozenset()) -> dict[str, dict[str, list[str]]]:
    """account -> category -> order line ids to claim, from the master's account sheets.
    ticketed: account -> order lines with a portal ticket. skip: (order_line_id, return_id)
    keys known to be received (OMSGuru) since the master was written."""
    from openpyxl import load_workbook
    from .engine import FIRST_DATA_ROW, HEADER_ROW, SHEETS, norm
    wb = load_workbook(path, read_only=True)
    out: dict[str, dict[str, list[str]]] = {}
    for name in SHEETS:
        if name not in wb.sheetnames:
            continue
        rows = wb[name].iter_rows(min_row=HEADER_ROW, values_only=True)
        head = [str(h).strip() if h is not None else "" for h in next(rows)]
        for _ in range(FIRST_DATA_ROW - HEADER_ROW - 1):
            next(rows, None)
        seen: set[str] = set()
        for values in rows:
            r = dict(zip(head, values))
            if (norm(r.get("order_line_id")), norm(r.get("return_id"))) in skip:
                continue
            cat = claim_of(r, today, ticketed.get(name, set()))
            oli = norm(r.get("order_line_id"))
            if cat and oli not in seen:
                seen.add(oli)
                out.setdefault(name, {}).setdefault(cat, []).append(oli)
    wb.close()
    return out


def write_ticket_file(path: Path, rows: list[dict]) -> None:
    """xlsx, not CSV: ticket ids then stay numbers in the master, as from the hand export."""
    from openpyxl import Workbook
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("tickets")
    ws.append(TICKET_HEADER)
    for t in rows:
        ws.append([t.get(c) for c in TICKET_HEADER])
    wb.save(path)


def portal_tickets(portal, account: str, dest: Path) -> set[str]:
    """Every ticket of the account into dest/<account>/TICKET_FILE; the order lines they cover."""
    from .engine import norm
    rows = portal.spf_tickets()
    write_ticket_file(dest / account / TICKET_FILE, rows)
    log.info("%s: %d SPF tickets on the portal", account, len(rows))
    return {norm(t.get("orderLineId")) for t in rows if t.get("orderLineId")}


def raise_claims(portal, account: str, wanted: dict[str, list[str]], dest: Path, live: bool,
                 seller: int | None = None) -> list[str]:
    """Raise this account's claims (live) or only list them. Writes the bot's ticket file so the
    master shows them at once. One line per category for the report."""
    from ..portals.myntra import SPF_BULK_MAX
    cap = num("MYNTRA_SPF_TICKET_MAX", SPF_BULK_MAX)
    lines, raised = [], []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for cat in (RNR, DELAYED):
        ids = wanted.get(cat) or []
        if not ids:
            continue
        todo, rest = ids[:cap], len(ids) - min(len(ids), cap)
        more = f", {rest} left for the next run" if rest else ""
        if not live:
            lines.append(f"{account} {CATEGORY_NAME[cat]}: {len(todo)} due (not raised: tickets off){more}")
            continue
        jobs = []
        for i in range(0, len(todo), SPF_BULK_MAX):
            part = todo[i:i + SPF_BULK_MAX]
            try:
                job = portal.spf_bulk_claim(part, cat, DESCRIPTION[cat], seller=seller)
            except Exception as exc:  # noqa: BLE001 - the other category / account still go
                log.exception("%s %s claim failed", account, cat)
                lines.append(f"{account} {CATEGORY_NAME[cat]}: {len(part)} NOT raised: {str(exc)[:80]}")
                continue
            jobs.append(job)
            raised += [{"ticketId": f"BOT JOB {job}", "orderLineId": o, "issueCategory": cat,
                        "status": "RAISED BY BOT", "createdOn": now} for o in part]
        if jobs:
            n = sum(1 for t in raised if t["issueCategory"] == cat)
            lines.append(f"{account} {CATEGORY_NAME[cat]}: {n} raised (job {', '.join(map(str, jobs))}){more}")
    if raised:
        write_ticket_file(dest / account / BOT_FILE, raised)
    return lines
