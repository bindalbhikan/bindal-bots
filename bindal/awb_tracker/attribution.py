"""Work out *who* caused a negative outcome, and quote the courier's own words.

Every partner phrases this differently -- Blue Dart says "Pickup Cancelled By
Etailer", Delhivery says "Order cancelled as per the seller's instructions",
Shadowfax says "Cancelled by Ajio", XpressBees just says "RPCancel" and leaves
the reason scattered through earlier scans. This module reads all of that and
reduces it to one party plus the sentence that proves it.

Two tiers of evidence are used:

* **explicit** -- the text names the party ("cancelled by customer"). Trusted
  wherever it appears in the history.
* **inferred** -- the text names a cause that implies a party (a pincode the
  courier could not service). Only used when nothing explicit was found.

Texts are read most-recent-first, so a late explicit statement outranks an
earlier one.
"""
from __future__ import annotations

import re
from typing import Iterable

from .models import (CANCELLED, PICKUP_FAILED, RTO, UNDELIVERED, TrackResult)

# Party values written to the report.
SELLER = "SELLER"
CUSTOMER = "CUSTOMER"
COURIER = "COURIER"
QC_FAIL = "QC_FAIL"
UNKNOWN_PARTY = "UNKNOWN"
NOT_APPLICABLE = ""

#: Outcomes worth attributing. Everything else gets a blank party.
ATTRIBUTABLE = {CANCELLED, PICKUP_FAILED, RTO, UNDELIVERED}

# Marketplaces / brands that appear as "Cancelled by <brand>" -- the brand is
# the seller, not the courier. Extend freely; the generic rule below catches
# names that are not listed.
SELLER_BRANDS = (
    "ajio|myntra|flipkart|amazon|meesho|nykaa|snapdeal|tatacliq|tata cliq|"
    "shopsy|jiomart|firstcry|lenskart|bigbasket|blinkit|zepto|purplle|limeroad"
)

# --- explicit rules: the text names the responsible party --------------------
_EXPLICIT: list[tuple[re.Pattern[str], str]] = [
    # Quality-check rejection at the door. Kept first: it is the most specific
    # outcome and is reported separately rather than folded into a party.
    (re.compile(r"qc\s*fail|qcfail|quality\s*check|failed the qua"), QC_FAIL),

    (re.compile(r"\bby (the )?(customer|consignee|buyer|receiver|recipient)\b|"
                r"customer (cancell?ed|refused|rejected|denied|declined|not interested)|"
                r"consignee (refused|rejected|cancell?ed)|"
                r"cancell?ed (at )?customer('?s)? (request|end)|"
                r"(refused|rejected) by (customer|consignee)"), CUSTOMER),

    (re.compile(r"\bby (the )?(etailer|e-tailer|seller|client|merchant|vendor|"
                r"shipper|consignor|brand)\b|"
                r"(seller|client|merchant|shipper|consignor)'?s? (instruction|request)|"
                r"as per (the )?(seller|client|merchant|shipper)|"
                r"cancell?ed by (" + SELLER_BRANDS + r")\b"), SELLER),

    (re.compile(r"\bby (the )?(courier|carrier|rider|agent|field executive|fe)\b|"
                r"cancell?ed by (shadowfax|delhivery|xpressbees|bluedart|blue dart|ekart|valmo)"),
     COURIER),
]

# --- inferred rules: the text names a cause that implies a party -------------
_INFERRED: list[tuple[re.Pattern[str], str]] = [
    # Serviceability and operational failures sit with the courier.
    (re.compile(r"pincode|pin code|out of (delivery |service )?area|"
                r"non.?serviceable|not serviceable|no service|out of station|"
                r"capacity|misroute|mis-route|"
                r"address (issue|incorrect|incomplete|mismatch|not found|wrong)|"
                r"wrong address|incomplete address|"
                r"pick ?up not attempted|not attempted|no attempt|"
                r"agent (not|un)available|rider (not|un)available"), COURIER),

    # A customer who could not be reached or would not hand the parcel over.
    (re.compile(r"customer (not available|unavailable|not reachable|not responding)|"
                r"(phone|number) (not reachable|switched off|busy)|"
                r"premises closed|door lock|self ?collect"), CUSTOMER),

    # Order-level closure with no named party is a merchant-side decision.
    (re.compile(r"as per (client|seller)|closed as per|order cancell?ed|"
                r"cancell?ed by e?tailer"), SELLER),
]

# Generic last resort: "Cancelled by <Something>" where Something is not a
# customer or courier keyword is treated as the seller/brand.
_BY_NAME = re.compile(
    r"cancell?ed by ([a-z][a-z0-9 &.\-]{2,30})", re.I)
_NON_SELLER_WORDS = re.compile(
    r"customer|consignee|buyer|receiver|call|phone|courier|rider|agent|"
    r"system|auto|ops|qc|quality", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _candidate_texts(result: TrackResult) -> list[str]:
    """Evidence strings, most recent / most authoritative first."""
    texts = [result.status_detail, result.status_raw]
    for event in reversed(result.events):        # events are chronological
        texts.append(event.remark)
        texts.append(event.status)
    return [_clean(t) for t in texts if _clean(t)]


def _match(texts: Iterable[str],
           rules: list[tuple[re.Pattern[str], str]]) -> tuple[str, str] | None:
    for text in texts:
        lowered = text.lower()
        for pattern, party in rules:
            if pattern.search(lowered):
                return party, text
    return None


def attribute(result: TrackResult) -> tuple[str, str]:
    """Return ``(party, evidence)`` for a shipment.

    ``party`` is one of SELLER / CUSTOMER / COURIER / QC_FAIL / UNKNOWN, or an
    empty string when the outcome is not one worth attributing. ``evidence`` is
    the courier's own wording that led to the verdict, so every call can be
    audited against the source.
    """
    if result.status not in ATTRIBUTABLE:
        return NOT_APPLICABLE, ""

    texts = _candidate_texts(result)
    if not texts:
        return UNKNOWN_PARTY, ""

    hit = _match(texts, _EXPLICIT)
    if hit:
        return hit

    # "Cancelled by <brand>" that no explicit rule listed.
    for text in texts:
        found = _BY_NAME.search(text)
        if found and not _NON_SELLER_WORDS.search(found.group(1)):
            return SELLER, text

    hit = _match(texts, _INFERRED)
    if hit:
        return hit

    return UNKNOWN_PARTY, texts[0]
