#!/bin/sh
# Barbet WCN3990: the WLAN firmware is loaded by the modem through the WLFW
# QMI service, and tqftpserv serves it over the ADSP's IPCRTR (QRTR) channel.
# Starting the modem on barbet needs a larger modem memory carveout that is
# not part of the first PR, so stay quiet and report while it is offline.
set -eu
store=/var/lib/luma/device-firmware-barbet
[ -d "$store" ] || { echo "barbet firmware store missing: $store" >&2; exit 0; }

modem=/sys/class/remoteproc/remoteproc1
if [ ! -e "$modem/state" ] || [ "$(cat "$modem/state")" != running ]; then
    echo "modem remoteproc is offline; WCN3990 firmware cannot be loaded yet" >&2
    exit 0
fi

command -v tqftpserv >/dev/null ||
    { echo "tqftpserv is not installed; Wi-Fi not started" >&2; exit 0; }

systemctl start tqftpserv.service 2>/dev/null || true

i=0
while [ ! -e /sys/class/net/wlan0 ] && [ "$i" -lt 200 ]; do
    i=$((i + 1))
    sleep 0.1
done

if [ ! -e /sys/class/net/wlan0 ]; then
    # The built-in probe ran before the modem/QRTR services were available.
    echo 18800000.wifi > /sys/bus/platform/drivers/ath10k_snoc/unbind 2>/dev/null || true
    sleep 1
    echo 18800000.wifi > /sys/bus/platform/drivers/ath10k_snoc/bind 2>/dev/null || true
    i=0
    while [ ! -e /sys/class/net/wlan0 ] && [ "$i" -lt 200 ]; do
        i=$((i + 1))
        sleep 0.1
    done
fi

[ -e /sys/class/net/wlan0 ] ||
    { echo "wlan0 did not appear; check the modem and WLAN firmware" >&2; exit 1; }

ip link set wlan0 up 2>/dev/null || true
echo "wlan0 ready; NetworkManager owns association"
