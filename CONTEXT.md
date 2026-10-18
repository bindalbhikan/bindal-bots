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
| Sale | `python run.py sale` | OMSGuru + Flipkart FBF + Myntra SJIT, Telegram summary | 3:30 PM (+ 9:00 AM SJIT request) | `BINDAL EXPORTS\DAILY SALE REPORT BINDAL\SALE REPORT.xlsx` |
| VMS | `python run.py vms` | Every Myntra order has a VMS packing video? | 5:30 PM | `BINDAL EXPORTS\VMS\MYNTRA ORDER ID WISE VMS.xlsx` |
| AJIO TAT | `python run.py ajio-tat` | AJIO dispatch TAT (the 10:10 rule) | 5:30 PM | `AJIO_TAT_MASTER.xlsx` |
| AJIO SPF | `python run.py ajio-spf` | SPF claims: raise tickets + Gemini-drafted follow-ups | 11:30 AM Wed + Sat | `BINDAL EXPORTS\SPF\AJIO\AJIO MASTER.xlsx` |

Each run downloads its workbook from Drive, updates it, and uploads it in place (same file id;
Drive keeps the old versions). It refuses to upload when someone changed the file meanwhile.

### VMS: ERP vs VMS

The VMS run also checks the parcels packed in the ERP yesterday and today (only those two
days are fetched; their rows are replaced, earlier days stay) in two sheets of the same
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

### AJIO TAT master

One master file, `AJIO_TAT_MASTER.xlsx`, kept by name in the Drive folder
`BINDAL EXPORTS/AJIO/Ajio Daily Order Tat Check Working` (`TAT_DRIVE_FOLDER_ID`; wins over the old
`TAT_DRIVE_FILE_ID`, whose file was trashed on 2026-10-18 and now only seeds a missing master).
Every run updates each order's status, and the columns `Label Printed` / `ERP Label Time` /
`ERP Label By` from the ERP packing report (report 62, portal AJIO: its "Order ID" is the AWB);
the first print counts. The master holds orders from 2026-08-01.

### Drive: new files

A Google service account has no Drive storage: it can update files you made, never create one
("Service Accounts do not have storage quota"); folders it can create. New files (monthly and
yearly return analysis reports, yearly data CSVs, a missing TAT master) go through the Drive
relay `scripts/drive_relay.gs`, an Apps Script web app that runs as you (`DRIVE_RELAY_URL`,
`DRIVE_RELAY_TOKEN`); existing files are still updated directly by the service account.

### Myntra partner portal reports

Three seller accounts, each its own login: MAIN 19170, DBCC 32207, FMCC 35426. All use the same
report API (`bindal/sale/myntra_portal.py`) with `storeId 2297`, `partnerType PPMP`:

| Report | reportId | One job covers |
|---|---|---|
| `Seller_Orders_Report` | 4 | at most 1 month (also used for SJIT) |
| `Seller_Returns_Report` | 12 | at most 1 month (by return date) |

`POST /api/partners/report/jobs/schedule` → poll `GET /report/jobs/search` until `COMPLETED` →
download through `/api/file-manager/getDownloadUrl?url=<successFile>` (redirect to a signed link).
A job takes ~5-10 minutes. **After a report is requested, the portal refuses the same report
for 15 minutes** (`coolDownPeriod`): request different reports together, the same report again
only after 15 minutes, and reuse completed jobs whenever they cover the dates. `schedule()`
waits out one refusal (16 min); the exact refusal text is not known yet.

### Return analysis (`python run.py returns`)

Monthly sale vs return report for Myntra MAIN / DBCC / FMCC, Ajio and Flipkart. The rules are
those of the hand-run tool in `BINDAL EXPORTS\RETURN ANALYSIS` (`main.py` and its CONTEXT.md),
copied unchanged into `bindal/return_analysis/engine.py` (same SEP-26 output, checked file by
file). Worker cron on the 15th: 9:00 AM `--request` (asks Myntra for the orders report and the
first returns report per account, and Flipkart for its two reports), 2:00 PM the report: sale =
last month 1st to last day, returns = last month 1st to this month's 14th (two Myntra returns
jobs per account, the second after the 15-minute cooldown; existing jobs for the same dates are
reused). MasterSheet comes from Drive `RETURN ANALYSIS/Supporting` (folder id in
the `RETURN_DRIVE_FOLDER_ID` secret); reports go to `Output/<MON-YY>/`, CSVs to `Template/`
(replaced by name), Telegram gets the table and the ALL PORTAL workbook. A portal that cannot be
fetched is left out and named; `--drive-input` uses the Drive `Input/` folder instead,
`--no-upload` keeps everything in `output/returns`. `/returns [YYYY-MM]` runs it on demand.

