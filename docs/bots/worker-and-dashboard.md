# Telegram worker and dashboard

**Worker** `bindal-telegram-bot` (`worker/`, Cloudflare): the Telegram webhook (commands + menu
buttons; only `ALLOWED_CHAT_IDS` + `EXTRA_CHAT_IDS` are obeyed), the scheduler (5 cron triggers to
GitHub `workflow_dispatch`, `SCHEDULE` with `day` / `weekdays` filters), `/api/push/<key>` for the
dashboard numbers (`DASHBOARD_TOKEN`, keys listed in `DASH_KEYS`), and `/api/myntra-session[/read]`
for the saved Myntra sessions (`?account=main|dbcc|fmcc`). Deploy after every change:
`cd worker && npx wrangler deploy`. Secrets: `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, `WEBHOOK_SECRET`,
`ALLOWED_CHAT_IDS`, `EXTRA_CHAT_IDS`, `DASHBOARD_TOKEN`, `SESSION_KEY`.

**Dashboard** `bindal-dashboard` (`dashboard/`): reads KV `DASH`; sign-in through `/dashboard` in
Telegram (one-time link valid 10 minutes, then a 30-day cookie, signed with `SESSION_KEY`).
Sections per bot from the pushed numbers (see `dashboard/src/page.html`).
