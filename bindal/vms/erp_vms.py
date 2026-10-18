"""ERP vs VMS: every Myntra parcel packed in the ERP yesterday and today, and whether VMS has
its packing video. Only those two days are fetched on a run; earlier days stay in the sheets.

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
from ..portals.erp import Erp
from ..portals.omsguru import OmsGuruClient
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
    """ERP vs VMS for the parcels packed yesterday and today: only those two days are fetched;
    their rows in the month sheet and in ERP NO VMS are replaced, earlier days stay as they are."""
    yesterday = today - timedelta(days=1)
    days = [yesterday, today]
    erp = Erp.from_env()
    erp.login()
    parcels = erp_parcels(erp, yesterday)
    if not parcels:
        raise RuntimeError(f"ERP has no Myntra parcels packed since {yesterday}")

    client = OmsGuruClient.from_env()
    client.login()
    lookback = num("SLA_LOOKBACK_DAYS", 10)
    codes_of, new_pairs, errors = channel_map(client, yesterday - timedelta(days=lookback), today, chans, book.awb_cache())
    book.save_awb_cache(new_pairs)

    vms = VmsClient(env("VMS_USERNAME"), env("VMS_PASSWORD"))
    vms.login()
    scanned = {a.upper() for a in vms.scanned_awbs(yesterday - timedelta(days=3), today)}

    per_day, missing = compare(parcels, codes_of, scanned, codes)
    every = codes + [OTHER]
    per_day = {d: per_day.get(d, {c: [0, 0] for c in every}) for d in days}
    for year, month in sorted({(d.year, d.month) for d in days}):
        sheet = book.erp_month_days(year, month, every)
        sheet.update({d: v for d, v in per_day.items() if (d.year, d.month) == (year, month)})
        shown = codes + ([OTHER] if any(v[OTHER][0] for v in sheet.values()) else [])
        upto = today if (year, month) == (today.year, today.month) else yesterday   # yesterday: last month's last day
        book.write_erp_month(year, month, sheet, shown, upto=upto)
    book.write_erp_no_vms(yesterday, today, missing)
    unknown = sum(d[OTHER][0] for d in per_day.values())
    log.info("ERP vs VMS %s..%s: %d parcels, %d without VMS, %d channel unknown",
             yesterday, today, len(parcels), len(missing), unknown)
    total = {c: [sum(d[c][0] for d in per_day.values()), sum(d[c][1] for d in per_day.values())] for c in every}
    return {"from": yesterday, "to": today, "codes": every, "total": total,
            "days": [{"date": d, "codes": v} for d, v in per_day.items()],
            "missing": missing, "unknown_channel": unknown, "label_errors": len(errors)}
