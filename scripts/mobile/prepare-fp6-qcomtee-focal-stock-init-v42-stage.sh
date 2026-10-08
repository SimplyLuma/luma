#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

# Assemble the local, phone-independent artifacts for the bounded stock Focal
# initialization and enumeration test. This script never contacts the FP6.

set -eu
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
module_dir=${1:?usage: prepare-fp6-qcomtee-focal-stock-init-v42-stage.sh QCOMTEE_DIR HELPER_DIR FOCALTECH_DIR OUTPUT_DIR}
helper_dir=${2:?missing helper directory}
focaltech_dir=${3:?missing FocalTech module directory}
output_dir=${4:?missing output directory}
focal_source=$repo_root/build/mobile/fp6-physical/fp6-qcomtee-focal-v22-stage/firmware
keymaster_source=$repo_root/build/mobile/fp6-physical/fp6-keymaster-slot-b-qrel1695/keymaster64.elf
runner=$repo_root/scripts/mobile/run-fp6-qcomtee-focal-stock-init-v42-remote.sh

expected_kernel=7.1.2-luma-fp-cma1
expected_runtime_config_sha=5bddfee4390b0f66de5709b99282e79ee3787a01749d5cc8d28ea82d6bbed7fe
expected_sync_config_sha=8b013facc735799355dd39b78d0df0a11b547b5faa0f93ff18dd080a78a220c5
expected_focal_bundle_sha=9159f0cead5b6478b4279e0ac85d83b38f62374dad80f0a925f0cb1ad02fbdb7
expected_keymaster_sha=8acb7eec3333ba720fb7fb95ad6832567b561d41e0125386451e475ec9899198
expected_runner_sha=87378833d681939460b2c1eda880436a140a6ff85b45df3442b7c145f1520872
expected_focaltech_patchset_sha=e6c8976f3d4c1156fdc88fce7c16d241075f89c11f944d85be3131193a9b81d0

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }
manifest_value() { sed -n "s/^$2=//p" "$1"; }

[ ! -e "$output_dir" ] || die 'refuse to overwrite output directory'
[ -f "$module_dir/qcomtee-control.ko" ] || die 'qcomtee module missing'
[ -f "$module_dir/manifest.env" ] || die 'qcomtee manifest missing'
[ -x "$helper_dir/fp6-focal-driver-stage" ] || die 'driver helper missing or not executable'
[ -f "$helper_dir/manifest.env" ] || die 'driver helper manifest missing'
[ -f "$focaltech_dir/focaltech_fp.ko" ] || die 'candidate FocalTech module missing'
[ -f "$focaltech_dir/manifest.env" ] || die 'candidate FocalTech manifest missing'
[ "$(manifest_value "$module_dir/manifest.env" LUMA_FP6_QCOMTEE_CONTROL_BUILD_VERSION)" = 41 ] ||
  die 'qcomtee build version differs'
[ "$(manifest_value "$module_dir/manifest.env" KERNEL_RELEASE)" = "$expected_kernel" ] ||
  die 'qcomtee kernel release differs'
[ "$(manifest_value "$module_dir/manifest.env" RUNTIME_CONFIG_SHA256)" = "$expected_runtime_config_sha" ] ||
  die 'qcomtee runtime configuration differs'
[ "$(manifest_value "$module_dir/manifest.env" FOCAL_SYNC_CONFIG_SHA256)" = "$expected_sync_config_sha" ] ||
  die 'qcomtee Focal configuration differs'
[ "$(manifest_value "$module_dir/manifest.env" BIOMETRIC_COMMANDS_ALLOWED)" = enumerate-only ] ||
  die 'qcomtee biometric allowlist differs'
[ "$(manifest_value "$helper_dir/manifest.env" LUMA_FP6_FOCAL_DRIVER_STAGE_BUILD_VERSION)" = 1 ] ||
  die 'driver helper build version differs'
[ "$(manifest_value "$helper_dir/manifest.env" CAPTURE_ALLOWED)" = false ] ||
  die 'driver helper capture policy differs'
[ "$(manifest_value "$helper_dir/manifest.env" ENROLL_ALLOWED)" = false ] ||
  die 'driver helper enrollment policy differs'
[ "$(manifest_value "$helper_dir/manifest.env" AUTHENTICATE_ALLOWED)" = false ] ||
  die 'driver helper authentication policy differs'
[ "$(manifest_value "$focaltech_dir/manifest.env" LUMA_FP6_FOCALTECH_CONTROL_BUILD_VERSION)" = 21 ] ||
  die 'candidate FocalTech build version differs'
[ "$(manifest_value "$focaltech_dir/manifest.env" KERNEL_RELEASE)" = "$expected_kernel" ] ||
  die 'candidate FocalTech kernel release differs'
[ "$(manifest_value "$focaltech_dir/manifest.env" RUNTIME_CONFIG_SHA256)" = "$expected_runtime_config_sha" ] ||
  die 'candidate FocalTech runtime configuration differs'
