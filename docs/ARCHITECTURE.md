# Architecture — BINDAL EXPORTS report bots

How the system is put together, and the checklist for adding a new working. Bot by bot detail
is in `docs/bots/`, the outside systems in `docs/PORTALS.md`.

## The moving parts

```
 Telegram (@bindal_exports_bot)            Cloudflare
 ┌──────────────┐  commands / buttons  ┌──────────────────────────────┐
 │ you, Vipul   │ ───────────────────▶ │ worker  bindal-telegram-bot  │──┐ KV "DASH": dashboard
 └──────────────┘ ◀── reports, alerts  │  * cron triggers (5 max)     │  │ numbers, Myntra
        ▲                              │  * /commands, menu buttons   │  │ sessions
        │                              │  * /api/push, /api/myntra-…  │◀─┘
        │                              └──────────────┬───────────────┘
        │                                 workflow_dispatch (GITHUB_TOKEN)
        │                                             ▼
        │                              GitHub Actions  bindalbhikan/bindal-bots (public)
        │                              one workflow per bot → scripts/github/ci_run.sh → run.py <bot>
        │                                             │
        └──────────── Telegram API ◀──────────────────┤
                                                      ├──▶ portals: Myntra, AJIO, Flipkart,
                                                      │    SRM, OMSGuru, ERP, couriers, VMS
                                                      └──▶ Google Drive: workbooks (service
                                                           account; new files via the relay)
 Dashboard worker  bindal-dashboard  reads KV "DASH" (sign-in link from /dashboard)
```

* **Scheduler = the Telegram worker** (`worker/`). Its cron triggers start GitHub workflows
  (`SCHEDULE` in `worker/src/worker.js`); GitHub's own `schedule:` is not used (it skipped
  runs). The Cloudflare free plan allows **5 cron triggers**, so monthly / weekly runs ride on a
  daily trigger with a `day` (IST day of month) or `weekdays` (IST, 0 = Sunday) filter.
* **Runner = GitHub Actions**, one `workflow_dispatch` workflow per bot. The repo is public
  (unlimited minutes), so every workflow runs its bot through `scripts/github/ci_run.sh`: the public
  log shows only the exit code; the log and `output/` are uploaded AES-encrypted
  (`ARTIFACT_KEY`); read them with `python scripts/github/run_output.py <run id>`.
* **Storage = Google Drive.** Each working keeps its workbook / folder on Drive and updates it
  in place (Drive keeps old versions). The Google service account can update files but **cannot
  create files** in a personal Drive; new files go through the Apps Script relay
  (`scripts/drive/drive_relay.gs`, `DRIVE_RELAY_URL` / `DRIVE_RELAY_TOKEN`) — `DriveFolder.put()` does
  this by itself. Folders the service account can create.
* **Telegram** gets every report, failure (`core/app.py` sends the error) and alert.
* **Dashboard**: every run posts its numbers to the worker (`/api/push/<key>`, `DASHBOARD_TOKEN`);
  the key must be listed in `DASH_KEYS` in `worker/src/worker.js`.

## Code layout

```
run.py                      entry point: python run.py <bot> [options]  (BOTS map)
bindal/
  core/                     shared by every bot
    config.py                 env() / flag() / num(); one .env locally, secrets on GitHub
    app.py                    run_bot(): args, logging, Telegram on failure, dashboard run status
    drive.py                  DriveFile (one workbook, change-checked upload), DriveFolder
                              (list / download / put by name; new files via the relay)
    telegram.py, dashboard.py, snapshot.py (sheet pictures), files.py, gauth.py
  portals/                  one client per outside system (docs/PORTALS.md)
    myntra.py  flipkart.py  ajio.py  srm.py  erp.py  omsguru.py  omsguru_returns.py
  awb_tracker/              courier tracking (BlueDart, Delhivery, Ekart, Shadowfax, Valmo, XpressBees)
  sale/  vms/  ajio_tat/  ajio_spf/  return_analysis/  myntra_spf/  ajio_ads/
                            one package per working: pipeline.py (run + main) and its own logic;
                            a working that came from a hand-run tool keeps that tool unchanged
                            as engine.py and only feeds it inputs (return_analysis, myntra_spf,
                            ajio_ads, ajio_spf's master_engine.py)
scripts/                    manual tools, one folder per job, each with a README (scripts/README.md):
  myntra/                     myntra_session.py: save a Myntra login (when a session expires)
  github/                     ci_run.sh (every workflow), run_output.py (read a run's encrypted log)
  drive/                      drive_relay.gs: the Apps Script relay that creates Drive files
  local/                      run_local.bat: run a bot on this PC
worker/                     Telegram bot + scheduler (Cloudflare);  dashboard/  the dashboard
tests/                      python -m pytest (runs on every push: .github/workflows/tests.yml)
docs/                       this file, PORTALS.md, bots/<bot>.md
```


