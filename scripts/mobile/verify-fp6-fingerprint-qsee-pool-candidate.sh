#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Verify that the offline FP6 QSEE-pool candidate differs from the physically
# accepted ELF64-loader image only in its pinned DTB addition. Read-only.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-qsee-pool-candidate.sh CANDIDATE ELF64_BOOT}
base_boot=${2:?usage: verify-fp6-fingerprint-qsee-pool-candidate.sh CANDIDATE ELF64_BOOT}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp fdtget mktemp sha256sum unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
for artifact in "$candidate" "$base_boot"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_SHA256" ] ||
  die 'candidate checksum differs'
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256" ] ||
  die 'ELF64-loader base checksum differs'
[ "$FP6_FINGERPRINT_QSEE_POOL_KERNEL_ABI_UNCHANGED" = true ] ||
  die 'kernel ABI preservation gate is closed'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/candidate" "$work/base"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$work/candidate.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" >"$work/base.txt"

cmp "$work/base/kernel" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
[ "$(hash "$work/candidate/kernel")" = "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256" ] ||
  die 'candidate kernel checksum differs'
[ "$(hash "$work/candidate/ramdisk")" = "$FP6_FINGERPRINT_MDT_ELF64_RAMDISK_SHA256" ] ||
  die 'candidate ramdisk checksum differs'
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_MDT_ELF64_DTB_SHA256" ] ||
  die 'base DTB checksum differs'
[ "$(hash "$work/candidate/dtb")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" ] ||
  die 'candidate DTB checksum differs'
! cmp -s "$work/base/dtb" "$work/candidate/dtb" || die 'candidate DTB did not change'

[ "$(fdtget "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool compatible)" = \
  shared-dma-pool ] || die 'QSEE pool compatible differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool size)" = \
  '0 1000000' ] || die 'QSEE pool size differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool alignment)" = \
  '0 400000' ] || die 'QSEE pool alignment differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool alloc-ranges)" = \
  '0 80000000 0 80000000' ] || die 'QSEE pool allocation range differs'
fdtget "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool no-map >/dev/null ||
  die 'QSEE pool is not marked no-map'
pool_phandle=$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool phandle)
[ "$(fdtget -t x "$work/candidate/dtb" /firmware/scm memory-region)" = "$pool_phandle" ] ||
  die 'SCM memory-region does not reference the QSEE pool'

printf 'FP6 fingerprint low-32-bit QSEE-pool candidate: PASS\n'
printf 'candidate_sha256=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_SHA256"
printf 'dtb_sha256=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256"
printf 'kernel_unchanged=true ramdisk_unchanged=true module_abi_unchanged=true phone_accessed=false\n'
