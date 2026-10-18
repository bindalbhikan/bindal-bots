# GitHub runs: logs and files

The repo `bindalbhikan/bindal-bots` is public (free GitHub Actions minutes). So that nobody else
can read the bots' logs or files, every workflow runs its bot through `ci_run.sh`:

* the public log on GitHub shows only that the run started and its exit code;
* the run's full log and its `output/` folder are uploaded as an artifact **encrypted** with the
  secret `ARTIFACT_KEY` (also in `.env`).

## Read a run's log and files

1. Find the run id: GitHub > Actions > the run; it is the number in the URL
   (`.../actions/runs/39154511943` → `39154511943`). Or: `gh run list -L 10`.
2. In the project folder:

   ```
   python scripts/github/run_output.py 39154511943
   ```

3. It downloads and decrypts the artifact into `output/runs/<run id>/`:
   `output/run.log` is the log, the rest are the files the run made.

Needs the GitHub CLI (`gh`, logged in) and `ARTIFACT_KEY` in `.env`. `output/` is git-ignored and
holds customer data: delete it when done.

## ci_run.sh

Used by every `.github/workflows/*.yml` as `bash scripts/github/ci_run.sh <bot> [options]` (same
options as `python run.py <bot>`). Nothing to run by hand. A new workflow must use it too
(see `docs/ARCHITECTURE.md`, "Adding a new working").
