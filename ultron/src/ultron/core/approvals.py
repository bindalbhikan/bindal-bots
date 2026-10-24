"""Approvals: the agent proposes an action, the owner taps Approve on Telegram, only then it runs.

Two backends:
  worker  (default) the seller's Cloudflare worker keeps the pending approval and, on Approve,
          starts the action's GitHub workflow (ultron/worker/agent.js).
  local   ultron serve on the seller's own machine: pending approvals are kept in
          output/agent/approvals.json and, on Approve, the action runs here: its command, else its
          GitHub workflow (needs GH_TOKEN with Actions: write).

Either way an approval is for one action with the inputs shown, works once and expires after
HOURS. The caller (agents/chat.py) has already checked the action and its inputs against
agent.toml; decide() checks them again against the config of the moment it runs.
"""
from __future__ import annotations

import json
import logging
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from . import config
from ..integrations import github, telegram

log = logging.getLogger("ultron")

HOURS = 24
NOT_RUN = "Sent to the owner with Approve / Reject buttons. It has NOT run yet: say it waits for approval."
_mode = "worker"
_lock = threading.Lock()


def use_local() -> None:
    global _mode
    _mode = "local"


def propose(action: str, inputs: dict, summary: str, chat: str) -> str:
    """Ask the owner; the text the model reads back."""
    return _propose_local(action, inputs, summary, chat) if _mode == "local" else _propose_worker(action, inputs, summary, chat)


def _propose_worker(action: str, inputs: dict, summary: str, chat: str) -> str:
    url, token = config.get().worker()
    r = requests.post(f"{url}/api/approval", timeout=60, headers={"Authorization": f"Bearer {token}"},
                      json={"action": action, "inputs": inputs, "summary": summary, "chat_id": chat})
    if r.status_code != 200:
        return f"Error: approval not sent: {r.text[:200]}"
    return NOT_RUN


# ------------------------------------------------------------------ local (ultron serve)
def _store() -> Path:
    return config.get().output / "agent" / "approvals.json"


def _load() -> dict:
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if v.get("expires", 0) > now}


def _save(data: dict) -> None:
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)                 # atomic on Linux, macOS, Windows and Android alike


def describe(action: str, inputs: dict) -> str:
    a = config.get().actions[action]
    what = f"command: {' '.join(a.command)}" if a.command else f"workflow: {a.workflow}"
    args = "\n".join(f"{k} = {v}" for k, v in inputs.items()) or "(no inputs)"
    return f"<b>Run</b> {telegram.esc(what)}\n<code>{telegram.esc(args)}</code>"


def _propose_local(action: str, inputs: dict, summary: str, chat: str) -> str:
    aid = secrets.token_hex(6)
    with _lock:
        data = _load()
        data[aid] = {"action": action, "inputs": inputs, "chat": str(chat), "expires": time.time() + HOURS * 3600}
        _save(data)
    sent = telegram.send_buttons(chat, f"🙋 <b>Approve this?</b>\n\n{telegram.esc(summary[:600])}\n\n"
                                       f"{describe(action, inputs)}\n\n<i>Expires in {HOURS} hours.</i>",
                                 [[("✅ Approve", f"ap:{aid}"), ("✖️ Reject", f"rj:{aid}")]])
    return NOT_RUN if sent else "Error: approval not sent (Telegram refused the message)"


def decide(aid: str, approve: bool, chat: str) -> str:
    """Approve or reject one pending approval (a button tap in `chat`); what to tell the owner."""
    with _lock:
        data = _load()
        item = data.pop(aid, None)
        if item is not None:
            _save(data)                   # one tap decides it, a second does nothing
    if item is None or item["chat"] != str(chat):
        return "⌛ Expired or already decided."
    if not approve:
        return "✖️ Rejected."
    cfg = config.get()
    action = cfg.actions.get(item["action"])
    if action is None or any(k not in action.inputs for k in item["inputs"]):
        return f"❌ {item['action']} with these inputs is no longer allowed in agent.toml."
    try:
        return "✅ Approved: " + start(item["action"], item["inputs"])
    except Exception as exc:  # noqa: BLE001 - the owner sees why it did not start
        log.error("Action %s did not start: %s", item["action"], exc)
        return f"❌ Approved, but it did not start: {exc}"


def command_line(action: config.Action, inputs: dict) -> list[str]:
    """The action's command with each input as --<input> <value>, one argument each (no shell,
    so a value can never become a second command). "python" is this Python."""
    argv = [sys.executable if c in ("python", "python3") else c for c in action.command]
    for k, v in inputs.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return argv


def start(name: str, inputs: dict) -> str:
    cfg = config.get()
    action = cfg.actions[name]
    if action.command:
        logs = cfg.output / "agent" / "actions"
        logs.mkdir(parents=True, exist_ok=True)
        log_file = logs / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        with open(log_file, "wb") as out:
            proc = subprocess.Popen(command_line(action, inputs), cwd=cfg.root, stdout=out, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
        return f"{name} started here (process {proc.pid}), log {log_file.relative_to(cfg.root).as_posix()}"
    github.dispatch(action.workflow, inputs, cfg.ref)
    return f"{action.workflow} started on GitHub Actions."
