"""Google service-account credentials, shared by the Sheets and Drive modules.

GitHub Actions: the whole key JSON in GOOGLE_SERVICE_ACCOUNT_JSON.
Local runs: the key file at GOOGLE_SERVICE_ACCOUNT_FILE (relative paths are
taken from the project folder, default service_account.json).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from google.oauth2.service_account import Credentials

from .config import ROOT


def credentials(scopes: list[str]) -> Credentials:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        return Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    path = Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip() or "service_account.json")
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise RuntimeError("Google credentials missing: set GOOGLE_SERVICE_ACCOUNT_JSON "
                           f"or put the key file at {path}")
    return Credentials.from_service_account_file(str(path), scopes=scopes)
