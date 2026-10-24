#!/data/data/com.termux/files/usr/bin/sh
# Android (Termux): install the agent and run it as a Termux service that restarts when it stops,
# and (with the Termux:Boot app) starts when the phone starts.
#   pkg install git && git clone <the seller's bots repo> && cd <it>
#   sh ultron/deploy/termux/setup.sh              this phone answers the bot
#   sh ultron/deploy/termux/setup.sh --fallback   only while the main host (PC / VPS) is down
# Keep the phone on a charger and allow Termux to run in the background (Android battery settings).
set -eu
DIR=$(pwd)
[ -f agent.toml ] || { echo "run this in the folder that holds agent.toml"; exit 1; }
[ -f .env ] || echo "note: no .env here yet - put the secrets in $DIR/.env (see ultron/README.md)"

pkg update -y
# pure-Python agent; the heavy optional parts come from Termux packages, not pip builds
pkg install -y python termux-services python-cryptography
pkg install -y tur-repo && pkg install -y python-pandas || echo "note: pandas not installed: xlsx summaries of run files are off"
pip install ./ultron
pip install openpyxl || true
ultron check

SV="$PREFIX/var/service/ultron"
mkdir -p "$SV/log" "$DIR/output"
sed "s#@DIR@#$DIR#g; s#@ARGS@#${1:-}#g" ultron/deploy/termux/run > "$SV/run"
printf '#!/data/data/com.termux/files/usr/bin/sh\nexec svlogd -tt %s\n' "$DIR/output" > "$SV/log/run"
chmod +x "$SV/run" "$SV/log/run"

mkdir -p "$HOME/.termux/boot"
cp ultron/deploy/termux/boot.sh "$HOME/.termux/boot/ultron.sh"
chmod +x "$HOME/.termux/boot/ultron.sh"

echo "restart Termux once (termux-services starts after that), then: sv up ultron"
echo "log: tail -f $DIR/output/current   stop: sv down ultron"
