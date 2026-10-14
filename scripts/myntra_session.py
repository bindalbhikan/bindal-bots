#!/usr/bin/env python
"""Save a Myntra partner portal login for the sale bot (SJIT report) and put it in the
GitHub secret MYNTRA_SESSION_JSON (needs the gh CLI, logged in).

    python scripts/myntra_session.py FILE       from a file: a network capture export
                                                (netcap-*.json), a "Copy as cURL" text, a
                                                Cookie header, or a saved session JSON
    python scripts/myntra_session.py            the newest netcap-*.json in Downloads or here;
                                                none there: log in in a Chrome window (below)
    add --no-push to only save myntra_session.json

Chrome window login (pip install playwright once; uses the Chrome already installed):

A normal Chrome window opens on the partner portal (its own profile, no automation:
an automated browser gets "network error" from the portal's bot protection). Log in
(captcha and all), then come back here and press Enter. Only then the script attaches
to that Chrome, reads the portal cookies, checks them the same way the bot will, and
writes them to myntra_session.json (git-ignored).

Browser JavaScript cannot do this: the portal's login cookies are httpOnly.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bindal.sale.myntra_portal import PORTAL, MyntraPortal, Session, SessionExpired, parse_session  # noqa: E402

OUT = ROOT / "myntra_session.json"
PROFILE = ROOT / "output" / "myntra_chrome"    # kept, so the next login may be quicker
PORT = 9333
REPO = "bindalbhikan/bindal-bots"
CHROME = [Path(os.environ.get(v, "")) / "Google/Chrome/Application/chrome.exe"
          for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")] + \
         [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"), Path("/usr/bin/google-chrome")]


def from_browser() -> Session | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is missing:  pip install playwright")
        return None
    chrome = next((p for p in CHROME if p.is_file()), None)
    if not chrome:
        print("Google Chrome not found")
        return None
    proc = subprocess.Popen([str(chrome), f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
                             "--no-first-run", "--no-default-browser-check", PORTAL])
    input("Log in on the portal in the new Chrome window, open the Dashboard, then press Enter here... ")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
            raw = browser.contexts[0].cookies()
            browser.close()
    finally:
        proc.terminate()
    cookies = [{k: c.get(k) for k in ("name", "value", "domain", "path", "expires", "httpOnly", "secure")}
               for c in raw if "myntrainfo" in c["domain"]]
    return Session(cookies, datetime.now())


def newest_capture() -> Path | None:
    files = [p for d in (Path.home() / "Downloads", ROOT) if d.is_dir() for p in d.glob("netcap-*.json")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def main(argv: list[str]) -> int:
    push = "--no-push" not in argv
    args = [a for a in argv if a != "--no-push"]
    source = Path(args[0]) if args else newest_capture()
    if source:
        print(f"Reading {source}")
        try:
            session = parse_session(source.read_text(encoding="utf-8-sig"))
        except SessionExpired as exc:
            print(f"No session in {source.name}: {exc}")
            return 1
        session.saved_at = session.saved_at or datetime.now()
    else:
        session = from_browser()
        if not session:
            return 1
    try:
        MyntraPortal(session).check()       # the bot's own HTTP client, not the browser
    except SessionExpired as exc:
        names = ", ".join(sorted(c["name"] for c in session.cookies))
        print(f"These cookies are not logged in ({exc}).\nCookies found: {names}\n"
              "A capture made by page JavaScript cannot see the httpOnly login cookies: use "
              "Copy as cURL in DevTools > Network, or a capture that reads chrome.cookies.")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Bot check failed (saved anyway): {exc}")
    data = {"savedAt": session.saved_at.isoformat(timespec="seconds"), "cookies": session.cookies}
    text = json.dumps(data, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    ends = session.expires()
    print(f"Saved {len(session.cookies)} cookies to {OUT}")
    print(f"Login cookies expire: {ends:%d %b %Y %H:%M}" if ends else "Expiry date not known (bot assumes ~15 days)")
    if push:
        # the bot reads the worker's copy first (the Chrome extension writes it): replace it too
        from dotenv import dotenv_values
        import requests
        cfg = {**dotenv_values(ROOT / ".env"), **os.environ}
        url, token = (cfg.get("DASHBOARD_URL") or "").rstrip("/"), cfg.get("DASHBOARD_TOKEN") or ""
        if url and token:
            w = requests.post(f"{url}/api/myntra-session", data=text, timeout=30,
                              headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            print("Worker session updated" if w.ok else f"Worker NOT updated: HTTP {w.status_code} {w.text[:100]}")
        r = subprocess.run(["gh", "secret", "set", "MYNTRA_SESSION_JSON", "--repo", REPO],
                           input=text, text=True, capture_output=True, cwd=ROOT)
        if r.returncode:
            print(f"GitHub secret NOT updated: {(r.stderr or r.stdout).strip()}")
            return 1
        print(f"GitHub secret MYNTRA_SESSION_JSON updated on {REPO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
