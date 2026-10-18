# Run a bot on this PC

Normally every bot runs on GitHub. To run one here (testing, or GitHub is down):

## Once

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

`.env` (copy of `.env.example` with the logins) and `service_account.json` must be in the project
folder.

## Run

```
python run.py <bot> [options]
```

Bots: `sale`, `vms`, `ajio-tat`, `ajio-spf`, `returns`, `myntra-spf`, `ajio-ads`.
`python run.py <bot> --help` lists the options. Useful ones for a safe test:

| Bot | Test without changing Drive / Telegram |
|---|---|
| returns, myntra-spf, ajio-ads | `--no-upload --no-telegram` |
| sale, vms, ajio-tat | `--no-drive --no-telegram` |
| ajio-spf | `--dry-run --no-upload --no-telegram` (posts nothing on AJIO) |

Results and the log go to `output/<bot>/` (git-ignored, has customer data: delete when done).

`run_local.bat <bot> [options]` does the same and appends the output to `output\local_run.log`
(for Windows Task Scheduler).

## Rules

* Do not run a Myntra bot here while the same bot runs on GitHub: Myntra refuses the same report
  twice within 15 minutes, so both runs lose reports.
* The local `FLIPKART_PASSWORD` was out of date on 18 Oct 2026 (the GitHub secret works): update
  `.env` before running Flipkart parts locally, and do not retry a failing Flipkart login.
