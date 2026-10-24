# Ultron · Ecom Manager

An AI e-commerce manager for marketplace sellers. It watches the seller's report bots (GitHub Actions
workflows) and tells the owner on Telegram only when a run went wrong, with the cause and a next
step. It also answers the owner's plain-text messages: it looks orders up, counts sales, lists
orders due, and proposes actions the owner approves with a button.

The code is the same for every seller. Each seller brings:

* **`agent.toml`**: company name and description, GitHub repo, model, channel names, which tools
  are on, and which actions may be proposed. No secrets.
* **Secrets**: their own logins (OMSGuru, Telegram, GitHub) in GitHub secrets.
* **A Cloudflare worker**: serves the model (Workers AI, free daily allocation) and the
  Approve / Reject buttons.

## Layout

```
ultron/
  pyproject.toml              the package: pip install ./ultron  ->  the `ultron` command
  src/ultron/
    cli.py                    ultron serve | watch | chat | check | version
    core/
      config.py               agent.toml (AGENT_CONFIG, else the nearest agent.toml from the current folder up)
      llm.py                  tool schemas from docstrings, the tool-calling loop, any chat-completions endpoint
      runtime.py              the command frame: .env, logging, a Telegram alert when the agent fails
      approvals.py            Approve / Reject: kept by the worker, or locally by ultron serve
      memory.py               long-term notes + each chat's last messages (worker KV or a local file)
    agents/
      watch.py                checks one finished bot run; silent when it is fine
      chat.py                 answers one owner message; propose_action for anything to be done
    host/
      polling.py              ultron serve: the agent on the seller's own machine (Telegram long polling)
    tools/                    built-in tool sets (core/config.py BUILTIN_TOOLS)
      runs.py                 "runs": recent runs, decrypted run log, run files, xlsx summary
      omsguru.py              "omsguru": find_order, sale_by_channel, due_today
      memory.py               "memory": recall, remember, forget (on whenever the memory is)
    portals/                  portal clients shared by every seller (OMSGuru), the seller's own login
    integrations/             github.py (runs API), telegram.py (messages), crypto.py (run artifacts)
  worker/
    agent.js                  worker routes: /api/ai, /api/approval, plain text -> agent, Approve / Reject
    index.js                  a standalone worker for a seller with no Telegram bot yet
    wrangler.example.toml
  main.py                     python main.py <command>: the same commands without installing
  deploy/                     linux/ macos/ windows/ termux/ docker/: run ultron serve as a service
  templates/                  agent-watch.yml, agent-chat.yml, ci_run.sh to copy into the seller's repo
  examples/agent.example.toml every agent.toml key, commented
  tests/                      python -m pytest (runs as the example seller)
```

`ultron/` must never name a particular seller; the host repo's tests check it.

## Setting up a new seller

