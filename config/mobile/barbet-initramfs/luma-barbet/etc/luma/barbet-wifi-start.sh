#!/bin/sh
# Barbet WCN3990: start the modem and the WLAN firmware service. The board
# calibration and tqftpserv are image/operator prerequisites; refuse to guess
# when they are absent, and leave the failure visible in the journal.
set -eu
store=/var/lib/luma/device-firmware-barbet
[ -d "$store" ] || { echo "barbet firmware store missing: $store" >&2; exit 0; }

command -v tqftpserv >/dev/null ||
    { echo "tqftpserv is not installed; Wi-Fi not started" >&2; exit 0; }

if [ -e /sys/class/remoteproc/remoteproc1/state ]; then
    [ "$(cat /sys/class/remoteproc/remoteproc1/state)" = running ] ||
        echo start > /sys/class/remoteproc/remoteproc1/state
fi

if systemctl list-unit-files tqftpserv.service >/dev/null 2>&1; then
    systemctl start tqftpserv.service 2>/dev/null || true
else
    tqftpserv &
fi

i=0
while [ ! -e /sys/class/net/wlan0 ] && [ "$i" -lt 200 ]; do
    i=$((i + 1))
    sleep 0.1
done
[ -e /sys/class/net/wlan0 ] ||
    { echo "wlan0 did not appear; modem firmware or board-2 missing" >&2; exit 0; }
echo "wlan0 ready; NetworkManager owns association"
