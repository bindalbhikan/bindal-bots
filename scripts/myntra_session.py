#!/usr/bin/env python
"""Save a Myntra partner portal login for the sale bot (SJIT report).

    python scripts/myntra_session.py [FILE]      (default FILE: myntra_cookie.txt)

FILE holds the portal login cookies, taken from Chrome while logged in on the portal:
DevTools (F12) > Network > any partnersapi.myntrainfo.com request > Request Headers >
copy the whole "cookie:" value (or right-click > Copy > Copy as cURL) and paste it into FILE.
The login cookies (erp.at, erp.rt, session) are httpOnly, so browser JavaScript cannot read them.

The cookies are checked against the portal, then saved to myntra_session.json (git-ignored),
to the Telegram worker (the copy the bot reads, DASHBOARD_URL / DASHBOARD_TOKEN from .env)
and to the GitHub secret MYNTRA_SESSION_JSON (gh CLI). --no-push only writes the local file.
After that the bot keeps the session alive by itself: every run saves the renewed cookies.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bindal.sale.myntra_portal import MyntraPortal, SessionExpired, parse_session  # noqa: E402

OUT = ROOT / "myntra_session.json"
DEFAULT_FILE = ROOT / "myntra_cookie.txt"
REPO = "bindalbhikan/bindal-bots"


def push(text: str) -> bool:
    from dotenv import dotenv_values
    import requests
    cfg = {**dotenv_values(ROOT / ".env"), **os.environ}
    url, token = (cfg.get("DASHBOARD_URL") or "").rstrip("/"), cfg.get("DASHBOARD_TOKEN") or ""
    ok = True
    if url and token:
        w = requests.post(f"{url}/api/myntra-session", data=text, timeout=30,
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        print("Worker session updated" if w.ok else f"Worker NOT updated: HTTP {w.status_code} {w.text[:100]}")
        ok = w.ok
    else:
        print("Worker NOT updated: DASHBOARD_URL / DASHBOARD_TOKEN missing in .env")
        ok = False
    r = subprocess.run(["gh", "secret", "set", "MYNTRA_SESSION_JSON", "--repo", REPO],
                       input=text, text=True, capture_output=True, cwd=ROOT)
    if r.returncode:
        print(f"GitHub secret NOT updated: {(r.stderr or r.stdout).strip()}")
        return False
    print(f"GitHub secret MYNTRA_SESSION_JSON updated on {REPO}")
    return ok


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--no-push"]
    source = Path(args[0]) if args else DEFAULT_FILE
    if not source.is_file():
        print(f"{source} not found: paste the portal cookie line into it first (see the top of this script)")
        return 1
    try:
        session = parse_session(source.read_text(encoding="utf-8-sig"))
        MyntraPortal(session).check()
    except SessionExpired as exc:
        print(f"These cookies are not logged in ({exc}). Copy the cookie line again while logged in.")
        return 1
    session.saved_at = session.saved_at or datetime.now()
    data = {"savedAt": session.saved_at.isoformat(timespec="seconds"), "source": "script", "cookies": session.cookies}
    text = json.dumps(data, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    print(f"Logged in. Saved {len(session.cookies)} cookies to {OUT}")
    if "--no-push" in argv:
        return 0
    return 0 if push(text) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
