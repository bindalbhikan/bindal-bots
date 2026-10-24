#!/usr/bin/env sh
# macOS: install the agent in a virtualenv and run it as a launchd agent that starts at login and
# restarts when it stops.
#   cd <the folder with agent.toml and .env>
#   sh ultron/deploy/macos/install.sh
set -eu
DIR=$(pwd)
[ -f agent.toml ] || { echo "run this in the folder that holds agent.toml"; exit 1; }
[ -f .env ] || echo "note: no .env here yet - put the secrets in $DIR/.env (see ultron/README.md)"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install "./ultron[full]"
.venv/bin/ultron check

PLIST="$HOME/Library/LaunchAgents/com.ultron.agent.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$DIR/output"
sed "s#@DIR@#$DIR#g" ultron/deploy/macos/com.ultron.agent.plist > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "running. log: tail -f $DIR/output/ultron.log   stop: launchctl unload $PLIST"
