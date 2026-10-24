#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot runs this when the phone starts: keep the CPU awake, start the services.
termux-wake-lock
. "$PREFIX/etc/profile.d/start-services.sh"