Yearly report (January to December in one workbook per portal + MYNTRA COMBINED + ALL PORTAL):
each monthly run keeps every portal's built rows (template columns + STATE) in Drive
`RETURN ANALYSIS/Yearly/<YYYY>/<PORTAL> <MON-YY>.csv` and rebuilds `Output/<YYYY>/<PORTAL> SALE
RETURN ANALYSIS <YYYY>.xlsx` from all of them (`yearly.py`). The first build of a year is a
one-time backfill (`--backfill-year 2026`, workflow input `backfill_year`): every month whose
returns window is over is fetched and built (Myntra: one orders report per month and one returns
report per calendar month, orders and returns side by side, each 15 minutes after the last of
its kind; Ajio and Flipkart per month), a few hours. A backfilled month's Myntra return status is
as of the backfill day (the orders report carries it), not as of the next month's 14th. Where each input comes from (all found and checked 2026-10-18):

| Input | API |
|---|---|
| Ajio orders (`DropShipOrderReports`) | `POST /malekith/vision/v1/dropship/order/report/download?from=&to=` (same form and headers as the RTV download in `ajio_spf/sources/ajio.py`; max 31 days). Sep-26 download = the hand-downloaded file (788 rows, same columns) |
| Ajio returns (`DropShipRtvReports`) | `.../dropship/rtv/report/download` (already used by the SPF bot; max 31 days) |
| Flipkart sales (sheet "Sales Report") | Tax Reports → `Sales Report`: `POST /napi/metrics/bizReport/submitReport?reportName=Sales%20Report` |
| Flipkart returns (sheet "Returns") | Fulfilment Reports → `Returns`: `submitReport?reportName=Returns` |

Flipkart reports: body `{"from_date","to_date","file_format":"EXCEL","type":"ONETIME","filters":[{"values":[from,to],"operator":"RANGE","name":"date"}]}`
with the `fk-csrf-token` header; poll `POST /napi/metrics/bizReport/report/getReportsV2` until the
request's `status` is `FILE_STORED`; download
`GET /napi/metrics/bizReport/downloadReport/<request_id>_<created_at>.xlsx?token=<request_id>&sellerId=<seller id>`.
(The Fulfilment "Orders" report is not the one the tool reads.)

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

**The GitHub repo is public** (since 2026-10-18, for unlimited Actions minutes): code, run logs
and artifacts are readable by anyone. So every workflow runs its bot through
`scripts/ci_run.sh`: the public log shows only the exit code, the bot's log and `output/` are
uploaded only as `secure/output.tar.gz.enc` (AES, key = `ARTIFACT_KEY` secret, also in the local
`.env`). Read a run with `python scripts/run_output.py <run id>` (files in
`output/runs/<run id>/`). Ids that are not code stay in secrets (`RETURN_DRIVE_FOLDER_ID`, the
worker's `EXTRA_CHAT_IDS`). Never commit or print logins, cookies or customer data.

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
- Myntra SJIT: the partner portal login has a reCAPTCHA (never bypassed). The bot reuses the
  login made by hand in Chrome: the `cookie:` request header copied from DevTools, saved by
  `scripts/myntra_session.py MAIN|DBCC|FMCC` to the Telegram worker (KV `myntra_session`,
  `myntra_session_dbcc`, `myntra_session_fmcc`; MAIN also the secret `MYNTRA_SESSION_JSON`).
  Three separate seller logins: MAIN 19170 (SJIT), DBCC 32207, FMCC 35426; DBCC / FMCC are for
  the monthly return analysis and are kept alive by the 9 AM and 3:30 PM sale runs. Do not log out of the
  portal: that ends the bot's session (a Chrome extension was tried and removed: the login
  cookies are httpOnly, host-only on partnersapi). Reports already on the portal are
  used first; only uncovered days get a new `Seller_Orders_Report` (the run waits for it). A
  9:00 AM run (`sale --sjit-request`, worker cron) asks for the report days early. Renewed
  cookies are saved back to the worker after each run. Telegram alert when the session is
  expired or expires within 3 days. A CSV dropped into the `sjit` Drive folder still counts;
  days no report covers count as 0 and only fill empty cells.
- Courier tracking: a courier with no record of an AWB (not handed over yet, or older than the
  courier keeps — Delhivery forgets after ~2-3 months) is "not found", not an error; the SPF bot
  then uses AJIO's own 3PL status.
