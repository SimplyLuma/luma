#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Verify the offline FP6 fingerprint ELF64-loader candidate against the exact
# physically accepted listener-v2 boot. This script is read-only and has no
# fastboot, SSH, partition, trusted-application, or biometric operation.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-elf64-candidate.sh CANDIDATE KERNEL_BUNDLE LISTENER_V2_BOOT}
bundle=${2:?usage: verify-fp6-fingerprint-elf64-candidate.sh CANDIDATE KERNEL_BUNDLE LISTENER_V2_BOOT}
base_boot=${3:?usage: verify-fp6-fingerprint-elf64-candidate.sh CANDIDATE KERNEL_BUNDLE LISTENER_V2_BOOT}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp mktemp sha256sum unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
for artifact in "$candidate" "$bundle/Image.gz" "$bundle/config" \
  "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko" "$base_boot"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done

[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256" ] ||
  die 'candidate checksum differs'
[ "$(hash "$bundle/Image.gz")" = "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256" ] ||
  die 'kernel checksum differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_MDT_ELF64_CONFIG_SHA256" ] ||
  die 'kernel configuration checksum differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module checksum differs'
[ "$(hash "$bundle/qseecomtee.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ] ||
  die 'QSEECOM module checksum differs'
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_LISTENER_V2_BOOT_SHA256" ] ||
  die 'listener-v2 base checksum differs'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/candidate" "$work/base"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$work/candidate.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" >"$work/base.txt"

cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$work/base/dtb" "$work/candidate/dtb"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
[ "$(hash "$work/candidate/dtb")" = "$FP6_FINGERPRINT_MDT_ELF64_DTB_SHA256" ] ||
  die 'candidate DTB checksum differs'
[ "$(hash "$work/candidate/ramdisk")" = "$FP6_FINGERPRINT_MDT_ELF64_RAMDISK_SHA256" ] ||
  die 'candidate ramdisk checksum differs'

printf 'FP6 fingerprint ELF64 candidate: PASS\n'
printf 'candidate_sha256=%s\n' "$FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256"
printf 'kernel_sha256=%s\n' "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256"
printf 'dtb_unchanged=true ramdisk_unchanged=true modules_unchanged=true phone_accessed=false\n'
