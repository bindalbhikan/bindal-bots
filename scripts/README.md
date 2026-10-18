# scripts — manual tools

The bots run by themselves (GitHub Actions, started by the Telegram worker). These are the few
things done by hand, one folder per job. Each folder's README has the steps.

| Folder | When you need it | Command |
|---|---|---|
| [`myntra/`](myntra/README.md) | Telegram says a **Myntra session expired** (MAIN / DBCC / FMCC) | `python scripts/myntra/myntra_session.py` |
| [`github/`](github/README.md) | You want to **read a run's log or files** (they are encrypted, the repo is public) | `python scripts/github/run_output.py <run id>` |
| [`drive/`](drive/README.md) | The **Drive relay** stops working, or the relay token is changed | Apps Script, by hand |
| [`local/`](local/README.md) | You want to **run a bot on this PC** instead of GitHub | `python run.py <bot> ...` |

All commands run from the project folder (`G:\My Drive\BINDAL EXPORTS\BOTS\Bindal Exports Bot`)
and read the logins from `.env` there. Never commit `.env`, `service_account.json`, the Myntra
`.txt` / `.json` session files or anything in `output/`: they hold live logins or customer data
(they are git-ignored).

Other manual jobs that are not scripts:

* **Telegram bot / worker settings**: `worker/setup.ps1` (one-time setup, also sets the command
  list), then `cd worker && npx wrangler deploy` after any change to `worker/src/worker.js`.
* **Dashboard**: `cd dashboard && npx wrangler deploy` after any change to `dashboard/src/`.
* **Start a bot now**: send its command in Telegram (`/help` lists them), or GitHub > Actions >
  the workflow > Run workflow.
