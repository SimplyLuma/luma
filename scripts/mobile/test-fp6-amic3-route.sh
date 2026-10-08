#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Run one bounded raw AMIC3 capture on the FP6 v4 RAM-boot candidate. The
# secondary route is always removed on exit and the proven AMIC1 channel is
# left in place. This script does not restart services, reboot, or access a
# partition.

set -euo pipefail
umask 022

output=${1:?usage: test-fp6-amic3-route.sh OUTPUT_WAV [packed-dec1|isolated-dec0] [7|20]}
route_mode=${2:-packed-dec1}
duration=${3:-7}
kernel_release=7.1.2
module_sha=10b7aaea18a2df37c1ac6ad195db47e9f89f7c7249080e7ef30a8e1221d761d9
module=/lib/modules/$kernel_release/updates/luma-fp6-microphone/snd-soc-wcd9378-sdw.ko

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
cset() { amixer -q -c 0 cset "name=$1" "$2"; }

[ "$(id -u)" -eq 0 ] || die 'run as root on the FP6'
[ "$(tr -d '\000' </sys/firmware/devicetree/base/model)" = 'The Fairphone (Gen. 6)' ] || die 'device identity differs'
[ "$(uname -r)" = "$kernel_release" ] || die 'kernel release differs'
[ -f "$module" ] || die 'staged module is missing'
[ "$(sha256sum "$module" | cut -d ' ' -f 1)" = "$module_sha" ] || die 'staged module checksum differs'
[ "$(modinfo -F filename snd_soc_wcd9378_sdw)" = "$module" ] || die 'AMIC3 module is not the loaded module path'
lsmod | grep -q '^snd_soc_wcd9378_sdw ' || die 'AMIC3 module is not loaded'
[ ! -e "$output" ] || die 'output already exists'
case "$route_mode" in
  packed-dec1|isolated-dec0) ;;
  *) die 'unknown route mode' ;;
esac
case "$duration" in
  7|20) ;;
  *) die 'duration must be 7 or 20 seconds' ;;
esac
if fuser /dev/snd/pcmC0D1c >/dev/null 2>&1; then
  die 'capture PCM is already in use'
fi

restore_primary_only() {
  set +e
  cset 'TX1 SEQUENCER Switch' 0
  cset 'ADC2 Switch' 0
  if [ "$route_mode" = packed-dec1 ]; then
    cset 'TX_AIF1_CAP Mixer DEC1' 0
    cset 'TX SMIC MUX1' ZERO
  else
    cset 'TX SMIC MUX0' ZERO
    cset 'ADC1 MUX' CH1_AMIC1
    cset 'ADC1 Volume' 12
    cset 'TX0 MODE' ADC_NORMAL
    cset 'TX SMIC MUX0' SWR_MIC0
    cset 'TX DEC0 MUX' SWR_MIC
    cset 'TX_DEC0 Volume' 84
    cset 'ADC1 Switch' 1
    cset 'TX0 SEQUENCER Switch' 1
  fi
  cset 'MultiMedia2 Mixer TX_CODEC_DMA_TX_3' 1,0
}
trap restore_primary_only EXIT INT TERM

cset 'ADC2 MUX' CH2_AMIC3
cset 'ADC2 Volume' 12
cset 'TX1 MODE' ADC_LP
if [ "$route_mode" = packed-dec1 ]; then
  # Android's AFE can expose sparse DEC0/DEC2 slots. The Linux PCM is fixed at
  # two packed channels, so feed SWR_MIC4 through DEC1 to make AMIC3 channel 1.
  cset 'TX SMIC MUX1' SWR_MIC4
  cset 'TX DEC1 MUX' SWR_MIC
  cset 'TX_DEC1 Volume' 84
  cset 'ADC2 Switch' 1
  cset 'TX1 SEQUENCER Switch' 1
  cset 'TX_AIF1_CAP Mixer DEC1' 1
  cset 'MultiMedia2 Mixer TX_CODEC_DMA_TX_3' 1,1
else
  # Feed AMIC3 through the proven DEC0/PCM channel to isolate the WCD9378 and
  # SoundWire path from all downstream stereo-slot behavior.
  cset 'TX0 SEQUENCER Switch' 0
  cset 'ADC1 Switch' 0
  cset 'TX SMIC MUX0' ZERO
  cset 'TX SMIC MUX0' SWR_MIC4
  cset 'TX DEC0 MUX' SWR_MIC
  cset 'TX_DEC0 Volume' 84
  cset 'ADC2 Switch' 1
  cset 'TX1 SEQUENCER Switch' 1
  cset 'MultiMedia2 Mixer TX_CODEC_DMA_TX_3' 1,0
fi

arecord -q -D hw:0,1 -t wav -f S16_LE -r 48000 -c 2 -d "$duration" "$output"
sync "$output"
sha256sum "$output"
