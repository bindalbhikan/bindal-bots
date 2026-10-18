# Project context — BINDAL EXPORTS report bots

Last updated: 2026-10-18. Owner: Bhikan (GitHub `bindalbhikan`, commit author
`bhikan.serein@gmail.com`). Every bot reports to Telegram **@bindal_exports_bot**.

This folder is the working copy of the GitHub repo **`bindalbhikan/bindal-bots`** (public since
2026-10-18). Read next: **`docs/ARCHITECTURE.md`** (how it fits together + the checklist for a new
working), **`docs/PORTALS.md`** (every outside system, its API and limits), **`docs/bots/<bot>.md`**
(one page per working). `README.md` is the short user guide.

## The workings

| Working | Run | What it does | When (IST) | Page |
|---|---|---|---|---|
| Sale | `python run.py sale` | OMSGuru + Flipkart FBF + Myntra SJIT into `SALE REPORT.xlsx` | 3:30 PM (7 AM SJIT request) | `docs/bots/sale.md` |
| VMS | `python run.py vms` | Myntra packing-video check + ERP vs VMS | 5:30 PM | `docs/bots/vms.md` |
| AJIO TAT | `python run.py ajio-tat` | 10:10 dispatch rule + ERP label print, `AJIO_TAT_MASTER.xlsx` | 5:30 PM | `docs/bots/ajio-tat.md` |
| AJIO SPF | `python run.py ajio-spf` | AJIO returns master; SPF tickets + replies | 9 AM master; tickets Wed + Sat | `docs/bots/ajio-spf.md` |
| Myntra SPF | `python run.py myntra-spf` | Myntra MAIN / DBCC / FMCC returns master, claim aging | 7 AM request, 9 AM master | `docs/bots/myntra-spf.md` |
| Return analysis | `python run.py returns` | Monthly + yearly sale vs return, 5 portals | 15th: 7 AM request, 2 PM report | `docs/bots/returns.md` |
| AJIO ads gap | `python run.py ajio-ads` | G4 vs TOT marketing charge (SRM statement) | 3rd 9 AM (5th if missed) | `docs/bots/ajio-ads.md` |
| Worker + dashboard | `worker/`, `dashboard/` | Telegram commands, scheduler, dashboard | — | `docs/bots/worker-and-dashboard.md` |

## Rules every working keeps

- **Data must never be wrong.** A source that fails, or a month whose source data is incomplete
  (e.g. Flipkart returns older than ~4 months), is left out and named — never shown as zero.
- A blank value never erases an existing cell; one row per order / day; old data is kept. A run
  whose required source returns nothing at all leaves the workbook untouched.
- Inputs come from the portals; a report already generated for the same dates is reused; an old
  one that will not download is asked for again, waited for, and the run carries on.
- Return received (Myntra and AJIO SPF): ERP GRN first, then OMSGuru (only rows whose final status
  is blank), then the manual sheet.
- A hand-run tool brought into the bot stays unchanged (`engine.py`); its port is checked file by
  file against the tool's own output.
- SPF: per-run caps; any failed source turns the run into a dry run; a failed "ticket already
  exists?" check skips that ticket. AJIO tickets / replies only Wed + Sat.
- Myntra: never bypass its reCAPTCHA; never log out of the portal (it ends the bot's session).

## Settings and secrets — never in git

- **Public repo**: every workflow runs its bot through `scripts/github/ci_run.sh` (public log = exit code
  only; log + `output/` uploaded AES-encrypted with `ARTIFACT_KEY`; read with
  `python scripts/github/run_output.py <run id>`). Never commit or print logins, cookies or customer data;
  folder / file ids and chat ids live in secrets.
- Local: one `.env` (copy of `.env.example`) + `service_account.json` (Google service account
  `ajio-tat-bot@storied-port-441507-k7.iam.gserviceaccount.com`, Editor on the folders). Ignored by
  git, as are `output/`, workbooks, CSVs, captures, `myntra_session*.json`, `scripts/*.txt`.
- GitHub: repository **Secrets** (logins, Drive ids, `GOOGLE_SERVICE_ACCOUNT_JSON`,
  `DRIVE_RELAY_URL/TOKEN`, `ARTIFACT_KEY`) and **Variables** (tuning). `SPF_DRY_RUN=0` = AJIO SPF
  posts for real. A blank GitHub variable arrives as an empty string and falls back to the default.
- Cloudflare worker secrets: see `docs/bots/worker-and-dashboard.md`.
- New Drive files need the relay (the service account cannot create files): `docs/PORTALS.md`.

## Hand-run tools the workings came from (still in their Drive folders)

`RETURN ANALYSIS/main.py` → returns · `SPF/MYNTRA/main.py` → myntra-spf · `SPF/AJIO/main.py` →
ajio-spf (`master_engine.py`) · `AJIO/Ajio Ads Gap Working/main.py` → ajio-ads.
