"""SJIT units from Myntra's Seller_Orders_Report CSV.

The report is requested on the Myntra partner portal in the morning and saved as
a CSV into a Google Drive folder (by the PC-side Myntra job, or by hand). The
sale report run reads every CSV in that folder and uses the newest data per
order line. Rules (unchanged from the original main.py):
  * only po_type SJIT or FBM (PPMP is excluded)
  * GROSS  = order lines by "created on" date
  * Cancel = lines with order status F, by "cancelled on" date

The partner portal login has a reCAPTCHA, so the bot fetches the report with a
saved login session (myntra_portal.py); a report dropped by hand into the SJIT
Drive folder (SJIT_DRIVE_FOLDER_ID) is still read as well.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import Counter
from datetime import date, datetime

from google.auth.transport.requests import AuthorizedSession

from ..core.gauth import credentials

log = logging.getLogger(__name__)

SJIT_PO_TYPES = {"sjit", "fbm"}
DRIVE_API = "https://www.googleapis.com/drive/v3/files"
SCOPES = ["https://www.googleapis.com/auth/drive"]


def _dt(value: str) -> datetime | None:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def _csv_texts(name: str, data: bytes) -> list[str]:
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return [z.read(n).decode("utf-8-sig", "replace") for n in z.namelist() if n.lower().endswith(".csv")]
    return [data.decode("utf-8-sig", "replace")]


def parse(files: list[tuple[str, bytes]]) -> tuple[Counter, Counter, set[date]]:
    """(gross per day, cancel per day, days the files cover) from report files, newest first."""
    lines: dict[str, dict] = {}
    covered: set[date] = set()
    for name, data in files:
        for text in _csv_texts(name, data):
            for raw in csv.DictReader(io.StringIO(text)):
                # header case differs between portal downloads ("Created On" vs "created on")
                row = {(k or "").strip().casefold(): v for k, v in raw.items()}
                created = _dt(row.get("created on", ""))
                if created:
                    covered.add(created.date())
                if (row.get("po_type") or "").strip().casefold() not in SJIT_PO_TYPES:
                    continue
                key = (row.get("order line id") or "").strip()
                if key and key not in lines:  # newest file wins
                    lines[key] = row
    gross, cancel = Counter(), Counter()
    for row in lines.values():
        created = _dt(row.get("created on", ""))
        if not created:
            continue
        gross[created.date()] += 1
        if (row.get("order status") or "").strip().casefold() == "f":
            cancelled = _dt(row.get("cancelled on", "")) or created
            cancel[cancelled.date()] += 1
    return gross, cancel, covered


def files_from_drive(folder_id: str, limit: int = 10) -> list[tuple[str, bytes]]:
    """Newest CSV / ZIP files of the SJIT drop folder."""
    http = AuthorizedSession(credentials(SCOPES))
    r = http.get(DRIVE_API, timeout=60, params={
        "q": f"'{folder_id}' in parents and trashed=false",
        "orderBy": "modifiedTime desc", "pageSize": limit,
        "fields": "files(id,name,modifiedTime)", "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true"})
    if not r.ok:
        raise RuntimeError(f"SJIT folder listing failed: HTTP {r.status_code} {r.text[:200]}")
    out = []
    for f in r.json().get("files", []):
        if f["name"].lower().endswith((".csv", ".zip")):
            d = http.get(f"{DRIVE_API}/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"}, timeout=120)
            if d.ok:
                out.append((f["name"], d.content))
    log.info("SJIT files read from Drive: %s", [n for n, _ in out])
    return out
