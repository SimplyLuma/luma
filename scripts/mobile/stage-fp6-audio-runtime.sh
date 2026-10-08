#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Install only the exact FP6 amplifier profile and private playback-acceptance
# sample on a running FP6. This does not install packages, restart services,
# reboot, touch a boot partition, or enable an audio route.

set -euo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-audio.env"

firmware=${1:?usage: stage-fp6-audio-runtime.sh AW88261_PROFILE TEST_TRACK TOPOLOGY UCM_CARD UCM_HIFI}
test_track=${2:?usage: stage-fp6-audio-runtime.sh AW88261_PROFILE TEST_TRACK TOPOLOGY UCM_CARD UCM_HIFI}
topology=${3:?usage: stage-fp6-audio-runtime.sh AW88261_PROFILE TEST_TRACK TOPOLOGY UCM_CARD UCM_HIFI}
ucm_card=${4:?usage: stage-fp6-audio-runtime.sh AW88261_PROFILE TEST_TRACK TOPOLOGY UCM_CARD UCM_HIFI}
ucm_hifi=${5:?usage: stage-fp6-audio-runtime.sh AW88261_PROFILE TEST_TRACK TOPOLOGY UCM_CARD UCM_HIFI}
model_file=${LUMA_FP6_MODEL_FILE:-/proc/device-tree/model}
firmware_root=${LUMA_FP6_FIRMWARE_ROOT:-/usr/lib/firmware}
ucm_root=${LUMA_FP6_UCM_ROOT:-/usr/share/alsa/ucm2}
lab_root=${LUMA_FP6_AUDIO_LAB_ROOT:-/var/lib/luma/fp6-audio-lab}
firmware_target=$firmware_root/$FP6_AUDIO_AW88261_FIRMWARE_PATH
topology_target=$firmware_root/$FP6_AUDIO_TOPOLOGY_PATH
ucm_card_target=$ucm_root/conf.d/milos/Fairphone\ \(Gen.\ 6\).conf
ucm_hifi_target=$ucm_root/Fairphone/fp6/HiFi.conf
track_target=$lab_root/acceptance.mp3

[ "$(id -u)" -eq 0 ] || {
  printf 'error: run as root on the target FP6\n' >&2
  exit 1
}
[ -f "$model_file" ] || {
  printf 'error: device-tree model is unavailable\n' >&2
  exit 1
}
model=$(tr -d '\000' <"$model_file")
[ "$model" = 'The Fairphone (Gen. 6)' ] || {
  printf 'error: refusing non-FP6 model: %s\n' "$model" >&2
  exit 1
}
for input in "$firmware" "$test_track" "$topology" "$ucm_card" "$ucm_hifi"; do
  [ -f "$input" ] || {
    printf 'error: bounded input is not a regular file: %s\n' "$input" >&2
    exit 1
  }
done
[ "$(sha256sum "$topology" | awk '{print $1}')" = \
  "$FP6_AUDIO_TOPOLOGY_SHA256" ] || {
  printf 'error: AudioReach topology checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$ucm_card" | awk '{print $1}')" = \
  "$FP6_AUDIO_UCM_CARD_SHA256" ] || {
  printf 'error: UCM card checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$ucm_hifi" | awk '{print $1}')" = \
  "$FP6_AUDIO_UCM_HIFI_SHA256" ] || {
  printf 'error: UCM HiFi checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$firmware" | awk '{print $1}')" = \
  "$FP6_AUDIO_AW88261_FIRMWARE_SHA256" ] || {
  printf 'error: AW88261 profile checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$test_track" | awk '{print $1}')" = \
  "$FP6_AUDIO_TEST_TRACK_SHA256" ] || {
  printf 'error: acceptance-track checksum differs\n' >&2
  exit 1
}

install -d -m 0755 "$(dirname -- "$firmware_target")"
install -m 0644 "$firmware" "$firmware_target"
install -d -m 0755 "$(dirname -- "$topology_target")"
install -m 0644 "$topology" "$topology_target"
install -d -m 0755 "$(dirname -- "$ucm_card_target")"
install -m 0644 "$ucm_card" "$ucm_card_target"
install -d -m 0755 "$(dirname -- "$ucm_hifi_target")"
install -m 0644 "$ucm_hifi" "$ucm_hifi_target"
install -d -m 0700 "$lab_root"
install -m 0600 "$test_track" "$track_target"

[ "$(sha256sum "$firmware_target" | awk '{print $1}')" = \
  "$FP6_AUDIO_AW88261_FIRMWARE_SHA256" ]
[ "$(sha256sum "$topology_target" | awk '{print $1}')" = \
  "$FP6_AUDIO_TOPOLOGY_SHA256" ]
[ "$(sha256sum "$ucm_card_target" | awk '{print $1}')" = \
  "$FP6_AUDIO_UCM_CARD_SHA256" ]
[ "$(sha256sum "$ucm_hifi_target" | awk '{print $1}')" = \
  "$FP6_AUDIO_UCM_HIFI_SHA256" ]
[ "$(sha256sum "$track_target" | awk '{print $1}')" = \
  "$FP6_AUDIO_TEST_TRACK_SHA256" ]

printf 'FP6 audio lab inputs staged; no route enabled and no reboot performed\n'
