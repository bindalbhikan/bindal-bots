# BINDAL EXPORTS report bots

One project for all the report workings and the Telegram command bot that starts them.
Every bot sends its report to Telegram **@bindal_exports_bot**.

| Bot | Command | What it does | Daily (IST) |
|---|---|---|---|
| Sale | `python run.py sale` | Fills `SALE REPORT.xlsx` (OMSGuru + Flipkart FBF + Myntra SJIT), Telegram summary | 3:30 PM (7:00 AM: SJIT report request) |
| VMS | `python run.py vms` | Checks every Myntra order has a VMS packing video, fills `MYNTRA ORDER ID WISE VMS.xlsx`; also ERP vs VMS (parcels packed in the ERP yesterday and today) | 5:30 PM |
| AJIO TAT | `python run.py ajio-tat` | AJIO dispatch TAT (the 10:10 rule), fills `AJIO_TAT_MASTER` | 5:30 PM |
| AJIO SPF | `python run.py ajio-spf` | AJIO SPF claims: raise tickets + Gemini-drafted follow-ups, rebuilds `AJIO MASTER.xlsx` | master 9:00 AM daily; tickets + replies only Wed + Sat |
| Returns | `python run.py returns` | Monthly sale vs return analysis (Myntra MAIN / DBCC / FMCC, Ajio, Flipkart) into `RETURN ANALYSIS/Output/<MON-YY>` on Drive and updates the yearly report `Output/<YYYY>`, Telegram summary | 2:00 PM on the 15th (7:00 AM: report request) |
| Myntra SPF | `python run.py myntra-spf` | Myntra MAIN / DBCC / FMCC returns with claim aging, tickets, payouts; return received checked in the ERP, then OMSGuru, then the manual sheet; `SPF/MYNTRA/MYNTRA SPF MASTER.xlsx` | 9:00 AM (7:00 AM: report request) |
| AJIO Ads gap | `python run.py ajio-ads` | Monthly G4 vs TOT marketing charge from the SRM statement, `AJIO/Ajio Ads Gap Working/output/AJIO_Simple_Monthly_G4_Charges.xlsx` | 9:00 AM on the 3rd (again on the 5th if missed) |

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

See `docs/ARCHITECTURE.md` (system, code layout, how to add a working), `docs/PORTALS.md`
(outside systems) and `docs/bots/` (one page per working).

## How it runs

- The bots run on **GitHub Actions** in the `bindal-bots` repo, `workflow_dispatch` only.
- The **Cloudflare Worker** (`worker/src/worker.js`) is the only scheduler: its cron triggers
  start the daily runs, Telegram commands start on-demand runs, and the chat that sent the
  command gets that run's report.
- Commands: `/sale [today|from to]`, `/vms [from to|erp]`, `/ajiotat [days]`, `/ajiospf [dry]`,
  `/myntraspf`, `/returns [YYYY-MM]`, `/ajioads`, `/salepic [YYYY-MM]`, `/vmspic [YYYY-MM]`, `/menu`,
  `/status`, `/help`, `/dashboard`.
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
- Myntra SJIT: the partner portal login has a reCAPTCHA, so the bot never logs in. It reuses a
  login made by hand in Chrome, one per seller account: MAIN (19170, SJIT), DBCC (32207),
  FMCC (35426). To give it a new one: logged in on the portal with that account, DevTools (F12)
  > Network > any `partnersapi.myntrainfo.com` request > Request Headers > copy the `cookie:`
  value, then run `python scripts/myntra/myntra_session.py MAIN` (or `DBCC` / `FMCC`; it reads the
  clipboard, or a file given after the account). It checks the login is that account and saves it
  to the Telegram worker (the copy the bots read); MAIN also to the GitHub secret
  `MYNTRA_SESSION_JSON`. The login cookies are httpOnly: browser JavaScript and page captures
  (netcap) cannot read them. Do not log out of the portal afterwards: that ends the bot's session
  too. The 7:00 AM and 3:30 PM runs use every saved session and save the renewed cookies, which
  keeps them alive.
  The sale run first uses `Seller_Orders_Report`s already on the portal (newest per day; also
  the last 30 days, for cancellations of older orders). Only days no report covers, or a report
  that does not download, get a new report, which the run waits for (~5-10 min). At 7:00 AM a
  short run (`python run.py sale --sjit-request`) asks the portal for the report days, so it is
  ready by 3:30 PM. After each run the cookies the portal renewed are saved back to the worker.
  Telegram alert when the session has expired or expires within 3 days (a pasted cookie line
  has no expiry date: taken as 15 days from the last save, which every run renews). A CSV dropped by hand into the `sjit` Drive
  folder is still read too.

## Local run

```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py vms --no-telegram
.venv\Scripts\python -m pytest
```

`--snapshot` also needs LibreOffice (`soffice`) and poppler (`pdftoppm`) on the PATH.
