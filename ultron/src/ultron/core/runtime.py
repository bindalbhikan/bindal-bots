"""The frame the agent's commands run in: .env next to agent.toml, logging, and a Telegram alert
when the agent itself fails."""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from typing import Callable

from . import config
from ..integrations import telegram

log = logging.getLogger("ultron")


def load_dotenv() -> None:
    """Load .env next to agent.toml without overriding variables already set (GitHub Actions)."""
    try:
        from dotenv import load_dotenv as _load
        _load(config.find().parent / ".env", override=False)
    except (ImportError, FileNotFoundError):
        pass


def setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; keep emoji printable
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)], force=True)


def run(title: str, parser: argparse.ArgumentParser, fn: Callable[[argparse.Namespace], int], argv=None) -> int:
    load_dotenv()
    setup_logging()
    if not any(a.dest == "no_telegram" for a in parser._actions):
        parser.add_argument("--no-telegram", action="store_true", help="print the message instead of sending it")
    args = parser.parse_args(argv)
    try:
        return fn(args)
    except Exception as exc:  # noqa: BLE001 - report any failure
        log.error("%s failed:\n%s", title, traceback.format_exc())
        msg = f"❌ <b>{telegram.esc(title)} failed</b>\n\n<code>{telegram.esc(str(exc)[:1500])}</code>"
        print(msg) if args.no_telegram else telegram.send(msg)
        return 1
