"""ERP vs VMS: every Myntra parcel packed in the ERP since the 1st of the month, and whether
VMS has its packing video.

  AWBs     ERP packing report (Shipping Label Print), portal MYNTRA: its "Order ID" is the
           Myntra tracking number. One parcel can have several SKU lines; it counts once, on
           the day it was first packed.
  Channel  OMSGuru's Myntra orders (channel id -> TLPL / MMRJ / BOAT). Orders shipped by
           Shadowfax / Delhivery show the courier's AWB in OMSGuru, so their Myntra AWB comes
           from the label cache in the workbook, or from the shipping label. A parcel OMSGuru
           does not know counts as OTHER.
  VMS      the same packing scans the VMS report uses.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from ..core.config import env, num
from ..core.erp import Erp
from ..core.omsguru import OmsGuruClient
from .myntra import MYNTRA_AWB, list_rows, myntra_awbs
from .vms import VmsClient

log = logging.getLogger(__name__)
OTHER = "OTHER"


def erp_parcels(erp: Erp, since: date) -> dict[str, dict]:
    """Myntra AWB -> {date, packed_by, voucher, skus} for parcels packed from `since`."""
    out: dict[str, dict] = {}
    for line in erp.packed("MYNTRA", since):
        awb = str(line.get("Order ID") or "").strip().upper()
        when = line.get("Voucher Date")
        if not awb or not isinstance(when, datetime):
            continue
        p = out.get(awb)
        if p is None or when.date() < p["date"]:
            out[awb] = p = {"date": when.date(), "packed_by": line.get("Created By") or "",
                            "voucher": line.get("Voucher No.") or "", "skus": p["skus"] if p else []}
        sku = str(line.get("SKU Code") or "")
        if sku and sku not in p["skus"]:
            p["skus"].append(sku)
    log.info("ERP: %d Myntra parcels packed since %s", len(out), since)
    return out


def channel_map(client: OmsGuruClient, start: date, end: date, chans: dict[str, str],
                awb_cache: dict[str, str]) -> tuple[dict[str, str], list, list[str]]:
    """(Myntra AWB -> code, new order -> Myntra AWB pairs for the cache, label warnings)."""
    rows = list_rows(client, datetime.combine(start, datetime.min.time()),
                     datetime.combine(end + timedelta(days=1), datetime.min.time()), list(chans))
    rows = rows[rows["Channel Id"].isin(list(chans)) & (rows["AWB"] != "")]
    code_of_order = dict(zip(rows["Order Id"], rows["Channel Id"].map(chans)))
    out = {str(a).upper(): code_of_order[o] for o, a in zip(rows["Order Id"], rows["AWB"])}
    other = rows[~rows["AWB"].str.match(MYNTRA_AWB.pattern)]
    todo = [o for o in other["Order Id"] if o not in awb_cache]
    found, errors = myntra_awbs(client, todo) if todo else ({}, [])
    for oid, awb in {**awb_cache, **found}.items():
        if oid in code_of_order:
            out[str(awb).upper()] = code_of_order[oid]
    awb_of = dict(zip(rows["Order Id"], rows["AWB"]))
    new_pairs = [(oid, awb_of.get(oid, ""), awb) for oid, awb in found.items()]
    return out, new_pairs, errors


def compare(parcels: dict[str, dict], codes_of: dict[str, str], scanned: set[str], codes: list[str]):
    """(per day {date: {code: [erp, vms]}}, parcels without VMS as rows)."""
    per_day: dict[date, dict[str, list[int]]] = {}
    missing = []
    for awb, p in sorted(parcels.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        code = codes_of.get(awb, OTHER)
        day = per_day.setdefault(p["date"], {c: [0, 0] for c in codes + [OTHER]})
        day[code][0] += 1
        if awb in scanned:
            day[code][1] += 1
        else:
            missing.append({"date": p["date"], "code": code, "awb": awb, "packed_by": p["packed_by"],
                            "voucher": p["voucher"], "skus": ", ".join(p["skus"])})
    return per_day, missing


def run(book, today: date, codes: list[str], chans: dict[str, str]) -> dict:
    """Month-to-date ERP vs VMS into the workbook; returns what the report and dashboard show."""
    first = today.replace(day=1)
    erp = Erp.from_env()
    erp.login()
    parcels = erp_parcels(erp, first)
    if not parcels:
        raise RuntimeError(f"ERP has no Myntra parcels packed since {first}")

    client = OmsGuruClient.from_env()
    client.login()
    lookback = num("SLA_LOOKBACK_DAYS", 10)
    codes_of, new_pairs, errors = channel_map(client, first - timedelta(days=lookback), today, chans, book.awb_cache())
    book.save_awb_cache(new_pairs)

    vms = VmsClient(env("VMS_USERNAME"), env("VMS_PASSWORD"))
    vms.login()
    scanned = {a.upper() for a in vms.scanned_awbs(first - timedelta(days=3), today)}

    per_day, missing = compare(parcels, codes_of, scanned, codes)
    unknown = sum(d[OTHER][0] for d in per_day.values())
    shown = codes + ([OTHER] if unknown else [])          # OTHER only when OMSGuru missed a parcel
    book.write_erp_month(today.year, today.month, per_day, shown, upto=today)
    book.write_erp_no_vms(first, today, missing)
    log.info("ERP vs VMS %s..%s: %d parcels, %d without VMS, %d channel unknown",
             first, today, len(parcels), len(missing), unknown)
    total = {c: [sum(d[c][0] for d in per_day.values()), sum(d[c][1] for d in per_day.values())]
             for c in codes + [OTHER]}
    return {"from": first, "to": today, "codes": codes + [OTHER], "total": total,
            "days": [{"date": d, "codes": v} for d, v in sorted(per_day.items())],
            "missing": missing, "unknown_channel": unknown, "label_errors": len(errors)}