## How a working is built

1. **Inputs from the portals**, never hand-dropped files where an API exists. Reuse a report
   already generated for the same dates; if an old one will not download, ask for a new one,
   wait, carry on. Respect each portal's limits (Myntra: same report again only after 15 min).
2. **The hand-run tool's rules unchanged** (`engine.py`), fed through a work folder laid out the
   way the tool expects. Verify the port: same inputs → same output as the tool, file by file.
3. **Drive**: download the previous workbook, update, upload in place (change-checked).
4. **Telegram**: a short phone-width table (≤ 28 characters per `<pre>` line,
   `telegram.PHONE_WIDTH`), then the file if useful. A list of AWBs / orders / SKUs is a
   `telegram.Listing` sent with `telegram.send_report`: inline when the whole message fits in one
   Telegram message, otherwise the message keeps the titles and counts and every row comes in one
   attached Excel (a sheet per list). Text from the model goes through `telegram.md`.
   A source that fails is left out and named in the notes; never shown as zero.
5. **Data must never be wrong.** A month or portal whose source data is incomplete is left out
   with a note (e.g. Flipkart returns older than ~4 months), not reported as real numbers.

## Adding a new working — checklist

1. `bindal/<name>/__init__.py`, `pipeline.py` with `run(args)` and
   `main(argv) -> run_bot("<Title>", parser, run, argv, key="<name>")`. Reuse `bindal/portals`
   clients; add a client there if the system is new (and its section in `docs/PORTALS.md`).
   If it comes from a hand-run tool: copy it as `engine.py`, make its folder settable by an env
   variable, change nothing else, and compare its output with the tool's.
2. `run.py`: add it to the docstring and to `BOTS`.
3. Settings: read with `env()` / `num()`; add to `.env.example`; Drive folder / file ids and
   logins as GitHub **secrets** (`gh secret set NAME`), tuning values as variables.
4. `.github/workflows/<name>.yml`: copy a recent one (`ajio-ads.yml`); `bash scripts/github/ci_run.sh
   <name> …`, upload only `secure/`, pass every secret it needs, sensible `timeout-minutes`.
5. Worker (`worker/src/worker.js`): a `COMMANDS` entry (`/command`, inputs, usual minutes), a
   menu button if useful, `SCHEDULE` entries on one of the **existing** 5 cron triggers (use
   `day` / `weekdays`), the dashboard keys in `DASH_KEYS`; then `cd worker && npx wrangler deploy`
   (an undeployed change does not run).
6. Dashboard: a section in `dashboard/src/page.html` for its pushed numbers; deploy `dashboard/`.
7. Tests in `tests/test_<name>.py` for the pure logic (windows, parsing, summaries).
8. Docs: `docs/bots/<name>.md`, a row in `README.md` and `CONTEXT.md`.
9. Test run first: `--no-upload --no-telegram` locally or the workflow's test input, check the
   numbers against the hand-made result, then switch it on.

## Daily schedule (IST)

| Time | What | Cron (UTC) |
|---|---|---|
| 7:00 AM | Myntra report requests: SJIT, Myntra SPF returns + delivery; return analysis on the 15th | `30 1 * * *` |
| 9:00 AM | Myntra SPF + AJIO SPF masters (AJIO tickets / replies only Wed + Sat); AJIO ads gap on the 3rd, retry on the 5th | `30 3 * * *` |
| 2:00 PM | Return analysis, only on the 15th | `30 8 * * *` |
| 3:30 PM | Daily sale | `0 10 * * *` |
| 5:30 PM | VMS, AJIO TAT | `0 12 * * *` |

All five triggers are in use: a new working must ride on one of them.
