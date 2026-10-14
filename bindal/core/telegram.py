"""Telegram notifications (Bot API) for every bot: text, photos and files.

TELEGRAM_CHAT_ID may hold several comma-separated chats. Long messages are cut
on line boundaries and any HTML tag still open at a cut (<pre>, <b>, <i> ...)
is closed and reopened in the next part, so Telegram never rejects a part for
broken HTML. If Telegram still refuses the HTML, the part is sent as plain text
instead of being lost.
"""
from __future__ import annotations

import html
import logging
import os
import re

import requests

log = logging.getLogger(__name__)
MAX_LEN = 4000  # Telegram hard limit is 4096
API = "https://api.telegram.org/bot{token}/{method}"
TAG = re.compile(r"<(/?)(b|strong|i|em|u|s|code|pre|a)(\s[^>]*)?>", re.I)


def esc(value) -> str:
    return html.escape(str(value))


def _config() -> tuple[str, list[str]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chats = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    return token, chats


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def split_html(text: str, limit: int = MAX_LEN) -> list[str]:
    """Cut text into parts of at most `limit` characters without breaking HTML tags."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    open_tags: list[tuple[str, str]] = []   # (name, opening tag text)
    current = ""

    def closing() -> str:
        return "".join(f"</{name}>" for name, _ in reversed(open_tags))

    def reopening() -> str:
        return "".join(tag for _, tag in open_tags)

    def track(piece: str) -> None:
        for m in TAG.finditer(piece):
            name = m.group(2).lower()
            if m.group(1):
                for i in range(len(open_tags) - 1, -1, -1):
                    if open_tags[i][0] == name:
                        del open_tags[i]
                        break
            else:
                open_tags.append((name, m.group(0)))

    lines = text.split("\n")
    for n, line in enumerate(lines):
        piece = line + ("\n" if n < len(lines) - 1 else "")
        # one line longer than a whole message: cut it outside entities (&amp;) and tags
        while len(piece) > limit // 2:
            cut = limit // 2
            amp, lt = piece.rfind("&", 0, cut), piece.rfind("<", 0, cut)
            if amp > piece.rfind(";", 0, cut):
                cut = amp
            if lt > piece.rfind(">", 0, cut):
                cut = lt
            cut = cut or limit // 2
            head, piece = piece[:cut], piece[cut:]
            if len(current) + len(head) + len(closing()) > limit:
                parts.append(current + closing())
                current = reopening()
            current += head
            track(head)
        if current and len(current) + len(piece) + len(closing()) > limit:
            parts.append(current + closing())
            current = reopening()
        current += piece
        track(piece)
    if current.strip():
        parts.append(current + closing())
    return parts


def _post(token: str, method: str, **kw) -> requests.Response | None:
    try:
        return requests.post(API.format(token=token, method=method), timeout=120, **kw)
    except requests.RequestException as exc:
        log.error("Telegram %s failed: %s", method, exc)
        return None


def send(text: str) -> bool:
    token, chats = _config()
    if not token or not chats:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) - message printed instead")
        print(text)
        return False
    ok = True
    for chat in chats:
        for part in split_html(text):
            r = _post(token, "sendMessage", json={"chat_id": chat, "text": part, "parse_mode": "HTML",
                                                  "disable_web_page_preview": True})
            if r is not None and r.status_code == 400 and "parse" in r.text.lower():
                log.warning("Telegram refused the HTML (%s); sending as plain text", r.text[:200])
                r = _post(token, "sendMessage", json={"chat_id": chat, "text": _plain(part)[:4096],
                                                      "disable_web_page_preview": True})
            if r is None or not r.ok:
                ok = False
                if r is not None:
                    log.error("Telegram send to %s failed: %s %s", chat, r.status_code, r.text[:300])
    return ok


def send_file(path, caption: str = "", as_document: bool = False) -> bool:
    """Send a file to every chat: an image as a photo (inline preview) or any file as a document."""
    token, chats = _config()
    if not token or not chats:
        log.warning("Telegram not configured - %s not sent", path)
        return False
    method, field = ("sendDocument", "document") if as_document else ("sendPhoto", "photo")
    caption = split_html(caption, 1000)[0] if caption else ""
    ok = True
    for chat in chats:
        try:
            with open(path, "rb") as fh:
                r = _post(token, method, data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                          files={field: (os.path.basename(str(path)), fh)})
        except OSError as exc:
            log.error("Telegram %s: cannot read %s: %s", method, path, exc)
            return False
        if r is None or not r.ok:
            ok = False
            if r is not None:
                log.error("Telegram %s to %s failed: %s %s", method, chat, r.status_code, r.text[:300])
    return ok


def send_document(path, caption: str = "") -> bool:
    return send_file(path, caption, as_document=True)
