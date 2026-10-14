"""Normalized data model shared by every courier adapter."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

# Canonical status buckets. Every courier's raw status is mapped onto one of
# these so downstream reports can group across partners.
PENDING = "PENDING"
PICKED_UP = "PICKED_UP"
IN_TRANSIT = "IN_TRANSIT"
OUT_FOR_PICKUP = "OUT_FOR_PICKUP"
OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
DELIVERED = "DELIVERED"
UNDELIVERED = "UNDELIVERED"
PICKUP_FAILED = "PICKUP_FAILED"
RTO = "RTO"
CANCELLED = "CANCELLED"
LOST = "LOST"
NOT_FOUND = "NOT_FOUND"
UNKNOWN = "UNKNOWN"

# Direction of travel. A return (reverse pickup / RTO) moves customer -> seller.
FORWARD = "FORWARD"
RETURN = "RETURN"

#: Progress states that read differently on a return leg, so they get a
#: RETURN_ prefix ("RETURN_IN_TRANSIT"). Terminal outcomes such as CANCELLED
#: or PICKUP_FAILED are already unambiguous and stay as they are.
#: OUT_FOR_PICKUP is left out on purpose -- collecting from the customer is
#: already the return leg's own step, so "RETURN_OUT_FOR_PICKUP" would only
#: repeat itself.
_RETURN_PREFIXED = {PENDING, PICKED_UP, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED}

RETURN_PENDING = "RETURN_PENDING"
RETURN_PICKED_UP = "RETURN_PICKED_UP"
RETURN_IN_TRANSIT = "RETURN_IN_TRANSIT"
RETURN_OUT_FOR_DELIVERY = "RETURN_OUT_FOR_DELIVERY"
RETURN_DELIVERED = "RETURN_DELIVERED"

# Wording that marks a shipment as a return leg. "RVP" = reverse pickup,
# "RTO" = return to origin, "RP" = return pickup. Couriers glue these onto the
# next word ("RVPCancelledQCFail", "RPCancel"), so the codes are matched on a
# leading boundary only, with the ambiguous "RP" limited to its known forms.
_RETURN_TEXT = re.compile(
    r"\brvp|\brto\b|rto(?=cancel|deliver|initiat|confirm|marked|scan)|"
    r"shipmentrto|marked rto|\brp\b|\brpcancel|"
    r"reverse|return|"
    r"pick ?up (from|at) customer|customer pick ?up|"
    r"back to (origin|seller|shipper)",
    re.I,
)
# AWB shapes that only ever appear on return legs.
_RETURN_AWB = re.compile(r"^R\d{6,}", re.I)


def detect_flow(awb: str = "", *texts: str | None) -> str:
    """FORWARD or RETURN, inferred from the AWB shape and scan wording."""
    if awb and _RETURN_AWB.match(str(awb).strip()):
        return RETURN
    for text in texts:
        if text and _RETURN_TEXT.search(str(text)):
            return RETURN
    return FORWARD


def apply_flow(status: str, flow: str) -> str:
    """Rename a progress state onto its return-leg equivalent."""
    if flow == RETURN and status in _RETURN_PREFIXED:
        return f"RETURN_{status}"
    return status

# Future-tense promises ("will be delivered by 29 August") describe an ETA, not
# an outcome, so they are stripped before the rules run.
_ETA_NOISE = re.compile(
    r"(will be|to be|expected|estimated|scheduled|promised|due|by)\s+"
    r"(to\s+)?deliver(ed|y)?\b|deliver(y|ed)\s+(by|on|expected|scheduled)\b",
)

# Ordered most-specific first: the first pattern that matches wins.
_STATUS_RULES: list[tuple[str, str]] = [
    # Couriers glue "RTO" into CamelCase event names ("ShipmentRtoConfirmed"),
    # so the bare code is matched both standalone and as a known compound.
    (r"\brto\b|rto(?=confirm|initiat|deliver|marked|scan|receiv)|shipmentrto|"
     r"marked rto|return to origin|returned to (origin|seller|shipper)|"
     r"rvp\s*delivered", RTO),
    (r"cancel", CANCELLED),
    (r"\blost\b|damage[d]?\b|destroy", LOST),
    # Reverse pickups: attempted but not collected. Kept below the "cancel"
    # rule so "Pickup Cancelled" stays CANCELLED.
    (r"pick ?up (unsuccessful|failed|not done|not attempted|rejected)|"
     r"unsuccessful pick ?up|notpicked|not picked|qc ?fail|failed the qua",
     PICKUP_FAILED),
    (r"undelivered|not delivered|delivery (failed|attempt)|failed attempt|"
     r"unsuccessful delivery|customer (not available|unavailable|rejected)|"
     r"refused|rejected|\bnsl\b", UNDELIVERED),
    # An agent on the way to collect -- the parcel is NOT yet picked up, so
    # this must outrank the PICKED_UP rule below.
    (r"out ?for ?pick ?up|outforpickup|pickup scheduled|agent assigned", OUT_FOR_PICKUP),
    (r"out for delivery|ofd\b|outfordelivery|reaching you|near you|"
     r"arriving today|with delivery agent", OUT_FOR_DELIVERY),
    # (?<!un) so "undelivered" can never fall through to this rule.
    (r"(?<!un)deliver(ed|y done)|shipment delivered|\bdlvd\b", DELIVERED),
    # "returnReceived" is glued; bare "received" is deliberately NOT matched
    # here so "Data Received" still falls through to PENDING.
    (r"in ?transit|dispatched|received at|returnreceived|return received|"
     r"reached|left |bagged|connected|shipped|forwarded|arrived|departed|"
     r"inscan|in scan|misroute|detained|held (at|by)", IN_TRANSIT),
    (r"picked ?up|pick ?up done|pick ?done|pickup from|collected", PICKED_UP),
    (r"pending|manifest|data received|shipment created|order (confirmed|placed|received)|"
     r"ready for pickup|pick ?up requested|awaiting|reschedul|reattempt|"
     r"soft ?data", PENDING),
]


def _spaced(text: str) -> str:
    """API codes as words: "PickDone" -> "Pick Done", "IN_TRANSIT" -> "IN TRANSIT"."""
    return re.sub(r"[_\-]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text))


def classify(*texts: str | None) -> str:
    """Map free-text courier status wording onto a canonical bucket.

    Arguments are tried in priority order -- the courier's own status field
    first, supporting text after -- so a remark such as "will be delivered by
    Saturday" can never outrank a primary status of "Near You". Each text is
    tried as written, then with API codes split into words.
    """
    for text in texts:
        if not text:
            continue
        for variant in dict.fromkeys((str(text), _spaced(str(text)))):
            blob = _ETA_NOISE.sub(" ", variant.lower())
            if not blob.strip():
                continue
            for pattern, bucket in _STATUS_RULES:
                if re.search(pattern, blob):
                    return bucket
    return UNKNOWN


def to_iso(value: Any) -> str:
    """Best-effort conversion of the many timestamp shapes couriers emit.

    Handles epoch seconds/millis, ISO-8601, and the human formats used by
    XpressBees ("Jun 18, 2026 16:12") and BlueDart ("15 Jul 2026" + "22:41").
    Returns "" when nothing parses, never raises.
    """
    if value in (None, "", 0):
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e11:          # milliseconds
            ts /= 1000.0
        if ts <= 0:
            return ""
        return datetime.fromtimestamp(ts, IST).replace(tzinfo=None).isoformat(timespec="seconds")

    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit():
        return to_iso(int(text))
    # XpressBees occasionally emits two dates in one scan field
    # ("18-04-2026, 22-04-2026"); keep the first that parses.
    if "," in text and re.search(r"\d.*,.*\d", text):
        for part in (p.strip() for p in text.split(",")):
            parsed = to_iso(part) if part else ""
            if parsed and parsed != part:
                return parsed

    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%b %d, %Y %H:%M", "%b %d, %Y",
        "%d %b %Y %H:%M", "%d %b %Y",
        "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%d %B %Y", "%d %B %Y, %A",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue

    # ISO with a trailing offset / Z
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(
            tzinfo=None).isoformat(timespec="seconds")
    except ValueError:
        return text  # keep the original so information is never silently lost


@dataclass
class TrackEvent:
    """One scan in a shipment's history."""
    timestamp: str = ""
    status: str = ""
    location: str = ""
    remark: str = ""

    def sort_key(self) -> str:
        return self.timestamp or ""


