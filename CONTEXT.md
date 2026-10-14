# Project context — BINDAL EXPORTS report bots

Last updated: 2026-10-14. Owner: Bhikan (GitHub `bindalbhikan`, commit author
`bhikan.serein@gmail.com`). Every bot reports to Telegram **@bindal_exports_bot**.

This folder is the working copy of the private GitHub repo **`bindalbhikan/bindal-bots`**.
All four report bots and the Telegram command bot live here (until 2026-10-14 they were five
separate folders / repos: `daily-sale-report`, `myntra-vms-report`, `ajio-tat-bot`,
`ajio-spf-bot` and the worker; those repos are no longer run). `README.md` has the full detail.

## The bots

| Bot | Run | What it does | Daily (IST) | Result workbook (Drive, synced here) |
|---|---|---|---|---|
| Sale | `python run.py sale` | OMSGuru + Flipkart FBF + Myntra SJIT, Telegram summary | 3:30 PM | `BINDAL EXPORTS\DAILY SALE REPORT BINDAL\SALE REPORT.xlsx` |
| VMS | `python run.py vms` | Every Myntra order has a VMS packing video? | 5:30 PM | `BINDAL EXPORTS\VMS\MYNTRA ORDER ID WISE VMS.xlsx` |
| AJIO TAT | `python run.py ajio-tat` | AJIO dispatch TAT (the 10:10 rule) | 5:30 PM | `AJIO_TAT_MASTER.xlsx` |
| AJIO SPF | `python run.py ajio-spf` | SPF claims: raise tickets + Gemini-drafted follow-ups | 11:30 AM Wed + Sat | `BINDAL EXPORTS\SPF\AJIO\AJIO MASTER.xlsx` |

Each run downloads its workbook from Drive, updates it, and uploads it in place (same file id;
Drive keeps the old versions). It refuses to upload when someone changed the file meanwhile.

### VMS: ERP vs VMS

The VMS run also rebuilds, from the 1st of the month to today, two sheets in the same
workbook: `ERP VS VMS MON-YY` (per day and channel: parcels packed in the ERP, how many have
a VMS scan, %) and `ERP NO VMS` (the AWBs without a scan, with who packed them). AWBs come
from the ERP packing report (DigiBizz report 62, portal MYNTRA: its Order ID is the Myntra
tracking number), channels from OMSGuru. `/vms erp` (or VMS → 🏭 ERP vs VMS in the menu)
runs only this part. If the ERP part fails, the VMS report still goes out with a note.

### Sale: blocks

Since Oct 2026 the month sheets have a COCOBLU block right after AZ/FX/FBA (OMSGuru channel
"BINDAL EXPORTS LIMITED - COCOBLU", earlier counted inside Amazon) and no SNAPDEAL block
(Snapdeal orders are not reported). Older month sheets keep their old layout; a run that
writes into one puts Cocoblu back inside AZ/FX/FBA. New months copy the latest sheet.

## How it runs

- **GitHub Actions** in `bindal-bots`, one `workflow_dispatch` workflow per bot
  (`.github/workflows/sale.yml`, `vms.yml`, `ajio-tat.yml`, `ajio-spf.yml`) + `tests.yml` on push.
  GitHub's own `schedule:` is not used (it skipped runs).
- **Cloudflare Worker** `bindal-telegram-bot` (`worker/`) is the only scheduler: cron triggers in
  `worker/wrangler.toml` start the daily runs; Telegram commands start on-demand runs and the
  chat that asked gets the report. Commands: `/sale [today|from to]`, `/vms [from to]`,
  `/ajiotat [days]`, `/ajiospf [dry]`, `/salepic [YYYY-MM]`, `/vmspic [YYYY-MM]`, `/menu`
  (buttons), `/status`, `/help`. The "started" reply gives the expected run time, measured from
  the workflow's recent runs (usual: sale ~1 min, VMS / TAT ~2 min, SPF ~13 min). Only `ALLOWED_CHAT_IDS` + `EXTRA_CHAT_IDS` are obeyed.
- After changing `worker/`: `cd worker && npx wrangler deploy` (a change that is not deployed
  does not run — the Wed+Sat SPF schedule sat undeployed on 13-14 Oct 2026).

## Dashboard

`https://bindal-dashboard.bhikan-serein.workers.dev` (worker in `dashboard/`). Each bot run posts
its numbers and its result to the Telegram worker (`/api/push/<key>`, `DASHBOARD_TOKEN`), which
keeps them in Cloudflare KV `DASH`; the dashboard reads them. Sign-in: send `/dashboard` to the
bot (or tap 🖥 Dashboard) → a link valid 10 minutes, usable once → 30-day cookie on that device.
Links are signed with `SESSION_KEY` (same secret in both workers); changing it signs everyone
out. Cloudflare Access was not used: its Zero Trust sign-up asks for a card.

## Code layout

`bindal/core/` is shared by every bot: settings + logging (`config.py`), the run frame that
reports failures to Telegram (`app.py`), the one OMSGuru client, Drive, Telegram, sheet
pictures. `bindal/awb_tracker/` tracks couriers (BlueDart, Delhivery, Ekart, Shadowfax, Valmo,
XpressBees). One package per bot: `bindal/sale`, `vms`, `ajio_tat`, `ajio_spf`.
`python -m pytest` runs the tests.

## Settings and secrets — never in git

- One `.env` here for local runs (copy of `.env.example`), plus `service_account.json` (Google
  service account `ajio-tat-bot@storied-port-441507-k7.iam.gserviceaccount.com`, Editor on the
  workbooks). Both are `.gitignore`d, as are `output/` and every `.xlsx` / `.csv`.
- On GitHub: repository **Secrets** (logins, Drive ids, `GOOGLE_SERVICE_ACCOUNT_JSON`) and
  **Variables** (tuning). `SPF_DRY_RUN=0` = SPF posts to AJIO for real; `1` = only lists.
- Worker secrets (Cloudflare): `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN` (fine-grained, Actions
  read/write on `bindal-bots`), `WEBHOOK_SECRET`, `ALLOWED_CHAT_IDS`.
- A blank setting falls back to its default: an unset GitHub variable arrives as an empty string
  (on 2026-10-14 that emptied MYNTRA_CHANNELS and a VMS run wrote zeros).

## Rules the bots keep

- A blank value never erases an existing cell; one row per order/day; old data is kept.
- A run whose source returns no orders at all stops without touching the workbook.
- SPF: per-run caps; any failed source turns the run into a dry run; a failed "ticket already
  exists?" check stops that ticket instead of raising a duplicate.
- Myntra SJIT is not fetched automatically (the partner portal login has a reCAPTCHA; bypassing
  it is out of scope). The `Seller_Orders_Report` CSV is dropped by hand into the `sjit` Drive
  folder; missing days count as 0 and only fill empty cells.
- Courier tracking: a courier with no record of an AWB (not handed over yet, or older than the
  courier keeps — Delhivery forgets after ~2-3 months) is "not found", not an error; the SPF bot
  then uses AJIO's own 3PL status.