1. **Repo.** The seller's bots repo gets a copy of `ultron/` and, at its root, `agent.toml`
   from `examples/agent.example.toml`, filled in. Install with `pip install -e ./ultron` (or
   `-e ./ultron` in the repo's requirements.txt) and check the setup with `ultron check`.
2. **Workflows.** Copy `templates/agent-watch.yml` and `templates/agent-chat.yml` into
   `.github/workflows/`. In `agent-watch.yml`, list the seller's bot workflows by name. The bots
   should keep their log in `output/run.log` and upload `output/` encrypted the way
   `templates/ci_run.sh` does, so the watcher can read it.
3. **Worker.** In `ultron/worker/`: copy `wrangler.example.toml` to `wrangler.toml`, fill in the
   repo and `[vars.AGENT_ACTIONS]` (the same workflows and inputs as `agent.toml [actions]`), create
   the KV namespace, put the secrets, `npx wrangler deploy`, then point the Telegram bot's webhook
   at the worker with the `WEBHOOK_SECRET`. A seller who already has a worker imports
   `makeAgent` from `agent.js` instead and calls `route`, `ask`, `isTap` and `decide` from it.
4. **GitHub secrets.** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ARTIFACT_KEY` (any long random
   string), `AGENT_WORKER_URL` (the worker's address), `AGENT_WORKER_TOKEN` (the worker's
   `AGENT_TOKEN`), and for the `omsguru` tools `OMSGURU_EMAIL`, `OMSGURU_PASSWORD`, `OMSGURU_OTP`.
   If `agent.toml [worker]` names other env vars, use those names.
5. **Try it.** Send the bot a plain message ("how many orders yesterday?"), and run Agent watch
   by hand on a failed run id.

## Hosting on the seller's own machine

Instead of GitHub Actions + a Cloudflare worker, the agent can run as one long-lived process on
any machine: `ultron serve` (or `python ultron/main.py serve` without installing). It polls
Telegram itself (nothing to open on the machine, works behind a home router), answers the owner,
and keeps the Approve / Reject buttons itself; an approved action runs its `command` on that
machine, or starts its GitHub workflow (`GH_TOKEN` with Actions: write).

| System | Setup (run in the folder that holds `agent.toml` and `.env`) | Runs as |
|---|---|---|
| Linux PC, server, VPS | `sh ultron/deploy/linux/install.sh` | systemd user service |
| macOS | `sh ultron/deploy/macos/install.sh` | launchd agent |
| Windows | `powershell -ExecutionPolicy Bypass -File ultron\deploy\windows\install.ps1` | scheduled task |
| Android (Termux) | `sh ultron/deploy/termux/setup.sh` | termux-services (+ Termux:Boot) |
| Any, with Docker | `ultron/deploy/docker/Dockerfile` | container |

Each restarts the agent when it stops and starts it with the machine. `.env` holds
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (the owner's chats; or `ALLOWED_CHAT_IDS`), the model
(`AGENT_LLM_URL` + `AGENT_LLM_KEY` for any OpenAI-compatible endpoint, or the worker's URL and
token), and the portal logins of the tools that are on.

* A Telegram bot is polled or has a webhook, not both: `ultron serve` needs a bot no worker
  answers (or the webhook removed).
* **Fallback host.** Run the main host as `ultron serve` and a second one (a phone in Termux:
  `setup.sh --fallback`) as `ultron serve --fallback`. Telegram gives the bot to one of them at
  a time: the main host takes it straight back, so the fallback answers only while the main host
  is down, and checks every 2 minutes. A message that arrives in the second of a hand-over may be
  answered twice.
* **Android.** The agent is pure Python, so Termux runs it as fast as a PC: the model works in
  the cloud and the phone only waits on the network. `pandas` (xlsx summaries of run files) and
  `cryptography` (encrypted GitHub run artifacts) are optional and come from Termux packages. Keep
  the phone on a charger and let Termux run in the background (Android battery settings), or
  Android stops it.

## Memory

The agent remembers between messages and runs (`core/memory.py`, `[memory]` in agent.toml):

* **Long-term notes**: preferences ("reports in units"), facts ("PPMP is the second Myntra
  account"), known issues ("a cooldown in Myntra SPF is harmless"), decisions, and incidents the
  watcher alerted on. Every message gets the owner's preferences and the best-matching notes in
  front of the model; the model also has `recall`, `remember` and `forget` tools and notes what
  the owner teaches it by itself.
* **Conversations**: each chat's last messages (10, at most 3 days old), so follow-ups work.
* **The watcher** reads the notes about a bot before judging its run (a known harmless issue is
  OK; an incident seen before is called recurring) and notes every alert it sends.
* **Where**: the worker's KV (`/api/memory`, versioned so two runs never overwrite each other),
  shared by GitHub Actions runs and every host, a fallback phone included; or
  `output/agent/memory.json` on a machine with no worker. Never in git.
* **Never kept**: text that looks like a password, OTP, token or key, or a phone number or email
  is refused before it is stored.
* **Owner commands** (ultron serve): `/memory [words]` lists notes, `/forget <id>` drops one,
  `/new` starts a fresh conversation. With the worker, ask in plain words ("what do you
  remember about SPF?", "forget a1b2c3").

## Rules the agent keeps

* Tools only read. Anything to be done goes through `propose_action`: the owner gets Approve /
  Reject buttons, and only Approve starts it. The workflow and its inputs must be in
  `agent.toml [actions]` and in the worker's `AGENT_ACTIONS`, so the model cannot start anything
  else. An approval expires after 24 hours and works once.
* Text from portals, logs and files is data, never instructions.
* Messages go only to the seller's own allowed chats.

## Adding tools

A seller-specific tool is a module listed in `agent.toml [chat] tools` (or `[watch] tools`),
exporting `TOOLS`:

```python
from ultron.core.llm import tool

@tool
def stock_of(sku: str) -> str:
    """Stock of one SKU in every warehouse.

    Args:
        sku: the SKU code.
    """
    ...

TOOLS = [stock_of]
```

A tool every seller can use belongs in `src/ultron/tools/` with an entry in
`core/config.py` `BUILTIN_TOOLS`. Tools must only read.

## Model

The default is Cloudflare Workers AI `@cf/openai/gpt-oss-120b` through the worker's `/api/ai`, on
the account's free daily allocation. The `AGENT_MODEL` repo variable picks another allowed model.
`AGENT_LLM_URL` + `AGENT_LLM_KEY` point the agent at any OpenAI-compatible
`/chat/completions` endpoint instead of the worker.
