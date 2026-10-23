"""Telegram notifications (Bot API) for every bot: text, photos and files.

TELEGRAM_CHAT_ID may hold several comma-separated chats. Long messages are cut
on line boundaries and any HTML tag still open at a cut (<pre>, <b>, <i> ...)
is closed and reopened in the next part, so Telegram never rejects a part for
broken HTML. If Telegram still refuses the HTML, the part is sent as plain text
instead of being lost.

A report with long lists (AWBs, orders, SKUs) is built as blocks: text and Listing. When the
whole report fits in one message the lists go inline; otherwise the message keeps each list's
title and count and every row goes to one attached Excel, a sheet per list (send_report).
<pre> tables stay within PHONE_WIDTH characters a line so they do not wrap on a phone.
"""
from __future__ import annotations

import html
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)
MAX_LEN = 4000  # Telegram hard limit is 4096
API = "https://api.telegram.org/bot{token}/{method}"
PHONE_WIDTH = 28  # monospace characters a <pre> line shows on a phone without wrapping
TAG = re.compile(r"<(/?)(b|strong|i|em|u|s|code|pre|a)(\s[^>]*)?>", re.I)


def esc(value) -> str:
    return html.escape(str(value))


def md(text: str) -> str:
    """A model's light markdown as Telegram HTML: **bold**, `code`, "- " / "* " bullets, # headings,
    and | table | rows | as "a · b" lines (a table does not fit a phone); everything else escaped."""
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


# ------------------------------------------------------------------ reports with long lists

@dataclass
class Listing:
    """A list inside a report: `lines` (HTML) inline when the report fits in one message, otherwise
    `rows` (column -> value) on the Excel sheet `sheet`. `title` stays in the message either way."""
    title: str
    lines: list[str]
    rows: list[dict] = field(default_factory=list)
    sheet: str = "List"


def render(report: list, inline: bool = True) -> str:
    """The report's text: lists inline, or only their titles with a pointer to the Excel."""
    out = []
    for block in report:
        if not isinstance(block, Listing):
            out.append(block)
        elif inline or not block.rows:
            out += [block.title, *block.lines]
        else:
            out += [block.title, f"📎 all {len(block.rows)} in the Excel below (sheet {esc(block.sheet)})"]
    return "\n".join(out)


def fits(report: list) -> bool:
    return len(render(report)) <= MAX_LEN


def write_xlsx(report: list, path) -> Path | None:
    """One sheet per list with rows: bold frozen header, columns as wide as their text."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    lists = [b for b in report if isinstance(b, Listing) and b.rows]
    if not lists:
        return None
    wb = Workbook()
    wb.remove(wb.active)
    for block in lists:
        name, n = re.sub(r"[\\/*?:\[\]]", "-", block.sheet)[:31] or "List", 2
        while name in wb.sheetnames:
            name, n = f"{block.sheet[:28]} {n}", n + 1
        ws = wb.create_sheet(name)
        cols = list(dict.fromkeys(k for r in block.rows for k in r))
        ws.append(cols)
        for r in block.rows:
            ws.append([r.get(c) for c in cols])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"
        for i, c in enumerate(cols, 1):
            width = max(len(str(c)), *(len(str(r.get(c) if r.get(c) is not None else "")) for r in block.rows))
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(width + 2, 60)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def send_report(report: list, name: str, dry: bool = False) -> bool:
    """Send a report of text and Listing blocks: one message with the lists inline when it fits,
    otherwise the message with the lists' titles plus an Excel (name_YYYY-MM-DD_HHMM.xlsx) of every row.
    dry: print the message and keep the Excel locally instead of sending."""
    if fits(report):
        text = render(report)
        print(text) if dry else None
        return True if dry else send(text)
    text = render(report, inline=False)
    path = write_xlsx(report, Path(tempfile.mkdtemp(prefix="tg_")) / f"{name}_{datetime.now():%Y-%m-%d_%H%M}.xlsx")
    if dry:
        print(text)
        print(f"[Excel: {path}]")
        return True
    ok = send(text)
    if path:
        lists = [b for b in report if isinstance(b, Listing) and b.rows]
        caption = f"📎 <b>{esc(name.replace('_', ' '))}</b> · " + " · ".join(f"{esc(b.sheet)} {len(b.rows)}" for b in lists)
        ok = send_document(path, caption) and ok
    return ok
