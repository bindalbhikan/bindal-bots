"""Settings shared by every bot: one .env at the project root, read once.

Every bot reads its settings through env() / flag() / num(), so a value set in
GitHub Secrets (Actions) or in the local .env behaves the same way.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output"

TRUE = ("1", "true", "yes", "on")
FALSE = ("0", "false", "no", "off")


def env(name: str, default: str = "") -> str:
    """The setting, or `default` when it is missing or blank. GitHub passes an unset
    repository variable as an empty string, which must not wipe out the default."""
    return (os.getenv(name) or "").strip() or default


def flag(name: str, default: bool = False) -> bool:
    """A yes/no setting. Anything that is not clearly yes or no keeps the default."""
    value = env(name).lower()
    if value in TRUE:
        return True
    if value in FALSE:
        return False
    return default


def num(name: str, default: int) -> int:
    try:
        return int(env(name) or default)
    except ValueError:
        return default


def csv_list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in env(name, default).split(",") if x.strip()]


def load_dotenv() -> None:
    """Load ROOT/.env without overriding variables already set (GitHub Actions)."""
    try:
        from dotenv import load_dotenv as _load
    except ImportError:
        return
    _load(ROOT / ".env", override=False)


def setup_logging() -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; keep emoji printable
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)], force=True)


def output_dir(bot: str) -> Path:
    """output/<bot>/, created on demand; uploaded as the run's artifact on GitHub."""
    path = OUTPUT / bot
    path.mkdir(parents=True, exist_ok=True)
    return path
