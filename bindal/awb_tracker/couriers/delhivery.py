"""Delhivery -- public unified-tracking JSON API, no auth required."""
from __future__ import annotations

import re

from ..base import BaseCourier
from ..models import (NOT_FOUND, RETURN, TrackEvent, TrackResult,
                      classify, to_iso)

API = "https://dlv-api.delhivery.com/v3/unified-tracking-new"
SITE = "https://www.delhivery.com"

_RETURN_HINT = re.compile(r"reverse|return|\brvp\b|\brto\b", re.I)


class Delhivery(BaseCourier):
    name = "DELHIVERY"
    aliases = ("DELIVERY", "DELHIVARY", "DLV")
    min_interval = 0.2

    def _fetch(self, awb: str) -> TrackResult:
        res = self.new_result(awb)
        resp = self.client.get(
            API,
            params={"wbn": awb},
            headers=self.base_headers(referer=f"{SITE}/track-v2/package/{awb}", origin=SITE),
        )
        if resp.status_code != 200:
            res.error = f"HTTP {resp.status_code}"
            return res

        payload = resp.json()
        res.raw = payload
        rows = payload.get("data") or []
        if not rows:
            res.status = NOT_FOUND
            res.error = payload.get("message") or "no data returned"
            return res

        item = rows[0]
        status = item.get("status") or {}
        res.ok = True
        res.status_raw = status.get("status") or item.get("hqStatus") or ""
        res.status_detail = status.get("instructions") or item.get("deliveryPillLabel") or ""
        res.last_update = to_iso(status.get("statusDateTime"))
        res.expected_delivery = to_iso(item.get("expectedDate") or item.get("promiseDate") or "")
        res.reference_no = item.get("referenceNo") or ""
        res.origin = item.get("origin") or ""
        res.destination = item.get("destination") or ""

        # Delhivery marks reverse legs with an "RT" reference prefix and an
        # "RT"/"PP" status type; anything else is a forward shipment.
        flow_hint = f"{item.get('orderType', '')} {item.get('productType', '')}"
        if (res.reference_no.upper().startswith("RT")
                or str(status.get("statusType", "")).upper() in {"RT", "PP", "DR"}
                or _RETURN_HINT.search(f"{flow_hint} {res.status_detail}")):
            res.flow = RETURN
        if item.get("deliveryDate"):
            res.delivered_on = to_iso(item["deliveryDate"])

        # trackingStates group scans into milestones; flatten them.
        for state in item.get("trackingStates") or []:
            for scan in state.get("scans") or []:
                res.events.append(TrackEvent(
                    timestamp=to_iso(scan.get("scanDateTime") or scan.get("scanDate")),
                    status=scan.get("scan") or state.get("label") or "",
                    location=scan.get("scannedLocation") or scan.get("cityLocation") or "",
                    remark=scan.get("scanNslRemark") or "",
                ))

        # The public API often leaves every scanDateTime blank; only the current status
        # carries a time. Keep it as an event so the pickup moment is not lost
        # (status "PICKUP" + statusDateTime = when the parcel was picked up).
        if res.last_update and not any(e.timestamp for e in res.events):
            code = str(status.get("status") or "").upper()
            res.events.append(TrackEvent(
                timestamp=res.last_update,
                status="Shipment picked up" if code == "PICKUP" else (res.status_detail or res.status_raw),
                location="",
                remark=f"current status {code}".strip(),
            ))

        res.status = classify(res.status_raw, res.status_detail, item.get("currentFlow"))
        return res
