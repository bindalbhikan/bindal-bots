"""The frame every bot runs in: settings, logging, a Telegram alert when a run fails, and the
run's result (ok / failed, how long, the error) on the dashboard."""
from __future__ import annotations

import argparse
import logging
import time
import traceback
from datetime import datetime
from typing import Callable

from . import dashboard, telegram
from .config import env, load_dotenv, setup_logging

log = logging.getLogger("bindal")


def run_bot(title: str, parser: argparse.ArgumentParser, run: Callable[[argparse.Namespace], int],
            argv=None, key: str = "") -> int:
    """Parse args, run, and on any error log the traceback and tell Telegram (unless --no-telegram).
    `key` names the bot on the dashboard (sale, vms, ajio_tat, ajio_spf)."""
    load_dotenv()
    setup_logging()
    if not any(a.dest == "no_telegram" for a in parser._actions):
        parser.add_argument("--no-telegram", action="store_true", help="print the report instead of sending it")
    args = parser.parse_args(argv)
    started, error, code = time.time(), "", 1
    try:
        code = run(args)
        return code
    except Exception as exc:  # noqa: BLE001 - the whole point is to report any failure
        error = str(exc)
        log.error("%s failed:\n%s", title, traceback.format_exc())
        msg = (f"❌ <b>{telegram.esc(title)} failed</b>\n"
               f"<i>{telegram.esc(env('RUN_MODE', 'local'))}</i>\n\n"
               f"<code>{telegram.esc(str(exc)[:1500])}</code>")
        extra = getattr(exc, "notes", None)
        if extra:
            msg += "\n\n⚠️ <b>Also</b>\n" + "\n".join(f"• {telegram.esc(n)[:200]}" for n in extra)
        if args.no_telegram:
            print(msg)
        else:
            telegram.send(msg)
        return 1
    finally:
        if key:
            dashboard.publish(f"run_{key}", {
                "ok": code == 0 and not error, "error": error[:500], "mode": env("RUN_MODE", "local"),
                "started_at": datetime.fromtimestamp(started).isoformat(timespec="seconds"),
                "minutes": round((time.time() - started) / 60, 1),
                "args": " ".join(argv or []) if argv is not None else "",
            })
