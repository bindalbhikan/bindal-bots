"""XpressBees -- POST guarded by an ALTCHA proof-of-work, then a plain GET.

The site issues a SHA-256 ALTCHA challenge: find the integer ``n`` where
``sha256(salt + n)`` equals the published digest, then send the solution
back base64-encoded. It costs a few milliseconds in Python, so no browser
is needed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time

from ..base import BaseCourier
from ..models import NOT_FOUND, TrackEvent, TrackResult, classify, to_iso

CHALLENGE_URL = "https://altcha-api.xbees.in/v1/challenge"
POST_URL = "https://www.xpressbees.com/api/tracking"
GET_URL = "https://www.xpressbees.com/api/tracking/{awb}"
SITE = "https://www.xpressbees.com"


def solve_altcha(challenge: dict) -> str:
    """Brute-force the ALTCHA proof-of-work and return the base64 payload."""
    salt = challenge["salt"]
    target = challenge["challenge"]
    algorithm = challenge.get("algorithm", "SHA-256")
    hasher = {"SHA-256": hashlib.sha256,
              "SHA-384": hashlib.sha384,
              "SHA-512": hashlib.sha512}.get(algorithm, hashlib.sha256)
    max_number = int(challenge.get("maxnumber", 100_000))

    started = time.time()
    prefix = salt.encode()
    for number in range(max_number + 1):
        if hasher(prefix + str(number).encode()).hexdigest() == target:
            payload = {
                "algorithm": algorithm,
                "challenge": target,
                "number": number,
                "salt": salt,
                "signature": challenge["signature"],
                "took": int((time.time() - started) * 1000),
            }
            return base64.b64encode(
                json.dumps(payload, separators=(",", ":")).encode()).decode()
    raise RuntimeError("ALTCHA challenge unsolved within maxnumber")


class XpressBees(BaseCourier):
    name = "XPRESSBEES"
    aliases = ("XPRESS BEES", "XB", "XPRESSBEE")
    min_interval = 0.3

    def _fetch(self, awb: str) -> TrackResult:
        res = self.new_result(awb)
        referer = f"{SITE}/shipment/tracking?awbNo={awb}"
        headers = self.base_headers(referer=referer, origin=SITE)

        # 1. Solve the proof-of-work and post it to get the shipment summary.
        summary: dict = {}
        try:
            challenge = self.client.get(
                CHALLENGE_URL, headers=self.base_headers(referer=SITE + "/")).json()
            post = self.client.post(
                POST_URL,
                json={"awbNo": awb, "altchaPayload": solve_altcha(challenge)},
                headers={**headers, "Content-Type": "application/json"},
            )
            if post.status_code == 200:
                body = post.json()
                rows = body.get("domestic") or body.get("international") or []
                if rows:
                    summary = rows[0]
            elif post.status_code == 400:
                res.status = NOT_FOUND
                res.error = (post.json() or {}).get("message", "invalid AWB")
        except Exception as exc:
            res.error = f"summary step: {exc}"

        # 2. The scan history endpoint needs no challenge.
        resp = self.client.get(GET_URL.format(awb=awb), headers=headers)
        if resp.status_code != 200:
            res.error = res.error or f"HTTP {resp.status_code}"
            return res

        payload = resp.json()
        res.raw = {"summary": summary, "detail": payload}
        scans = payload.get("data") or []
        if not scans and not summary:
            res.status = NOT_FOUND
            res.error = res.error or "no tracking data"
            return res

        res.ok = True
        res.origin = summary.get("origin") or ""
        res.destination = summary.get("destination") or ""
        res.reference_no = payload.get("orderId") or summary.get("orderId") or ""
        res.expected_delivery = to_iso(summary.get("EDD") or payload.get("edd") or "")
        res.status_raw = summary.get("status") or (scans[0].get("label") if scans else "")

        # XpressBees returns newest-first; finalize() re-sorts chronologically.
        for scan in scans:
            res.events.append(TrackEvent(
                timestamp=to_iso(scan.get("shipmentDate")),
                status=scan.get("label") or "",
                location=scan.get("location") or "",
                remark="milestone" if scan.get("isMilestone") else "",
            ))

        res.status = classify(res.status_raw)
        return res
