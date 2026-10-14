"""Which returns need an SPF ticket today.

Rules (from the user, 2026-10-12):
  * AJIO marked the return delivered but it never reached us (no OMS Guru
    receipt, no ERP GRN, not in the manual register): raise DELIVERY DISPUTE
    RETURNS once 5 days have passed since the Return Delivered Date. This holds
    even when AJIO already paid the invoice (G4), because a paid return is
    usually reversed later.
  * Return still in transit: raise NOT DELIVERED RETURNS as soon as 60 days
    have passed since the return was created (AJIO's window closes at 90).
  * AJIO's credit note (IC) does not count as a late reversal: AJIO books it
    as soon as the return is created, before the parcel moves. A paid and
    reversed return is therefore handled by the two rules above like any other.
A return that already has a ticket, or has been received, is never raised,
and neither is one the courier never picked up from the customer (COURIER
PICKUP, written by pickup.py): there is nothing for AJIO to reimburse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from openpyxl import load_workbook

DISPUTE = "DELIVERY DISPUTE RETURNS"
NOT_DELIVERED = "NOT DELIVERED RETURNS"
DELIVERED_STATUSES = {"RETURN_DELIVERED", "RETURN_PROCESSED", "CREDIT_NOTE_CREATED"}


@dataclass
class Rules:
    dispute_after: int = 5          # days after Return Delivered Date
    dispute_max: int = 365          # no practical upper limit (user, 2026-10-12)
    transit_after: int = 60         # days after Return Created Date
    transit_max: int = 90


@dataclass
class Candidate:
    return_order: str
    pob_id: str
    sub_issue: str
    reason: str
    age: int
    rows: list = field(default_factory=list)

    @property
    def first(self) -> dict:
        return self.rows[0]


def read_sheet(path) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=False)
    ws = wb["Sheet1"]
    it = ws.iter_rows(values_only=True)
    headers = [str(h).strip() if h is not None else "" for h in next(it)]
    rows = [dict(zip(headers, values)) for values in it if any(v not in (None, "") for v in values)]
    wb.close()
    return rows


def as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(value.strip()[:10], fmt).date()
            except ValueError:
                pass
    return None


def amount(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def txt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def is_received(row: dict) -> bool:
    return ("RECEIVED" in txt(row.get("OMSGURU STATUS")).upper()
            or txt(row.get("MANUAL RETURN STATUS")).upper() == "RETURN RECEIVED")


def has_ticket(row: dict) -> bool:
    return txt(row.get("TICKET ID")) not in ("", "#N/A", "NA", "-")


def delivered_on(row: dict) -> date | None:
    when = as_date(row.get("Return Delivered Date"))
    if when:
        return when
    delivered = (txt(row.get("3PL Delivery Status")).upper() == "RETURN DELIVERED"
                 or txt(row.get("Return Status")).upper() in DELIVERED_STATUSES)
    return as_date(row.get("Delivery Challan Date")) if delivered else None


NO_PICKUP = {"NOT PICKED UP", "PICKUP CANCELLED", "PICKUP FAILED"}


def not_picked(row: dict) -> bool:
    return txt(row.get("COURIER PICKUP")) in NO_PICKUP


def is_cancelled(row: dict) -> bool:
    return "CANCEL" in txt(row.get("3PL Delivery Status")).upper()


def decide(row: dict, today: date, rules: Rules) -> tuple[str, str, int] | None:
    """(sub issue type, reason, age in days) when this row needs a ticket now."""
    if is_received(row) or has_ticket(row) or not_picked(row) or not txt(row.get("RETURN ORDER NUMBER")):
        return None
    delivered = delivered_on(row)
    if delivered:
        age = (today - delivered).days
        if rules.dispute_after <= age <= rules.dispute_max:
            paid = " (G4 paid)" if amount(row.get("G4 PAYMENT")) else ""
            return DISPUTE, f"delivered {age} days ago, not received{paid}", age
        return None
    if is_cancelled(row):
        return None
    created = as_date(row.get("Return Created Date"))
    if not created:
        return None
    age = (today - created).days
    if rules.transit_after <= age <= rules.transit_max:
        return NOT_DELIVERED, f"in transit for {age} days", age
    return None


def candidates(rows: list[dict], today: date, rules: Rules) -> list[Candidate]:
    """One candidate per return order (a ticket covers every item of the order)."""
    found: dict[str, Candidate] = {}
    for row in rows:
        verdict = decide(row, today, rules)
        if not verdict:
            continue
        rt = txt(row.get("RETURN ORDER NUMBER"))
        sub, reason, age = verdict
        cand = found.get(rt)
        if cand is None:
            found[rt] = Candidate(rt, txt(row.get("POB ID")), sub, reason, age, [row])
        else:
            cand.rows.append(row)
    return sorted(found.values(), key=lambda c: (-c.age, c.return_order))


def upcoming(rows: list[dict], today: date, rules: Rules) -> dict[str, int]:
    """How many returns cross a threshold in the next few days (for the summary)."""
    soon = {"dispute": 0, "transit": 0, "missed_dispute": 0}
    for row in rows:
        if is_received(row) or has_ticket(row) or not_picked(row):
            continue
        delivered = delivered_on(row)
        if delivered:
            age = (today - delivered).days
            if rules.dispute_after - 2 <= age < rules.dispute_after:
                soon["dispute"] += 1
            elif age > rules.dispute_max:
                soon["missed_dispute"] += 1
            continue
        created = as_date(row.get("Return Created Date"))
        if created and not is_cancelled(row) and rules.transit_after - 7 <= (today - created).days < rules.transit_after:
            soon["transit"] += 1
    return soon


def row_facts(row: dict) -> dict:
    """The facts about one return that a ticket description or reply may quote."""
    def d(v):
        x = as_date(v)
        return f"{x:%d-%m-%Y}" if x else ""
    return {
        "return_order": txt(row.get("RETURN ORDER NUMBER")),
        "return_awb": txt(row.get("Return AWB No")),
        "return_carrier": txt(row.get("Return Carrier Name")),
        "return_created": d(row.get("Return Created Date")),
        "return_status": txt(row.get("Return Status")),
        "ajio_3pl_status": txt(row.get("3PL Delivery Status")),
        "marked_delivered_on": d(row.get("Return Delivered Date")) or d(row.get("Delivery Challan Date")),
        "delivery_challan": txt(row.get("Delivery Challan No")),
        "invoice": txt(row.get("FWD B2B INVOICE No")),
        "invoice_amount": amount(row.get("FWD B2B Invoice Amt")),
        "sku": txt(row.get("SELLER SKU")),
        "forward_awb": txt(row.get("FWD AWB")),
        "dispatched_on": d(row.get("DISPATCH DATE")),
        "g4_paid": amount(row.get("G4 PAYMENT")),
        "ic_reversed": amount(row.get("IC REVERSE")),
        "received_in_warehouse": is_received(row),
        "oms_status": txt(row.get("OMSGURU STATUS")),
        "courier_pickup": txt(row.get("COURIER PICKUP")),
        "courier_pickup_date": d(row.get("PICKUP DATE")),
        "courier_status": txt(row.get("COURIER STATUS")),
    }