[ "$(manifest_value "$focaltech_dir/manifest.env" PATCHSET_SHA256)" = "$expected_focaltech_patchset_sha" ] ||
  die 'candidate FocalTech patchset differs'
[ "$(manifest_value "$focaltech_dir/manifest.env" IRQ_ONESHOT)" = false ] ||
  die 'candidate FocalTech IRQ policy differs'
[ "$(manifest_value "$focaltech_dir/manifest.env" ENROLL_ALLOWED)" = false ] ||
  die 'candidate FocalTech enrollment policy differs'
[ "$(manifest_value "$focaltech_dir/manifest.env" AUTHENTICATE_ALLOWED)" = false ] ||
  die 'candidate FocalTech authentication policy differs'

module_sha=$(manifest_value "$module_dir/manifest.env" MODULE_SHA256)
helper_sha=$(manifest_value "$helper_dir/manifest.env" BINARY_SHA256)
focaltech_sha=$(manifest_value "$focaltech_dir/manifest.env" MODULE_SHA256)
[ -n "$module_sha" ] && [ "$(hash "$module_dir/qcomtee-control.ko")" = "$module_sha" ] ||
  die 'qcomtee module hash differs'
[ -n "$helper_sha" ] && [ "$(hash "$helper_dir/fp6-focal-driver-stage")" = "$helper_sha" ] ||
  die 'driver helper hash differs'
[ -n "$focaltech_sha" ] && [ "$(hash "$focaltech_dir/focaltech_fp.ko")" = "$focaltech_sha" ] ||
  die 'candidate FocalTech module hash differs'
[ "$(hash "$keymaster_source")" = "$expected_keymaster_sha" ] || die 'keymaster ELF differs'
[ "$(hash "$runner")" = "$expected_runner_sha" ] || die 'remote runner differs'
[ "$(find "$focal_source" -maxdepth 1 -type f -name 'focal64.b??' | wc -l | tr -d ' ')" = 9 ] ||
  die 'Focal trustlet split count differs'
focal_bundle_sha=$(
  cd "$focal_source"
  sha256sum focal64.b00 focal64.b01 focal64.b02 focal64.b03 \
    focal64.b04 focal64.b05 focal64.b06 focal64.b07 focal64.b08 |
    sha256sum | awk '{print $1}'
)
[ "$focal_bundle_sha" = "$expected_focal_bundle_sha" ] || die 'Focal trustlet bundle differs'

mkdir -p "$output_dir/firmware"
install -m 0644 "$module_dir/qcomtee-control.ko" "$output_dir/qcomtee-control.ko"
install -m 0755 "$helper_dir/fp6-focal-driver-stage" "$output_dir/fp6-focal-driver-stage"
install -m 0644 "$focaltech_dir/focaltech_fp.ko" "$output_dir/focaltech_fp.ko"
for split in 00 01 02 03 04 05 06 07 08; do
  install -m 0644 "$focal_source/focal64.b$split" "$output_dir/firmware/focal64.b$split"
done
install -m 0644 "$keymaster_source" "$output_dir/firmware/keymaster64.elf"

{
  printf 'LUMA_FP6_FOCAL_STOCK_INIT_STAGE_VERSION=42\n'
  printf 'KERNEL_RELEASE=%s\n' "$expected_kernel"
  printf 'RUNTIME_CONFIG_SHA256=%s\n' "$expected_runtime_config_sha"
  printf 'QCOMTEE_MODULE_SHA256=%s\n' "$module_sha"
  printf 'FOCAL_DRIVER_HELPER_SHA256=%s\n' "$helper_sha"
  printf 'FOCALTECH_CANDIDATE_MODULE_SHA256=%s\n' "$focaltech_sha"
  printf 'FOCALTECH_PATCHSET_SHA256=%s\n' "$expected_focaltech_patchset_sha"
  printf 'FOCAL_SYNC_CONFIG_SHA256=%s\n' "$expected_sync_config_sha"
  printf 'FOCAL_TRUSTLET_BUNDLE_SHA256=%s\n' "$expected_focal_bundle_sha"
  printf 'KEYMASTER_ELF_SHA256=%s\n' "$expected_keymaster_sha"
  printf 'REMOTE_RUNNER_SHA256=%s\n' "$expected_runner_sha"
  printf 'PHYSICAL_OPERATION=stock-initialize-and-enumerate-only\n'
  printf 'CAPTURE_ALLOWED=false\n'
  printf 'ENROLL_ALLOWED=false\n'
  printf 'AUTHENTICATE_ALLOWED=false\n'
  printf 'TEMPLATES_WRITTEN=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"
chmod 0644 "$output_dir/manifest.env"

[ "$(find "$output_dir" -type f | wc -l | tr -d ' ')" = 14 ] || die 'assembled file count differs'
printf 'FP6 focal stock-init v42 stage: %s\n' "$output_dir"
printf 'qcomtee module SHA-256: %s\n' "$module_sha"
printf 'driver helper SHA-256: %s\n' "$helper_sha"
printf 'candidate FocalTech module SHA-256: %s\n' "$focaltech_sha"
