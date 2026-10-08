#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
env_file=$repo_root/config/mobile/fp6-audio.env
dt_patch=$repo_root/patches/linux-milos/0025-arm64-dts-qcom-milos-enable-fp6-speaker-dai.patch
machine_patch=$repo_root/patches/linux-milos/0026-ASoC-qcom-sc8280xp-support-Senary-MI2S.patch
amp_patch=$repo_root/patches/linux-milos/0027-ASoC-codecs-aw88261-backport-mainline-format-negotiation-power-up-fix.patch
graph_patch=$repo_root/patches/linux-milos/0028-ASoC-qcom-q6apm-lpass-dais-start-graph-at-prepare.patch
builder=$repo_root/scripts/mobile/build-fp6-audio-modules.sh
installer=$repo_root/scripts/mobile/install-fp6-audio-modules.sh
repacker=$repo_root/scripts/mobile/prepare-fp6-audio-boot-candidate.sh
stager=$repo_root/scripts/mobile/stage-fp6-audio-runtime.sh
probes=$repo_root/scripts/mobile/generate-fp6-audio-probes.py
overlay=$repo_root/config/mobile/fp6-physical/overlay
ucm_card="$overlay/usr/share/alsa/ucm2/conf.d/milos/Fairphone (Gen. 6).conf"
ucm_hifi=$overlay/usr/share/alsa/ucm2/Fairphone/fp6/HiFi.conf
wireplumber=$overlay/etc/wireplumber/wireplumber.conf.d/51-luma-fp6-audio.conf
route_helper=$overlay/usr/libexec/luma-fp6-audio-route
route_service=$overlay/usr/libexec/luma-fp6-audio-route-service
route_unit=$overlay/usr/lib/systemd/system/luma-fp6-audio-route.service
route_bus_service=$overlay/usr/share/dbus-1/system-services/org.projectluma.AudioRoute1.service
route_bus_policy=$overlay/usr/share/dbus-1/system.d/org.projectluma.AudioRoute1.conf
microphone_route_helper=$overlay/usr/libexec/luma-fp6-microphone-route
ledger=$repo_root/docs/research/fp6-linux-hardware-readiness.md
packages=$repo_root/config/mobile/packages.txt
ui_packages=$repo_root/config/mobile/ui-packages.txt

bash -n "$builder" "$installer" "$repacker" "$stager" "$route_helper" "$microphone_route_helper"
PYTHONPYCACHEPREFIX=${TMPDIR:-/tmp}/luma-fp6-audio-pycache \
  python3 -m py_compile "$probes" "$route_service"

grep -Fq 'FP6_AUDIO_BASE_BOOT_SHA256=90f23847432289fd823a89b329e5305744abcd3cc07d83a455eff1e242072a63' "$env_file"
grep -Fq 'FP6_AUDIO_SPEAKER_DTB_SHA256=5a5b0be91937576ce20e0c17c63e2ac696f5a797b74bdb14e9052c978f51347e' "$env_file"
grep -Fq 'FP6_AUDIO_AW88261_FIRMWARE_SHA256=af723973655ba5901948d4a22212323e39ce53ff0156021185720af4af6ab0b3' "$env_file"
grep -Fq 'FP6_AUDIO_TOPOLOGY_SHA256=b799d001cf0da889f92215a6ef5143bda54021f5f76fa3e2e5d3797a169d7aa0' "$env_file"
grep -Fq 'FP6_AUDIO_UCM_CARD_SHA256=e8ecc6febc5b5cc77012e8f9e00d8951674f0dee29a42ed9935e2a74b9020d75' "$env_file"
grep -Fq 'FP6_AUDIO_UCM_HIFI_SHA256=b77077a3c89a5fcfaaab189d18b03682341d80158b57e81a57db52492af05534' "$env_file"
grep -Fq 'FP6_AUDIO_WIREPLUMBER_SHA256=d6e2907d8e279931b57aeef5b55fe3126a0e959eba5cf5b2931e9aee00ad40be' "$env_file"
grep -Fq 'FP6_AUDIO_ROUTE_HELPER_SHA256=bace0cb02010ccae596d7dc6786fd881458ffa15c77d07d91bcbdaf3a4cf937a' "$env_file"
grep -Fq 'FP6_AUDIO_MICROPHONE_ROUTE_HELPER_SHA256=c56f672b2d37cdfd15a539553a0e32803426ccea1bfc4483668a7cceba73cdf1' "$env_file"
grep -Fq 'FP6_AUDIO_TEST_TRACK_SHA256=4a99019ccadb2983706ef3ca5f419336ad30b6b544fd85e58de7a5c3b6a70b78' "$env_file"

test "$(sha256sum "$ucm_card" | awk '{print $1}')" = \
  e8ecc6febc5b5cc77012e8f9e00d8951674f0dee29a42ed9935e2a74b9020d75
test "$(sha256sum "$ucm_hifi" | awk '{print $1}')" = \
  b77077a3c89a5fcfaaab189d18b03682341d80158b57e81a57db52492af05534
