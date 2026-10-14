"""The frame every bot runs in: settings, logging, and a Telegram alert when a run fails."""
from __future__ import annotations

import argparse
import logging
import traceback
from typing import Callable

from . import telegram
from .config import env, load_dotenv, setup_logging

log = logging.getLogger("bindal")


def run_bot(title: str, parser: argparse.ArgumentParser, run: Callable[[argparse.Namespace], int],
            argv=None) -> int:
    """Parse args, run, and on any error log the traceback and tell Telegram (unless --no-telegram)."""
    load_dotenv()
    setup_logging()
    if not any(a.dest == "no_telegram" for a in parser._actions):
        parser.add_argument("--no-telegram", action="store_true", help="print the report instead of sending it")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - the whole point is to report any failure
        log.error("%s failed:\n%s", title, traceback.format_exc())
        msg = (f"<b>❌ {telegram.esc(title)} - FAILED</b> ({telegram.esc(env('RUN_MODE', 'local'))})\n"
               f"<code>{telegram.esc(str(exc)[:1500])}</code>")
        extra = getattr(exc, "notes", None)
        if extra:
            msg += "\n" + "\n".join(telegram.esc(n)[:200] for n in extra)
        if args.no_telegram:
            print(msg)
        else:
            telegram.send(msg)
        return 1
