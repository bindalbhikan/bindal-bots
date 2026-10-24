"""The `ultron` command (also `python -m ultron`).

  ultron serve                 run the agent on this machine: PC, server / VPS, Mac, Android (Termux)
  ultron watch --run-id <id>   check a finished bot run, tell Telegram only when something is wrong
  ultron chat --text "<msg>"   answer the owner's Telegram message
  ultron check                 read agent.toml and the tools, print what was found
  ultron version
"""
from __future__ import annotations

import platform
import sys
from importlib import import_module

from . import __version__

COMMANDS = {"serve": "ultron.host.polling", "watch": "ultron.agents.watch", "chat": "ultron.agents.chat"}


def check() -> int:
    from .agents.chat import tools
    from .core import config
    cfg = config.get()
    print(f"config:  {config.find()}")
    print(f"company: {cfg.company}")
    print(f"repo:    {cfg.repo or '(not set)'}")
    print(f"model:   {cfg.model_name()} (worker: {cfg.worker_url_env} / {cfg.worker_token_env})")
    print(f"actions: {', '.join(cfg.actions) or '(none)'}")
    print(f"tools:   {', '.join(t.__name__ for t in tools())}")
    print(f"python:  {platform.python_version()} on {platform.system()} {platform.machine()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    if cmd in COMMANDS:
        return import_module(COMMANDS[cmd]).main(argv[1:])
    if cmd == "check":
        return check()
    if cmd in ("version", "--version"):
        print(f"ultron {__version__}")
        return 0
    print(__doc__)
    return 0 if cmd in ("-h", "--help") else 2
