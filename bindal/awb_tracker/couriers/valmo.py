"""Valmo (Meesho logistics) -- public JSON API, no auth required."""
from __future__ import annotations

import time

from ..base import BaseCourier
from ..models import (FORWARD, NOT_FOUND, RETURN, TrackEvent, TrackResult,
                      classify, to_iso)

API = "https://www.valmo.in/api/valmo-web/track-order"
SITE = "https://www.valmo.in"


class Valmo(BaseCourier):
    name = "VALMO"
    aliases = ("VOLMO", "VALMO LOGISTICS", "MEESHO")
    min_interval = 0.2

    def _fetch(self, awb: str) -> TrackResult:
        res = self.new_result(awb)
        resp = self.client.get(
            API,
            params={"request_id": str(int(time.time() * 1000)), "tracking_id": awb},
            headers=self.base_headers(referer=f"{SITE}/track/{awb}", origin=SITE),
        )
        if resp.status_code != 200:
            res.error = f"HTTP {resp.status_code}"
            return res

        payload = resp.json()
        res.raw = payload
        stages = payload.get("tracking") or []
        if not payload.get("success") or not stages:
            res.status = NOT_FOUND
            res.error = payload.get("message") or "no tracking data"
            return res

        res.ok = True
        res.expected_delivery = to_iso(payload.get("expected_delivery_date") or "")

        # Valmo states the direction outright on each stage.
        movements = {str(s.get("movement_type", "")).lower() for s in stages}
        if movements & {"reverse", "return", "backward"}:
            res.flow = RETURN
        elif "forward" in movements:
            res.flow = FORWARD

        # Each stage carries an optional sub_status_list of finer scans.
        for stage in stages:
            display = stage.get("display_name") or stage.get("status") or ""
            if stage.get("date"):
                res.events.append(TrackEvent(
                    timestamp=to_iso(stage["date"]),
                    status=display,
                    remark=stage.get("status") or "",
                ))
            for sub in stage.get("sub_status_list") or []:
                res.events.append(TrackEvent(
                    timestamp=to_iso(sub.get("date")),
                    status=sub.get("display_name") or sub.get("status") or "",
                    remark=sub.get("status") or "",
                ))
            if stage.get("is_current"):
                res.status_raw = display
                res.last_update = to_iso(stage.get("date"))

        if not res.status_raw:
            completed = [s for s in stages if s.get("is_complete")]
            if completed:
                res.status_raw = completed[-1].get("display_name") or ""

        res.status = classify(res.status_raw, *(e.status for e in res.events[-3:]))
        return res
