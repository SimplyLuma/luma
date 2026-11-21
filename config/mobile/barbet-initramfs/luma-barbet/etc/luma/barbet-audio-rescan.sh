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

# The card only appears after the ADSP boots, which can be much later than the
# session. Re-announcing it before it exists would leave WirePlumber without a
# sound device for the rest of the boot.
i=0
while [ ! -e /sys/class/sound/card0 ]; do
    i=$((i + 1))
    [ "$i" -lt 180 ] || exit 0
    sleep 1
done

sleep 2
udevadm trigger --subsystem-match=sound --action=change

# WirePlumber reads the card profile set when it opens a device that already
# exists. A card that appears after the session started is only re-announced
# as a property change, which leaves it without a selected profile, so restart
# the session daemon now that the card is present. The card can be torn down
# and re-registered while the modem hands the WLAN subsystem back, so confirm
# the sink exists and retry instead of trusting the first restart.
i=0
while [ "$i" -lt 4 ]; do
    runuser -u luma -- env XDG_RUNTIME_DIR=/run/user/1000 \
        systemctl --user restart wireplumber.service 2>/dev/null || true
    sleep 5
    if runuser -u luma -- env XDG_RUNTIME_DIR=/run/user/1000 \
        wpctl status 2>/dev/null | grep -q 'alsa_output.platform-sound.speaker'; then
        exit 0
    fi
    i=$((i + 1))
done

echo "speaker sink did not appear" >&2
exit 1