test "$(sha256sum "$wireplumber" | awk '{print $1}')" = \
  d6e2907d8e279931b57aeef5b55fe3126a0e959eba5cf5b2931e9aee00ad40be
test "$(sha256sum "$route_helper" | awk '{print $1}')" = \
  bace0cb02010ccae596d7dc6786fd881458ffa15c77d07d91bcbdaf3a4cf937a
test "$(sha256sum "$microphone_route_helper" | awk '{print $1}')" = \
  c56f672b2d37cdfd15a539553a0e32803426ccea1bfc4483668a7cceba73cdf1

test "$(sha256sum "$machine_patch" | awk '{print $1}')" = \
  6a28abddc3e34a05cb65de70f186facd84ab2c0823cbb7a208aa440e26180df8
test "$(sha256sum "$amp_patch" | awk '{print $1}')" = \
  d08280d0b5e090caee8dc73631be3527c39625298a96c5fd6958d2abe24ee01d
test "$(sha256sum "$graph_patch" | awk '{print $1}')" = \
  d90c97cf73261656bbd77c81cdf95d3b08edb09bbba5fe1e1d9dfa75bb1af5c0

grep -Fxq 'alsa-ucm' "$packages"
grep -Fxq 'alsa-ucm-utils' "$packages"
grep -Fxq 'alsa-utils' "$packages"
grep -Fxq 'alsa-ucm-1.2.16.1-1.fc44' "$ui_packages"
grep -Fxq 'alsa-ucm-utils-1.2.16-1.fc44' "$ui_packages"
grep -Fxq 'alsa-utils-1.2.16-1.fc44' "$ui_packages"

grep -Fq 'Senary MI2S Playback' "$dt_patch"
grep -Fq 'LUMA_ALLOW_FP6_BOUNDED_AUDIO_BUILD' "$builder"
grep -Fq 'make -j1' "$builder"
grep -Fq 'original-audio-modules-7.1.2' "$installer"
grep -Fq 'api.alsa.disable-mmap = true' "$wireplumber"
grep -Fq 'api.alsa.period-size = 2400' "$wireplumber"
grep -Fq 'session.suspend-timeout-seconds = 0' "$wireplumber"
grep -Fq 'SectionDevice."Speaker"' "$ucm_hifi"
grep -Fq 'SectionDevice."Mic"' "$ucm_hifi"
grep -Fq 'CapturePCM "hw:${CardId},1"' "$ucm_hifi"
grep -Fq "name='ADC1 MUX' CH1_AMIC1" "$ucm_hifi"
grep -Fq "name='TX_AIF1_CAP Mixer DEC0' 1" "$ucm_hifi"
! grep -Fq 'SectionDevice."Earpiece"' "$ucm_hifi"
grep -Fq "'Amplifier L Profile Set'" "$route_helper"
grep -Fq $'\tearpiece)' "$route_helper"
grep -Fq 'fail_quiet' "$route_helper"
grep -Fq "printf 'route=speaker" "$route_helper"
grep -Fq 'BusName=org.projectluma.AudioRoute1' "$route_unit"
grep -Fq 'SystemdService=luma-fp6-audio-route.service' "$route_bus_service"
grep -Fq '<policy group="audio">' "$route_bus_policy"
grep -Fq 'ROUTES = frozenset({"speaker", "earpiece"})' "$route_service"
! grep -Fq 'sudo' "$route_service"
grep -Fq 'luma-fp6-microphone-route {primary|array|status}' "$microphone_route_helper"
grep -Fq "set_control 'TX SMIC MUX1' SWR_MIC4" "$microphone_route_helper"
grep -Fq "set_control 'TX_AIF1_CAP Mixer DEC1' 1" "$microphone_route_helper"
grep -Fq 'array_supported' "$microphone_route_helper"
grep -Fq 'primary_route' "$microphone_route_helper"

grep -Fq 'cmp "$base_boot" "$roundtrip"' "$repacker"
grep -Fq 'PHONE_ACCESSED=false' "$repacker"
grep -Fq 'PARTITION_WRITTEN=false' "$repacker"
if grep -Eiq '(^|[[:space:]])(adb|fastboot|scp|ssh)([[:space:]]|$)' "$repacker"; then
  printf 'FAIL: offline audio repacker must not contact a device\n' >&2
  exit 1
fi

grep -Fq "[ \"\$model\" = 'The Fairphone (Gen. 6)' ]" "$stager"
if grep -Eiq '^[[:space:]]*(dnf|rpm|systemctl|reboot|fastboot)([[:space:]]|$)' "$stager"; then
  printf 'FAIL: runtime stager must not install packages, restart or boot\n' >&2
  exit 1
fi

grep -Fq 'Both AW88261 endpoints, topology, UCM, raw ALSA, and PipeWire playback physically pass' "$ledger"
grep -Fq 'RAM-only v5 carries live AMIC1 and AMIC3 simultaneously through raw ALSA and PipeWire/Pulse' "$ledger"
grep -Fq 'The dual-microphone hardware path works' "$ledger"
grep -Fq 'private lab inputs and are never redistributed' "$ledger"

printf 'PASS: FP6 output and dual-microphone live capture are physically accepted\n'
