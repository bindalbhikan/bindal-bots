# Telegram command bot

Cloudflare Worker that lets @bindal_exports_bot start the bots' GitHub Actions runs, and the
only scheduler of the daily runs (cron triggers in `wrangler.toml`, times in `SCHEDULE` in
`src/worker.js`). Every command starts a workflow of the `bindal-bots` repo.

| Command | Workflow |
|---|---|
| `/sale today` | `sale.yml` - today so far, channel-wise (workbook not touched) |
| `/sale [from] [to]` | `sale.yml` |
| `/vms [from] [to]` | `vms.yml` |
| `/ajiotat [days]` | `ajio-tat.yml` |
| `/ajiospf [dry]` | `ajio-spf.yml` (dry = only list what would be raised / replied) |
| `/salepic [YYYY-MM]` | `sale.yml` - picture of the month sheet |
| `/vmspic [YYYY-MM]` | `vms.yml` - picture of the month sheet |
| `/menu` | buttons for every report (AJIO SPF LIVE asks to confirm first) |
| `/status` | last run of each: result, how long it took, next auto run |
| `/help` | command list |

After a command the bot replies with the expected run time: the median of that workflow's
last runs on GitHub (queue included), plus the wait for a run already in progress.

Dates are `YYYY-MM-DD`. Only chats in `ALLOWED_CHAT_IDS` (secret) + `EXTRA_CHAT_IDS`
(`wrangler.toml`) are obeyed; the webhook also checks Telegram's secret header.

## Setup (once)

1. GitHub → Settings → Developer settings → Fine-grained tokens → **Generate new token**:
   repository `bindal-bots`, permission **Actions: Read and write**.
2. In PowerShell, from this folder: `powershell -ExecutionPolicy Bypass -File .\setup.ps1`
   and paste the token when asked. Bot token and chat id are read from `..\.env`.

After a code change: `npx wrangler deploy`. While the webhook is set, `getUpdates` does not
work for this bot (Telegram allows one or the other).
