#!/usr/bin/env bash
# Runs one bot in GitHub Actions without anything of its output in the public run log:
#   bash scripts/github/ci_run.sh <bot> [options]          (same arguments as python run.py)
#
# The repository is public, so its Actions logs and artifacts are readable by anyone. The bot's
# log and its output/ folder are packed and encrypted with the ARTIFACT_KEY secret into
# secure/output.tar.gz.enc (the only file uploaded as the run's artifact); the public log shows
# only the exit code. Read a run's output with:  python scripts/github/run_output.py <run id>
# Failures still reach Telegram from the bot itself.
set -u
mkdir -p output secure
python run.py "$@" > output/run.log 2>&1
status=$?
echo "run.py $1 finished with exit code $status (log and output: encrypted artifact)"
if [ -n "${ARTIFACT_KEY:-}" ]; then
  tar -czf - output | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:ARTIFACT_KEY \
    -out secure/output.tar.gz.enc
else
  echo "ARTIFACT_KEY not set: the output is not kept"
fi
rm -rf output
exit $status
