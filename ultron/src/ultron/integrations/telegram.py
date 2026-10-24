"""Telegram messages from the agent (Bot API): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (comma-separated
chats allowed). Long messages are cut on line boundaries; a part Telegram refuses as HTML is sent
as plain text instead of being lost.
"""
from __future__ import annotations

import html
import logging
import re

import requests

from ..core.config import env

log = logging.getLogger("ultron")
MAX_LEN = 4000  # Telegram hard limit is 4096
API = "https://api.telegram.org/bot{token}/{method}"


def esc(value) -> str:
    return html.escape(str(value))


def md(text: str) -> str:
    """A model's light markdown as Telegram HTML: **bold**, `code`, "- " / "* " bullets, # headings,
    and | table | rows | as "a · b" lines (a table does not fit a phone); everything else escaped.
    Every tag opens and closes on one line, so a message can be cut at any line."""
    out = []
    for line in esc(text).splitlines():
        if re.fullmatch(r"\s*\|?[\s:|-]*-[\s:|-]*\|?\s*", line):        # |---|---| separator row
            continue
        if line.strip().startswith("|"):
            line = " · ".join(c.strip() for c in line.strip().strip("|").split("|") if c.strip())
        line = re.sub(r"^\s{0,3}#{1,6}\s*(.+)$", r"<b>\1</b>", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        out.append(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line).rstrip())
    return "\n".join(out)


def parts(text: str, limit: int = MAX_LEN) -> list[str]:
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            out, cur, line = out + ([cur] if cur else []) + [line[:limit]], "", line[limit:]
        if len(cur) + len(line) > limit:
            out.append(cur)
            cur = ""
        cur += line
    return [p for p in out + [cur] if p.strip()]


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _post(token: str, method: str, **kw) -> requests.Response | None:
    try:
        return requests.post(API.format(token=token, method=method), timeout=120, **kw)
    except requests.RequestException as exc:
        log.error("Telegram %s failed: %s", method, exc)
        return None


class WebhookSet(RuntimeError):
    """The bot is answered through a webhook (a worker); it cannot also be polled."""


class OtherInstance(RuntimeError):
    """Another poller took the bot (Telegram allows one at a time)."""


def chats() -> list[str]:
    """The owner's chats: TELEGRAM_CHAT_ID, comma-separated."""
    return [c.strip() for c in env("TELEGRAM_CHAT_ID").split(",") if c.strip()]


def call(method: str, **payload) -> dict | None:
    """One Bot API call; its `result`, or None when it failed (logged)."""
    r = _post(env("TELEGRAM_BOT_TOKEN"), method, json=payload)
    if r is None:
        return None
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok"):
        log.error("Telegram %s: %s %s", method, r.status_code, str(data or r.text)[:300])
        return None
    return data.get("result")


def send_buttons(chat: str, text: str, rows: list[list[tuple[str, str]]]) -> dict | None:
    """A message with inline buttons: rows of (label, callback data)."""
    return call("sendMessage", chat_id=chat, text=text[:MAX_LEN], parse_mode="HTML", disable_web_page_preview=True,
                reply_markup={"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]})


def updates(offset: int, timeout: int = 50) -> list[dict]:
    """New messages and button taps (long polling). Raises RuntimeError when Telegram refuses,
    e.g. 409 while the bot has a webhook (a worker) set: polling and a webhook cannot share a bot."""
    token = env("TELEGRAM_BOT_TOKEN")
    try:
        r = requests.get(API.format(token=token, method="getUpdates"), timeout=timeout + 15,
                         params={"offset": offset, "timeout": timeout, "allowed_updates": '["message","callback_query"]'})
    except requests.RequestException as exc:
        log.warning("Telegram getUpdates: %s", exc)
        return []
    if r.status_code == 409 and "webhook" in r.text.lower():
        raise WebhookSet("this bot has a webhook set (a worker answers it): use a separate bot for "
                         "ultron serve, or remove the webhook first")
    if r.status_code == 409:
        raise OtherInstance("another ultron serve is polling this bot")
    if not r.ok:
        raise RuntimeError(f"Telegram getUpdates: {r.status_code} {r.text[:200]}")
    return r.json().get("result", [])


def send(text: str, chat: str | int | None = None) -> bool:
    """Send to one chat, or to every chat in TELEGRAM_CHAT_ID."""
    token = env("TELEGRAM_BOT_TOKEN")
    targets = [str(chat)] if chat else chats()
    if not token or not targets:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) - message printed instead")
        print(text)
        return False
    ok = True
    for chat in targets:
        for part in parts(text):
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
