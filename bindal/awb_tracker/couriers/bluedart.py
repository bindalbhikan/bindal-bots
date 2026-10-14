"""Blue Dart -- server-rendered result page, parsed with selectolax.

The public tracking form puts a CAPTCHA in front of its portlet action, but
the result page itself is a plain GET on ``trackdartresult?trackFor=0&trackNo=``
which renders the same data without any challenge. Each waybill's panel holds
a ``SHIP<awb>`` details table and a ``SCAN<awb>`` scan table.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..base import BaseCourier
from ..models import NOT_FOUND, TrackEvent, TrackResult, classify, to_iso

RESULT_URL = "https://www.bluedart.com/web/guest/trackdartresult"
SITE = "https://www.bluedart.com"

# Label -> TrackResult attribute, matched case-insensitively on the details table.
FIELD_MAP = {
    "status": "status_raw",
    "reference no": "reference_no",
    "reference number": "reference_no",
    "origin": "origin",
    "from": "origin",
    "destination": "destination",
    "to": "destination",
    "expected date of delivery": "expected_delivery",
    "expected delivery date": "expected_delivery",
    "date of delivery": "delivered_on",
    "delivered on": "delivered_on",
}


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


class BlueDart(BaseCourier):
    name = "BLUEDART"
    aliases = ("BLUE DART", "BD", "BLUEDART EXPRESS")
    min_interval = 0.5
    #: the result page takes several waybills separated by commas
    batch_size = 20

    def _page(self, awbs: list[str]):
        resp = self.client.get(
            RESULT_URL,
            params={"trackFor": "0", "trackNo": ",".join(awbs)},
            headers={**self.base_headers(referer=SITE + "/web/guest/home", origin=SITE),
                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return HTMLParser(resp.text)

    def _fetch(self, awb: str) -> TrackResult:
        return self._parse(self._page([awb]), awb)

    def fetch_many(self, awbs) -> list[TrackResult]:
        awbs = [str(a).strip() for a in awbs]
        try:
            self.ensure_primed()
            tree = self._page(awbs)
        except Exception:
            return [self.fetch(a) for a in awbs]      # one by one, each with its own error
        return [self._parse(tree, a).finalize() for a in awbs]

    def _parse(self, tree, awb: str) -> TrackResult:
        res = self.new_result(awb)
        panel = tree.css_first(f"div#AWB{awb}")
        if panel is None:
            # Blue Dart: "Tracking will be visible once shipment is handed over to Blue Dart."
            res.status = NOT_FOUND
            res.error = "not handed over to Blue Dart yet (no record)"
            return res
        res.raw = panel.html
        res.ok = True

        # Shipment details: a two-column table of <th>label</th><td>value</td>.
        details = panel.css_first(f"div#SHIP{awb}")
        if details is not None:
            for row in details.css("tr"):
                header = row.css_first("th")
                value = row.css_first("td")
                if header is None or value is None:
                    continue
                label = _clean(header.text()).lower().rstrip(":")
                attr = FIELD_MAP.get(label)
                if attr and not getattr(res, attr):
                    setattr(res, attr, _clean(value.text()))

        # Scan table: Location | Details | Date | Time
        scans = panel.css_first(f"div#SCAN{awb}")
        if scans is not None:
            for row in scans.css("tbody tr"):
                cells = [_clean(c.text()) for c in row.css("td")]
                if len(cells) < 3:
                    continue
                location, detail, date = cells[0], cells[1], cells[2]
                time_part = cells[3] if len(cells) > 3 else ""
                res.events.append(TrackEvent(
                    timestamp=to_iso(f"{date} {time_part}".strip()),
                    status=detail,
                    location=location,
                ))

        if res.expected_delivery:
            res.expected_delivery = to_iso(res.expected_delivery)
        if res.delivered_on:
            res.delivered_on = to_iso(res.delivered_on)
        if not res.events and not res.status_raw:
            res.status = NOT_FOUND
            res.error = "no scan data on result page"
            return res

        res.status = classify(res.status_raw)
        return res
