#!/bin/sh
# Barbet WCN3990 bring-up. The WLAN firmware is booted by the modem, and
# ath10k only registers once the "mpss" SSR notifier fires. The FP6 carrier
# service must not start the modem first (it runs on this shared image and
# loses the race), so this service owns the ordered start: wait for ath10k,
# serve the firmware over QRTR, then start the modem.
set -eu
store=/var/lib/luma/device-firmware-barbet
[ -d "$store" ] || { echo "barbet firmware store missing: $store" >&2; exit 1; }
[ -d /dev/luma-fw ] || { echo "firmware path not staged" >&2; exit 1; }
command -v tqftpserv >/dev/null ||
    { echo "tqftpserv is not installed; Wi-Fi not started" >&2; exit 1; }

# ath10k probes asynchronously and registers the mpss notifier there.
i=0
while [ ! -e /sys/bus/platform/devices/18800000.wifi/driver ]; do
    i=$((i + 1))
    [ "$i" -lt 600 ] || { echo "ath10k did not bind" >&2; exit 1; }
    sleep 0.1
done

systemctl start tqftpserv.service 2>/dev/null || true

# Start the modem only now; its powerup is what registers ath10k.
if [ -e /sys/class/remoteproc/remoteproc1/state ] &&
   [ "$(cat /sys/class/remoteproc/remoteproc1/state)" != running ]; then
    echo start > /sys/class/remoteproc/remoteproc1/state
fi

i=0
while [ ! -e /sys/class/net/wlan0 ]; do
    i=$((i + 1))
    [ "$i" -lt 600 ] || { echo "wlan0 did not appear; check the WLAN firmware" >&2; exit 1; }
    sleep 0.1
done

ip link set wlan0 up 2>/dev/null || true
echo "wlan0 ready; NetworkManager owns association"
