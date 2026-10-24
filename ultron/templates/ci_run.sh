#!/usr/bin/env bash
# Runs one agent command in GitHub Actions without its output in the public run log:
#   bash ultron/templates/ci_run.sh <watch|chat> [options]     (same arguments as python -m ultron)
#
# The log and the output/ folder are packed and encrypted with the ARTIFACT_KEY secret into
# secure/output.tar.gz.enc (the only file uploaded as the run's artifact); the public log shows only
# the exit code. The seller's bots should run the same way, so the watcher can read their output.
set -u
mkdir -p output secure
python -m ultron "$@" > output/run.log 2>&1
status=$?
echo "ultron $1 finished with exit code $status (log and output: encrypted artifact)"
if [ -n "${ARTIFACT_KEY:-}" ]; then
  tar -czf - output | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:ARTIFACT_KEY \
    -out secure/output.tar.gz.enc
else
  echo "ARTIFACT_KEY not set: the output is not kept"
fi
rm -rf output
exit $status
