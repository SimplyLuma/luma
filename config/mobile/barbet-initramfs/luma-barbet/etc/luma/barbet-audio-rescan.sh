#!/bin/sh
# Barbet platform: make WirePlumber see the sound card.
#
# The barbet sound card is registered only after the ADSP firmware boots, and
# WirePlumber does not enumerate it on a cold boot: it needs a udev change
# event for the sound subsystem. Wait for WirePlumber to run (the session
# unit starts it), then re-announce the card. Type=simple in the unit keeps
# this off the boot critical path.
set -eu

i=0
while ! pgrep -u 1000 -x wireplumber >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -lt 900 ] || exit 0
    sleep 1
done

sleep 2
udevadm trigger --subsystem-match=sound --action=change
