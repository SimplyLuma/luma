#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Verify that the offline FP6 SHM-Bridge diagnostic changes only the kernel,
# retains the accepted low-32-bit pool and module ABI, and matches every pinned
# source and artifact identity. Read-only; never contacts a phone.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-shmbridge-candidate.sh CANDIDATE LOW32_BOOT KERNEL_BUNDLE}
base_boot=${2:?usage: verify-fp6-fingerprint-shmbridge-candidate.sh CANDIDATE LOW32_BOOT KERNEL_BUNDLE}
bundle=${3:?usage: verify-fp6-fingerprint-shmbridge-candidate.sh CANDIDATE LOW32_BOOT KERNEL_BUNDLE}
manifest=$bundle/manifest.env

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp fdtget grep mktemp sha256sum unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
for artifact in "$candidate" "$base_boot" "$bundle/Image.gz" \
  "$bundle/config" "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko" \
  "$manifest"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done

[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" ] ||
  die 'candidate checksum differs'
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_SHA256" ] ||
  die 'low-32-bit base checksum differs'
[ "$(hash "$bundle/Image.gz")" = "$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256" ] ||
  die 'SHM-Bridge kernel checksum differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256" ] ||
  die 'SHM-Bridge configuration checksum differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module differs'
[ "$(hash "$bundle/qseecomtee.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ] ||
  die 'QSEECOM module differs'
[ "$(hash "$repo_root/patches/linux-qseecom-shmbridge/0001-firmware-qcom-log-bounded-shmbridge-qsee-load-state.patch")" = \
  "$FP6_FINGERPRINT_SHMBRIDGE_DIAGNOSTIC_SOURCE_SHA256" ] ||
  die 'SHM-Bridge diagnostic source differs'

grep -qx '# CONFIG_QCOM_TZMEM_MODE_GENERIC is not set' "$bundle/config"
grep -qx 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y' "$bundle/config"
grep -qx '# CONFIG_DMA_CMA is not set' "$bundle/config"
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"
[ "$TZMEM_MODE" = shmbridge ] || die 'bundle TZ memory mode differs'
[ "$SHMBRIDGE_DIAGNOSTIC_PATCH_COUNT" = "$FP6_FINGERPRINT_SHMBRIDGE_PATCH_COUNT" ] ||
  die 'diagnostic patch count differs'
[ "$SHMBRIDGE_DIAGNOSTIC_PATCHSET_SHA256" = "$FP6_FINGERPRINT_SHMBRIDGE_PATCHSET_SHA256" ] ||
  die 'diagnostic patchset differs'
[ "$PHONE_ACCESSED" = false ] && [ "$PARTITION_WRITTEN" = false ] ||
  die 'bundle provenance differs'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/candidate" "$work/base"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$work/candidate.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" >"$work/base.txt"

cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
cmp "$work/base/dtb" "$work/candidate/dtb"
[ "$(hash "$work/base/kernel")" = "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256" ] ||
  die 'generic base kernel differs'
[ "$(hash "$work/candidate/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
  die 'candidate ramdisk differs'
[ "$(hash "$work/candidate/dtb")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" ] ||
  die 'candidate low-32-bit DTB differs'

[ "$(fdtget "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool compatible)" = \
  shared-dma-pool ] || die 'QSEE pool compatible differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool size)" = \
  '0 1000000' ] || die 'QSEE pool size differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool alignment)" = \
  '0 400000' ] || die 'QSEE pool alignment differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool alloc-ranges)" = \
  '0 80000000 0 80000000' ] || die 'QSEE pool range differs'
fdtget "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool no-map >/dev/null ||
  die 'QSEE pool is not no-map'
pool_phandle=$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool phandle)
[ "$(fdtget -t x "$work/candidate/dtb" /firmware/scm memory-region)" = "$pool_phandle" ] ||
  die 'SCM does not solely own the QSEE pool'

printf 'FP6 fingerprint SHM-Bridge candidate: PASS\n'
printf 'candidate_sha256=%s kernel_sha256=%s\n' \
  "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" \
  "$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256"
printf 'kernel_changed=true dtb_unchanged=true ramdisk_unchanged=true modules_unchanged=true phone_accessed=false\n'
