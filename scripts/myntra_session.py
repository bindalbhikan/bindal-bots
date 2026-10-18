#!/usr/bin/env python
"""Save a Myntra partner portal login for the bots, per seller account.

    python scripts/myntra_session.py                  every account with a file in scripts/:
                                                      main.txt (MAIN 19170), dbcc.txt (DBCC 32207),
                                                      fmcc.txt (FMCC 35426)
    python scripts/myntra_session.py ACCOUNT          that account: its .txt file, else the clipboard
    python scripts/myntra_session.py ACCOUNT FILE     that account from FILE

How to get the login, once per account:
  1. In Chrome, log in on https://partners.myntrainfo.com with that account.
  2. F12 > Network > reload the page > click any partnersapi.myntrainfo.com request > Headers.
  3. Copy the request headers (or just the "cookie" value) into scripts/<account>.txt.
  4. Run the command, then delete the .txt files (they hold live logins).

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

from bindal.portals.myntra import ACCOUNTS, MyntraPortal, SessionExpired, parse_session  # noqa: E402

REPO = "bindalbhikan/bindal-bots"
DROP = ROOT / "scripts"            # main.txt, dbcc.txt, fmcc.txt: request headers copied from DevTools


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


def save(account: str, text: str, where: str, push_it: bool) -> bool:
    """Check one account's cookies and save them; True when saved."""
    if "erp.at" not in text and "session" not in text:
        print(f"{account}: no portal login cookies in {where} (see the top of this script)")
        return False
    try:
        session = parse_session(text)
        portal = MyntraPortal(session)
        portal.check()
    except SessionExpired as exc:
        print(f"{account}: the cookies in {where} are not logged in ({exc}). Copy them again while logged in.")
        return False
    pid = portal.partner_id()
    if pid and pid != ACCOUNTS[account]:
        other = next((a for a, p in ACCOUNTS.items() if p == pid), str(pid))
        print(f"{account}: the login in {where} is for {other} ({pid}), not {account} ({ACCOUNTS[account]}). Nothing saved.")
        return False
    cookies = portal.cookies() or session.cookies      # as renewed by the check
    data = {"savedAt": datetime.now().isoformat(timespec="seconds"), "source": "script", "cookies": cookies}
    out = json.dumps(data, separators=(",", ":"))
    path = ROOT / ("myntra_session.json" if account == "MAIN" else f"myntra_session_{account.lower()}.json")
    path.write_text(out, encoding="utf-8")
    print(f"{account}: logged in ({pid or 'partner id not shown'}), {len(cookies)} cookies saved to {path.name}")
    return push(out, account) if push_it else True


def main(argv: list[str]) -> int:
    push_it = "--no-push" not in argv
    args = [a for a in argv if a != "--no-push"]
    if args and args[0].upper() not in ACCOUNTS:
        print(f"Usage: python scripts/myntra_session.py [{'|'.join(ACCOUNTS)}] [FILE]")
        return 2
    if len(args) > 1:                       # one account, from the file given
        source = Path(args[1])
        if not source.is_file():
            print(f"{source} not found")
            return 1
        return 0 if save(args[0].upper(), source.read_text(encoding="utf-8-sig"), source.name, push_it) else 1
    accounts = [args[0].upper()] if args else list(ACCOUNTS)
    done, tried = 0, 0
    for account in accounts:
        source = DROP / f"{account.lower()}.txt"
        if source.is_file():
            text, where = source.read_text(encoding="utf-8-sig"), f"scripts/{source.name}"
        elif args:                          # one account named, no file: the clipboard
            text, where = clipboard(), "the clipboard"
        else:
            continue
        tried += 1
        done += save(account, text, where, push_it)
    if not tried:
        print(f"Nothing to do: put the copied request headers in scripts/main.txt, dbcc.txt or fmcc.txt")
        return 1
    print(f"{done} of {tried} account(s) saved." + (" Delete the .txt files now: they hold live logins." if done else ""))
    return 0 if done == tried else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
