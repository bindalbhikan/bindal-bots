"""Send a bot's latest numbers to the dashboard.

Each run ends with publish(<key>, data): a small JSON summary posted to the Telegram worker
(DASHBOARD_URL, authorised by DASHBOARD_TOKEN), which keeps it in Cloudflare KV for the
dashboard page. Publishing never fails a run: without the settings, or on any error, it only
logs a warning.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

import requests

from .config import env

log = logging.getLogger(__name__)


def _default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):          # numpy / pandas scalars
        return value.item()
    return str(value)


def publish(key: str, data: dict) -> bool:
    url, token = env("DASHBOARD_URL"), env("DASHBOARD_TOKEN")
    if not url or not token:
        log.info("Dashboard not configured (DASHBOARD_URL / DASHBOARD_TOKEN) - %s not published", key)
        return False
    body = json.dumps({**data, "published_at": datetime.now().isoformat(timespec="seconds")},
                      default=_default, ensure_ascii=False)
    try:
        r = requests.post(f"{url.rstrip('/')}/api/push/{key}", data=body.encode("utf-8"), timeout=30,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        if not r.ok:
            log.warning("Dashboard push %s failed: HTTP %s %s", key, r.status_code, r.text[:200])
            return False
        log.info("Dashboard updated: %s (%d bytes)", key, len(body))
        return True
    except requests.RequestException as exc:
        log.warning("Dashboard push %s failed: %s", key, exc)
        return False
