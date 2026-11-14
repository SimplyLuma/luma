#!/bin/sh
# Barbet platform: ADSP firmware staging and the verified built-in audio routes.
# Firmware lives in the operator's store, never in the repository.
set -eu
store=/var/lib/luma/device-firmware-barbet
[ -d "$store" ] || { echo "barbet firmware store missing: $store" >&2; exit 1; }

mkdir -p /dev/luma-fw
for entry in "$store"/*; do
    name=${entry##*/}
    ln -sfn "$entry" "/dev/luma-fw/$name"
done
for n in 1 2 3 4; do
    test -s "$store/rt5514p_dsp_fw$n.bin" ||
        { echo "missing $store/rt5514p_dsp_fw$n.bin" >&2; exit 1; }
    ln -sf "$store/rt5514p_dsp_fw$n.bin" "/dev/luma-fw/rt5514p_dsp_fw$n.bin"
done
echo /dev/luma-fw > /sys/module/firmware_class/parameters/path

if [ "$(cat /sys/class/remoteproc/remoteproc0/state)" = offline ]; then
    echo start > /sys/class/remoteproc/remoteproc0/state
fi

i=0
until [ -e /dev/snd/controlC0 ]; do
    i=$((i + 1))
    [ "$i" -lt 600 ] || { echo 'Audio card did not appear' >&2; exit 1; }
    sleep 0.1
done

amixer -c 0 sset 'Stereo1 DMIC Mux' DMIC1
amixer -c 0 sset 'Sto1 ADC MIXL DMIC' on
amixer -c 0 sset 'ADC1' 23
amixer -c 0 sset 'MultiMedia1 Mixer PRIMARY_TDM_TX_0' on
amixer -c 0 sset 'QUIN_TDM_RX_0 Audio Mixer MultiMedia2' on
amixer -c 0 sset 'ASI1 Sel' Left
amixer -c 0 sset 'Amp Gain' 16
amixer -c 0 sset 'Digital Volume Control' 90
