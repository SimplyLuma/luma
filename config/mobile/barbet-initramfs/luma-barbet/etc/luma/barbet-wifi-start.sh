#!/bin/sh
# Barbet WCN3990 bring-up. The WLAN firmware is booted by the modem, and the
# modem only does that while the ADSP is not owning the WLAN subsystem. The
# ADSP is needed for audio, so the order on this device is: serve firmware
# over QRTR, stop the ADSP, start the modem (WLAN boots and WLFW appears),
# then start the ADSP for audio. This service runs before luma-barbet-audio.
set -eu
store=/var/lib/luma/device-firmware
[ -d "$store" ] || { echo "shared firmware store missing: $store" >&2; exit 1; }
command -v tqftpserv >/dev/null ||
    { echo "tqftpserv is not installed; Wi-Fi not started" >&2; exit 1; }

# ath10k probes asynchronously; its QMI client must exist before the chip.
i=0
while [ ! -e /sys/bus/platform/devices/18800000.wifi/driver ]; do
    i=$((i + 1))
    [ "$i" -lt 600 ] || { echo "ath10k did not bind" >&2; exit 1; }
    sleep 0.1
done

systemctl start tqftpserv.service 2>/dev/null || true

# luma-barbet-audio runs first and stages /dev/luma-fw; the modem uses that
# path for the WLAN firmware, matching the sequence that booted the chip.
i=0
while [ ! -e /dev/luma-fw ] && [ "$i" -lt 300 ]; do
    i=$((i + 1))
    sleep 0.1
done

# Wait until the TFTP service is actually visible over QRTR before the modem
# starts asking for firmware.
i=0
while ! qrtr-lookup 2>/dev/null | awk '$1 == 4096 { found = 1 } END { exit !found }'; do
    i=$((i + 1))
    [ "$i" -lt 100 ] || { echo "tqftpserv did not register over QRTR" >&2; exit 1; }
    sleep 0.1
done

# The WLAN subsystem belongs to the modem while the ADSP is down.
if [ -e /sys/class/remoteproc/remoteproc0/state ] &&
   [ "$(cat /sys/class/remoteproc/remoteproc0/state)" = running ]; then
    echo stop > /sys/class/remoteproc/remoteproc0/state
    i=0
    while [ "$(cat /sys/class/remoteproc/remoteproc0/state)" != offline ]; do
        i=$((i + 1))
        [ "$i" -lt 100 ] || { echo "ADSP did not stop" >&2; exit 1; }
        sleep 0.1
    done
fi

if [ -e /sys/class/remoteproc/remoteproc1/state ] &&
   [ "$(cat /sys/class/remoteproc/remoteproc1/state)" != running ]; then
    echo start > /sys/class/remoteproc/remoteproc1/state
fi

# The WLAN boot is normally quick. Do not delay audio for minutes when it
# fails: wait briefly, then hand the system back and record the failure.
i=0
while [ ! -e /sys/class/net/wlan0 ] && [ "$i" -lt 300 ]; do
    i=$((i + 1))
    sleep 0.1
done
if [ -e /sys/class/net/wlan0 ]; then
    ip link set wlan0 up 2>/dev/null || true
    echo "wlan0 ready; NetworkManager owns association"
else
    echo "wlan0 did not appear; check the WLAN firmware" >&2
fi

# Bring audio back whether or not Wi-Fi came up; luma-barbet-audio runs next.
if [ -e /sys/class/remoteproc/remoteproc0/state ] &&
   [ "$(cat /sys/class/remoteproc/remoteproc0/state)" != running ]; then
    echo start > /sys/class/remoteproc/remoteproc0/state
fi
