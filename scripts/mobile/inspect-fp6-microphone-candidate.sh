#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only acceptance inventory for the RAM-booted FP6 WCD9378 candidate.
# This script does not change a control, load a module, restart a service,
# create a recording, reboot, or access the bootloader.

set -euo pipefail

expected_model='The Fairphone (Gen. 6)'
expected_kernel=7.1.2
module_root=/usr/lib/modules/7.1.2/updates/luma-fp6-microphone

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[ "$(tr -d '\000' </proc/device-tree/model)" = "$expected_model" ] ||
  die 'device identity differs'
[ "$(uname -r)" = "$expected_kernel" ] || die 'kernel release differs'

printf 'model=%s\n' "$expected_model"
printf 'kernel=%s\n' "$expected_kernel"
printf 'boot_id=%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'uptime_seconds=%s\n' "$(cut -d ' ' -f 1 /proc/uptime)"

printf '\n[module overlay]\n'
for module in regmap-sdw snd-soc-wcd-common snd-soc-wcd9378 \
  snd-soc-wcd9378-sdw; do
  file=$module_root/$module.ko
  [ -f "$file" ] || die "installed module is missing: $module"
  case "$module" in
    regmap-sdw) expected_hash=5a73fe7ca1d5c02644f42cf011a5425035624fa02091531555fb3171cbcbbab9 ;;
    snd-soc-wcd-common) expected_hash=fc236f5cedeea8050faa3baad56f12d1b60cde89be521d08dbe272eab462baf2 ;;
    snd-soc-wcd9378) expected_hash=4ad0f230d5a1fc1893e28d904562dc633f7e925976cd8057585d6fbfe2866bb6 ;;
    snd-soc-wcd9378-sdw) expected_hash=25afa7124d8ee1e8160d72a9478b1ab4336ed90a157e5266722f94357c479c38 ;;
  esac
  actual_hash=$(sha256sum "$file" | cut -d ' ' -f 1)
  [ "$actual_hash" = "$expected_hash" ] || die "installed module hash differs: $module"
  printf '%s path=%s sha256=%s vermagic=%s\n' \
    "$module" "$(modinfo -n "$module")" \
    "$actual_hash" \
    "$(modinfo -F vermagic "$module")"
done

printf '\n[loaded modules]\n'
grep -E '^(regmap_sdw|snd_soc_wcd_common|snd_soc_wcd9378|snd_soc_wcd9378_sdw) ' \
  /proc/modules || true

printf '\n[SoundWire devices]\n'
find -L /sys/bus/soundwire/devices -maxdepth 2 -type f \
  \( -name modalias -o -name uevent -o -name status \) -print -exec cat {} \; \
  2>/dev/null || true

printf '\n[ALSA cards and PCMs]\n'
cat /proc/asound/cards
cat /proc/asound/pcm
arecord -l

printf '\n[required capture controls]\n'
controls=$(amixer -c 0 controls)
for control in \
  'MultiMedia2 Mixer TX_CODEC_DMA_TX_3' \
  'ADC1 MUX' 'ADC1 Switch' 'ADC1 Volume' \
  'TX0 MODE' 'TX0 SEQUENCER Switch' 'TX SMIC MUX0' \
  'TX DEC0 MUX' 'TX_DEC0 Volume' 'TX_AIF1_CAP Mixer DEC0' \
  'ADC2 MUX' 'ADC2 Switch' 'ADC2 Volume' \
  'TX1 MODE' 'TX1 SEQUENCER Switch' 'TX SMIC MUX1' \
  'TX DEC1 MUX' 'TX_DEC1 Volume' 'TX_AIF1_CAP Mixer DEC1'; do
  printf '%s\n' "$controls" | grep -F "name='$control'" >/dev/null ||
    die "required capture control is missing: $control"
  printf 'present=%s\n' "$control"
done

printf '\n[failed units]\n'
systemctl --failed --no-legend --plain || true

printf '\n[kernel faults]\n'
if sudo -n true 2>/dev/null; then
  sudo -n journalctl -k -b --no-pager | grep -Ei \
    'wcd9378|soundwire|audio|q6|adsp|oops|BUG:|hangcheck|GMU.*timeout' || true
else
  printf 'unavailable: passwordless journal access is required\n'
fi
