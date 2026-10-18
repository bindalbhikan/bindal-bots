#!/usr/bin/env python
"""Save a Myntra partner portal login for the bots, per seller account.

    python scripts/myntra_session.py ACCOUNT [FILE]      ACCOUNT: MAIN (19170), DBCC (32207), FMCC (35426)

How to get the login, once per account:
  1. In Chrome, log in on https://partners.myntrainfo.com with that account.
  2. F12 > Network > reload the page > click any partnersapi.myntrainfo.com request >
     Headers > Request Headers > right-click the "cookie:" value > Copy value.
  3. Run the command. Without FILE the cookie line is read from the clipboard.

The login cookies (erp.at, erp.rt, session) are httpOnly: browser JavaScript and page-level
network captures (netcap) never contain them, only DevTools shows them.

The cookies are checked against the portal (logged in, and really that account), then saved to
the Telegram worker (the copy the bots read; DASHBOARD_URL / DASHBOARD_TOKEN from .env) and to
myntra_session_<account>.json here (git-ignored). MAIN also goes to the GitHub secret
MYNTRA_SESSION_JSON. --no-push only writes the local file. After that the bot keeps every
saved session alive by using it twice a day and saving the cookies the portal renews.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bindal.sale.myntra_portal import ACCOUNTS, MyntraPortal, SessionExpired, parse_session  # noqa: E402

REPO = "bindalbhikan/bindal-bots"


def clipboard() -> str:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def push(text: str, account: str) -> bool:
    from dotenv import dotenv_values
    import requests
    cfg = {**dotenv_values(ROOT / ".env"), **os.environ}
    url, token = (cfg.get("DASHBOARD_URL") or "").rstrip("/"), cfg.get("DASHBOARD_TOKEN") or ""
    if not url or not token:
        print("Worker NOT updated: DASHBOARD_URL / DASHBOARD_TOKEN missing in .env")
        return False
    w = requests.post(f"{url}/api/myntra-session", data=text, timeout=30, params={"account": account.lower()},
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    print(f"Worker session {account} updated" if w.ok else f"Worker NOT updated: HTTP {w.status_code} {w.text[:100]}")
    if not w.ok:
        return False
    if account == "MAIN":   # fallback copy for the sale run
        r = subprocess.run(["gh", "secret", "set", "MYNTRA_SESSION_JSON", "--repo", REPO],
                           input=text, text=True, capture_output=True, cwd=ROOT)
        print(f"GitHub secret MYNTRA_SESSION_JSON updated on {REPO}" if not r.returncode
              else f"GitHub secret NOT updated: {(r.stderr or r.stdout).strip()}")
    return True


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--no-push"]
    account = (args[0].upper() if args else "")
    if account not in ACCOUNTS:
        print(f"Usage: python scripts/myntra_session.py {'|'.join(ACCOUNTS)} [FILE]")
        return 2
    if len(args) > 1:
        source = Path(args[1])
        if not source.is_file():
            print(f"{source} not found")
            return 1
        text, where = source.read_text(encoding="utf-8-sig"), source.name
    else:
        text, where = clipboard(), "the clipboard"
    if "erp.at" not in text and "session" not in text:
        print(f"No portal login cookies in {where}: copy the cookie: value from DevTools first (see the top of this script)")
        return 1
    try:
        session = parse_session(text)
        portal = MyntraPortal(session)
        portal.check()
    except SessionExpired as exc:
        print(f"These cookies are not logged in ({exc}). Copy the cookie line again while logged in.")
        return 1
    pid = portal.partner_id()
    if pid and pid != ACCOUNTS[account]:
        other = next((a for a, p in ACCOUNTS.items() if p == pid), str(pid))
        print(f"This login is for {other} ({pid}), not {account} ({ACCOUNTS[account]}). Nothing saved.")
        return 1
    session.saved_at = datetime.now()
    data = {"savedAt": session.saved_at.isoformat(timespec="seconds"), "source": "script", "cookies": session.cookies}
    out = json.dumps(data, separators=(",", ":"))
    path = ROOT / ("myntra_session.json" if account == "MAIN" else f"myntra_session_{account.lower()}.json")
    path.write_text(out, encoding="utf-8")
    print(f"Logged in as {account} ({pid or 'partner id not shown'}). Saved {len(session.cookies)} cookies to {path.name}")
    if "--no-push" in argv:
        return 0
    return 0 if push(out, account) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
