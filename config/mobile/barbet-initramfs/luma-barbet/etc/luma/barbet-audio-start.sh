#!/bin/sh
# Barbet platform: ADSP firmware staging and the verified built-in audio routes.
# Firmware lives in the operator's store, never in the repository. The shared
# store also serves the GPU; the barbet tree holds the ADSP; the RT5514P DSP
# blobs may be in either store or the boot hook's /dev/luma-codec-firmware.
set -eu
shared=/var/lib/luma/device-firmware
barbet=/var/lib/luma/device-firmware-barbet
[ -d "$shared" ] || [ -d "$barbet" ] ||
    { echo "no barbet firmware store" >&2; exit 1; }

# Merge both stores into one firmware view. The shared store holds the modem
# and WLAN files, the barbet store holds ADSP; linking either directory whole
# would shadow the other and break the modem/WLAN load.
mkdir -p /dev/luma-fw/qcom/sm7250/google/barbet
for store in "$shared" "$barbet"; do
    [ -d "$store" ] || continue
    for entry in "$store"/*; do
        name=${entry##*/}
        [ "$name" = qcom ] && continue
        ln -sfn "$entry" "/dev/luma-fw/$name"
    done
    for entry in "$store"/qcom/*; do
        name=${entry##*/}
        [ "$name" = sm7250 ] && continue
        [ -e "/dev/luma-fw/qcom/$name" ] || ln -s "$entry" "/dev/luma-fw/qcom/$name"
    done
    src="$store/qcom/sm7250/google/barbet"
    [ -d "$src" ] || continue
    for file in "$src"/*; do
        ln -sfn "$file" "/dev/luma-fw/qcom/sm7250/google/barbet/${file##*/}"
    done
done

for n in 1 2 3 4; do
    found=""
    for candidate in \
        "/dev/luma-codec-firmware/rt5514p_dsp_fw$n.bin" \
        "$shared/rt5514p_dsp_fw$n.bin" \
        "$barbet/rt5514p_dsp_fw$n.bin"; do
        if [ -s "$candidate" ]; then
            ln -sf "$candidate" "/dev/luma-fw/rt5514p_dsp_fw$n.bin"
            found=1
            break
        fi
    done
    [ -n "$found" ] || { echo "missing rt5514p_dsp_fw$n.bin" >&2; exit 1; }
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
