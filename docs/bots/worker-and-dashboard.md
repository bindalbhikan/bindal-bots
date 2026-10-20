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
Sections per bot from the pushed numbers (see `dashboard/src/page.html`); the data keys it reads
are `KEYS` in `dashboard/src/index.js` and must also be in the worker's `DASH_KEYS`.

| Tab | Key(s) | Shows |
|---|---|---|
| Sale | `sale`, `sale_today` | daily gross chart, channels vs last month, today so far |
| VMS | `vms`, `vms_erp` | VMS % by SLA date, NO VMS list, ERP vs VMS by packer |
| TAT | `ajio_tat` | TAT result per day, couriers, pickup pending list |
| AJIO SPF | `ajio_spf` | open returns by age, run summary, courier pickup, tickets |
| Myntra SPF | `myntra_spf` | per account / total: final status, received source (ERP / OMSGuru / manual), claim status + value (and how many values are known), open by age, returns per month |
| Returns | `returns`, `returns_year` | return % per portal (month / year), % by month, top reasons, brands, categories, states (`bindal/return_analysis/dash.py`) |
| Ads | `ajio_ads` | G4 per month, charge, charge / G4 %, pending months |

KPIs: sale, VMS %, AJIO pickup pending, AJIO open returns, Myntra claims eligible, last month's
return %. The run strip shows every bot's last run (`run_<key>`, pushed by `core/app.py`).
* `POST /api/ai` (`DASHBOARD_TOKEN`): one Workers AI chat-completions call for the ops agent
  (`bindal/agent/llm.py`), only the models in `AI_MODELS`, on the free daily allocation (`[ai]` binding).
