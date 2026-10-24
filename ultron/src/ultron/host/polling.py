"""ultron serve: the agent on the seller's own machine (a PC, a server or VPS, a Mac, an Android
phone in Termux), with no GitHub Actions or Cloudflare worker in between.

It asks Telegram for new messages (long polling: nothing to open on the machine, it works behind
a home router), answers the owner's plain messages with agents/chat.py, and keeps the Approve /
Reject buttons itself (core/approvals.py, local backend). Only the owner's chats are answered:
ALLOWED_CHAT_IDS, else TELEGRAM_CHAT_ID.

A Telegram bot is either polled or has a webhook, not both: give ultron serve its own bot, or
remove the webhook of a bot a worker used to answer.

Fallback: run the main host as `ultron serve` and a second one (e.g. a Termux phone) as
`ultron serve --fallback`. Telegram gives the bot to one poller at a time; the fallback tries
every FALLBACK_WAIT seconds, the main host takes the bot straight back, so the fallback holds it
only while the main host is down. A message that arrives in the second of a hand-over may be
answered twice.
"""
from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import TITLE
from ..agents import chat
from ..core import approvals, config, memory, runtime
from ..core.config import env
from ..integrations import telegram

log = logging.getLogger("ultron")

MAIN_WAIT = 2          # seconds: the main instance takes the bot back at once
FALLBACK_WAIT = 120    # seconds: a fallback instance checks this often whether the main one stopped

HELP = (f"🤖 <b>{TITLE}</b>\n\nWrite to me in plain words, e.g.\n• how many orders yesterday?\n"
        "• where is order 12345?\n• which orders are due today?\n\nAnything I would run, I ask first.\n"
        "I remember what you teach me: /memory shows it, /forget &lt;id&gt; drops a note, "
        "/new starts a fresh conversation.")


def allowed() -> set[str]:
    ids = env("ALLOWED_CHAT_IDS") or env("TELEGRAM_CHAT_ID")
    return {c.strip() for c in ids.split(",") if c.strip()}


def on_message(msg: dict) -> None:
    chat_id, text = str(msg["chat"]["id"]), (msg.get("text") or "").strip()
    if not text:
        return
    cmd, _, rest = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd in ("/start", "/help"):
        telegram.send(HELP, chat_id)
        return
    if cmd in ("/memory", "/forget", "/new"):
        telegram.send(memory_command(cmd, rest.strip(), chat_id), chat_id)
        return
    if text.startswith("/"):
        return
    telegram.call("sendChatAction", chat_id=chat_id, action="typing")
    try:
        reply = chat.answer(text, chat_id)
    except Exception as exc:  # noqa: BLE001 - one bad message must not stop the agent
        log.exception("Answer failed")
        reply = f"❌ <b>The agent failed</b>\n<code>{telegram.esc(str(exc)[:300])}</code>"
    telegram.send(reply, chat_id)


def memory_command(cmd: str, arg: str, chat_id: str) -> str:
    """/memory [words]: the notes (or those matching); /forget <id>; /new: a fresh conversation."""
    if config.get().memory.backend == "off":
        return "🧠 Memory is off ([memory] backend in agent.toml)."
    mem = memory.get()
    if cmd == "/new":
        mem.clear_chat(chat_id)
        return "🧠 Started a fresh conversation (the long-term notes stay)."
    if cmd == "/forget":
        if not arg:
            return "Use /forget <id>, with the id from /memory."
        return f"🧠 Forgot {telegram.esc(arg)}." if mem.forget(arg) else f"No note {telegram.esc(arg)}."
    notes = mem.search(arg, k=30) if arg else mem.read()["notes"][-30:]
    if not notes:
        return "🧠 Nothing in memory yet." if not arg else f"🧠 Nothing about {telegram.esc(arg)}."
    lines = [f"<code>{n['id']}</code> <i>{n['kind']}</i> {telegram.esc(n['text'])}" for n in notes]
    return "🧠 <b>Memory</b>" + (f" · {telegram.esc(arg)}" if arg else f" · last {len(notes)}") + "\n\n" + "\n".join(lines) \
        + "\n\n<i>/forget &lt;id&gt; drops one note.</i>"


def on_tap(cb: dict) -> None:
    data, msg = str(cb.get("data") or ""), cb["message"]
    if not data.startswith(("ap:", "rj:")):
        telegram.call("answerCallbackQuery", callback_query_id=cb["id"])
        return
    result = approvals.decide(data[3:], data.startswith("ap:"), str(msg["chat"]["id"]))
    telegram.call("answerCallbackQuery", callback_query_id=cb["id"], text=result[:190])
    who = telegram.esc((cb.get("from") or {}).get("first_name") or "someone")
    telegram.call("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"], parse_mode="HTML",
                  text=f"{telegram.esc(msg.get('text') or '')}\n\n{telegram.esc(result)} ({who})"[:4000])


def serve(args) -> int:
    cfg = config.get()
    if not env("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    owners = allowed()
    if not owners:
        raise RuntimeError("no owner chat: set ALLOWED_CHAT_IDS or TELEGRAM_CHAT_ID")
    approvals.use_local()
    me = telegram.call("getMe") or {}
    log.info("Ultron for %s on @%s: answering %d chat(s); Ctrl+C to stop", cfg.company, me.get("username"), len(owners))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):      # not every platform has every signal
            pass
    offset, pool = 0, ThreadPoolExecutor(max_workers=args.workers)
    while not stop.is_set():
        try:
            batch = telegram.updates(offset, timeout=25)
        except telegram.WebhookSet as exc:
            log.error("%s", exc)
            return 1
        except telegram.OtherInstance:
            # Telegram lets one poller have the bot. The main host takes it straight back; a
            # --fallback host (e.g. a Termux phone) stands by and holds the bot only once the main
            # host has stopped polling (it no longer takes the bot back).
            wait = FALLBACK_WAIT if args.fallback else MAIN_WAIT
            if args.fallback:
                log.info("The main instance is answering; standing by %ss", wait)
            stop.wait(wait)
            continue
        except RuntimeError as exc:
            log.error("%s", exc)
            stop.wait(30)
            continue
        for u in batch:
            offset = u["update_id"] + 1
            msg, cb = u.get("message"), u.get("callback_query")
            if msg and str(msg["chat"]["id"]) in owners:
                pool.submit(on_message, msg)
            elif cb and cb.get("message") and str(cb["message"]["chat"]["id"]) in owners:
                pool.submit(on_tap, cb)
            elif msg or cb:
                log.info("Ignored an update from chat %s (not an owner chat)", ((msg or cb.get("message") or {}).get("chat") or {}).get("id"))
        if not batch:
            time.sleep(0.2)
    log.info("Stopping: finishing the answers in progress")
    pool.shutdown(wait=True)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ultron serve", description="Run the agent on this machine (Telegram long polling)")
    p.add_argument("--workers", type=int, default=2, help="messages answered at the same time (default 2)")
    p.add_argument("--fallback", action="store_true",
                   help="stand by while another (main) instance answers; take over when it stops")
    return runtime.run(TITLE, p, serve, argv)