@dataclass
class TrackResult:
    """Normalized tracking outcome for a single AWB."""
    awb: str
    partner: str
    ok: bool = False
    status: str = UNKNOWN            # canonical bucket
    status_raw: str = ""             # courier's own wording
    status_detail: str = ""
    current_location: str = ""
    last_update: str = ""
    expected_delivery: str = ""
    delivered_on: str = ""
    origin: str = ""
    destination: str = ""
    reference_no: str = ""
    flow: str = ""                   # FORWARD or RETURN; blank until finalize()
    cancelled_by: str = ""           # SELLER / CUSTOMER / COURIER / QC_FAIL / UNKNOWN
    cancel_reason: str = ""          # the courier's own wording behind that call
    error: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now(IST)
                            .replace(tzinfo=None).isoformat(timespec="seconds"))
    events: list[TrackEvent] = field(default_factory=list)
    raw: Any = None

    def finalize(self) -> "TrackResult":
        """Derive summary fields from the event list once an adapter is done."""
        self.events.sort(key=TrackEvent.sort_key)
        if self.events:
            latest = self.events[-1]
            self.last_update = self.last_update or latest.timestamp
            self.current_location = self.current_location or latest.location
            self.status_raw = self.status_raw or latest.status
            self.status_detail = self.status_detail or latest.remark or latest.status
        if self.status == UNKNOWN:
            self.status = classify(self.status_raw, self.status_detail)
        if self.status == UNKNOWN and self.ok:
            # latest wording means nothing ("Contact Customer Service"): the newest scan that does
            for ev in reversed(self.events):
                self.status = classify(ev.status, ev.remark)
                if self.status != UNKNOWN:
                    break

        # Direction: an adapter may already know it from an explicit API field
        # (Valmo's movement_type, Shadowfax's Fwd/Rev), otherwise infer it from
        # the AWB and every piece of scan wording we collected.
        if not self.flow:
            self.flow = detect_flow(
                self.awb, self.status_raw, self.status_detail, self.reference_no,
                *(e.status for e in self.events),
                *(e.remark for e in self.events),
                *(e.location for e in self.events),
            )
        self.status = apply_flow(self.status, self.flow)

        if self.status in (DELIVERED, RETURN_DELIVERED) and not self.delivered_on:
            for ev in reversed(self.events):
                if classify(ev.status, ev.remark) == DELIVERED:
                    self.delivered_on = ev.timestamp
                    break

        # Imported here rather than at module scope: attribution reads the
        # status buckets defined above, so the dependency runs one way.
        from .attribution import attribute
        if not self.cancelled_by:
            self.cancelled_by, evidence = attribute(self)
            self.cancel_reason = self.cancel_reason or evidence
        return self

    def summary_row(self) -> dict[str, Any]:
        return {
            "AWB": self.awb,
            "PARTNER": self.partner,
            "OK": "YES" if self.ok else "NO",
            "FLOW": self.flow,
            "STATUS": self.status,
            "STATUS_RAW": self.status_raw,
            "STATUS_DETAIL": self.status_detail,
            "CANCELLED_BY": self.cancelled_by,
            "CANCEL_REASON": self.cancel_reason,
            "CURRENT_LOCATION": self.current_location,
            "LAST_UPDATE": self.last_update,
            "EXPECTED_DELIVERY": self.expected_delivery,
            "DELIVERED_ON": self.delivered_on,
            "ORIGIN": self.origin,
            "DESTINATION": self.destination,
            "REFERENCE_NO": self.reference_no,
            "EVENTS": len(self.events),
            "ERROR": self.error,
            "FETCHED_AT": self.fetched_at,
        }

    def event_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "AWB": self.awb,
                "PARTNER": self.partner,
                "SEQ": i,
                "TIMESTAMP": ev.timestamp,
                "STATUS": ev.status,
                "LOCATION": ev.location,
                "REMARK": ev.remark,
            }
            for i, ev in enumerate(self.events, 1)
        ]

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_raw:
            data.pop("raw", None)
        return data
