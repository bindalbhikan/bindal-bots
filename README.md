# BINDAL EXPORTS report bots

One project for all four report bots and the Telegram command bot that starts them.
Every bot sends its report to Telegram **@bindal_exports_bot**.

| Bot | Command | What it does | Daily (IST) |
|---|---|---|---|
| Sale | `python run.py sale` | Fills `SALE REPORT.xlsx` (OMSGuru + Flipkart FBF + Myntra SJIT), Telegram summary | 3:30 PM |
| VMS | `python run.py vms` | Checks every Myntra order has a VMS packing video, fills `MYNTRA ORDER ID WISE VMS.xlsx` | 5:30 PM |
| AJIO TAT | `python run.py ajio-tat` | AJIO dispatch TAT (the 10:10 rule), fills `AJIO_TAT_MASTER` | 5:30 PM |
| AJIO SPF | `python run.py ajio-spf` | AJIO SPF claims: raise tickets + Gemini-drafted follow-ups, rebuilds `AJIO MASTER.xlsx` | 11:30 AM Wed + Sat |

`python run.py <bot> --help` lists each bot's options (back-fill dates, `--no-telegram`,
`--no-drive`, `--snapshot`, ...).

## Where the results are

Each bot updates its workbook on Google Drive in place (same file, same link; Drive keeps the
old versions under "Manage versions"), and Drive for desktop syncs it into the project folder:

| Bot | Result workbook |
|---|---|
| Sale | `G:\My Drive\BINDAL EXPORTS\DAILY SALE REPORT BINDAL\SALE REPORT.xlsx` (`SALE_DRIVE_FILE_ID`) |
| VMS | `G:\My Drive\BINDAL EXPORTS\VMS\MYNTRA ORDER ID WISE VMS.xlsx` (`VMS_DRIVE_FILE_ID`) |
| AJIO SPF | `G:\My Drive\BINDAL EXPORTS\SPF\AJIO\AJIO MASTER.xlsx` (`SPF_MASTER_DRIVE_FILE_ID`) |
| AJIO TAT | `AJIO_TAT_MASTER.xlsx` on Drive (`TAT_DRIVE_FILE_ID`) |

Close a workbook in Excel before a run, otherwise Drive may hold two versions. Run logs and
error lists go to `output/<bot>/` (on GitHub: the run's artifact).

## Layout

```
run.py                     entry point: python run.py <bot> [options]
bindal/
  core/                    shared by every bot
    config.py              settings (.env / GitHub Secrets), logging, output folders
    app.py                 run frame: a failed run always reaches Telegram
    omsguru.py             the one OMSGuru client (login, warehouses, lists, order pages, search)
    drive.py  gauth.py     Google Drive workbooks (download, safe in-place upload)
    telegram.py            messages (split without breaking HTML), photos, files
    snapshot.py files.py   sheet pictures (LibreOffice), atomic workbook save
  awb_tracker/             courier tracking (BlueDart, Delhivery, Ekart, Shadowfax, Valmo, XpressBees)
  sale/  vms/  ajio_tat/  ajio_spf/    one package per bot
worker/                    Cloudflare Worker: Telegram commands + the daily cron + dashboard data push
dashboard/                 Cloudflare Worker: the dashboard page (sign-in through /dashboard in Telegram)
.github/workflows/         one workflow per bot (workflow_dispatch) + tests
tests/                     python -m pytest
scripts/                   local runs
```

## How it runs

- The bots run on **GitHub Actions** in the `bindal-bots` repo, `workflow_dispatch` only.
- The **Cloudflare Worker** (`worker/src/worker.js`) is the only scheduler: its cron triggers
  start the daily runs, Telegram commands start on-demand runs, and the chat that sent the
  command gets that run's report.
- Commands: `/sale [today|from to]`, `/vms [from to]`, `/ajiotat [days]`, `/ajiospf [dry]`,
  `/salepic [YYYY-MM]`, `/vmspic [YYYY-MM]`, `/menu`, `/status`, `/help`.
- Workbooks live on **Google Drive** (service account
  `ajio-tat-bot@storied-port-441507-k7.iam.gserviceaccount.com`, Editor). A run downloads the
  file, works on it, and uploads it in place only if nobody changed it on Drive meanwhile.

## Settings

One `.env` for everything (copy `.env.example`). On GitHub the same names are repository **Secrets** (logins, ids) and
**Variables** (tuning). Names that two bots used to share with different meanings now carry a
prefix:

| Old (per bot) | New |
|---|---|
| `MASTER_DRIVE_FILE_ID` (TAT) | `TAT_DRIVE_FILE_ID` |
| `GOOGLE_SHEET_ID`, `MASTER_XLSX_PATH`, `LOOKBACK_DAYS` (TAT) | `TAT_GOOGLE_SHEET_ID`, `TAT_XLSX_PATH`, `TAT_LOOKBACK_DAYS` |
| `MASTER_DRIVE_FILE_ID`, `MANUAL_DRIVE_FILE_ID`, `OMS_DRIVE_FILE_ID` (SPF) | `SPF_MASTER_DRIVE_FILE_ID`, `SPF_MANUAL_DRIVE_FILE_ID`, `SPF_OMS_DRIVE_FILE_ID` |
| `DRY_RUN` (SPF) | `SPF_DRY_RUN` |
| `REPORT_DAYS` (sale / VMS) | `SALE_REPORT_DAYS` / `VMS_REPORT_DAYS` |

Secrets never go in git: `.env`, `service_account.json` and all workbooks are ignored.

## Safety rules

- A blank value never erases an existing cell; one row per order/day; old data is kept.
- SPF: nothing is posted to AJIO unless `SPF_DRY_RUN` is explicitly `0`/`false`/`no`/`off`;
  per-run caps; any failed source turns the run into a dry run; a failed "does this return
  already have a ticket?" check stops that ticket instead of raising a duplicate.
- Myntra SJIT is not fetched automatically (the partner portal login has a reCAPTCHA): the
  `Seller_Orders_Report` CSV is dropped by hand into the `sjit` Drive folder.

## Local run

```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python run.py vms --no-telegram
.venv\Scripts\python -m pytest
```

`--snapshot` also needs LibreOffice (`soffice`) and poppler (`pdftoppm`) on the PATH.
