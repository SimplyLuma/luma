#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only structural verification for the FP6 stock-QSEECOM-heaps boot.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-stock-heaps-candidate.sh CANDIDATE SHMBRIDGE_BOOT KERNEL_BUNDLE}
base_boot=${2:?usage: verify-fp6-fingerprint-stock-heaps-candidate.sh CANDIDATE SHMBRIDGE_BOOT KERNEL_BUNDLE}
bundle=${3:?usage: verify-fp6-fingerprint-stock-heaps-candidate.sh CANDIDATE SHMBRIDGE_BOOT KERNEL_BUNDLE}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp fdtget mktemp sha256sum unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
for artifact in "$candidate" "$base_boot" "$bundle/Image.gz" \
  "$bundle/config" "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_STOCK_HEAPS_BOOT_SHA256" ] ||
  die 'candidate checksum differs'
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" ] ||
  die 'SHM-Bridge base checksum differs'
[ "$(hash "$bundle/Image.gz")" = "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_SHA256" ] ||
  die 'stock-heaps kernel differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256" ] ||
  die 'kernel configuration differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module differs'
[ "$(hash "$bundle/qseecomtee.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ] ||
  die 'QSEECOM module differs'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/candidate" "$work/base"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$work/candidate.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" >"$work/base.txt"

cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
[ "$(hash "$work/candidate/dtb")" = "$FP6_FINGERPRINT_STOCK_HEAPS_DTB_SHA256" ] ||
  die 'candidate DTB differs'
! cmp -s "$work/base/kernel" "$work/candidate/kernel" || die 'kernel did not change'
! cmp -s "$work/base/dtb" "$work/candidate/dtb" || die 'DTB did not change'

[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool size)" = \
  '0 1000000' ] || die 'TA pool size differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-apps-pool size)" = \
  '0 1400000' ] || die 'apps pool size differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-apps-pool alignment)" = \
  '0 400000' ] || die 'apps pool alignment differs'
[ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-apps-pool alloc-ranges)" = \
  '0 80000000 0 80000000' ] || die 'apps pool range differs'
fdtget "$work/candidate/dtb" /reserved-memory/qseecom-apps-pool no-map >/dev/null ||
  die 'apps pool is not no-map'
apps_phandle=$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-apps-pool phandle)
ta_phandle=$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-ta-pool phandle)
[ "$(fdtget -t x "$work/candidate/dtb" /firmware/scm memory-region)" = "$ta_phandle" ] ||
  die 'SCM TA pool differs'
[ "$(fdtget -t x "$work/candidate/dtb" /firmware/scm qcom,qseecom-apps-region)" = "$apps_phandle" ] ||
  die 'SCM apps pool differs'
fdtget "$work/candidate/dtb" /firmware/scm qcom,tzmem-whole-pool-shmbridge >/dev/null ||
  die 'whole-pool SHM marker is absent'
[ "$(fdtget -t x "$work/candidate/dtb" /soc@0/qseecom@c1700000 qseecom_mem)" = "$apps_phandle" ] ||
  die 'stock apps phandle differs'
[ "$(fdtget -t x "$work/candidate/dtb" /soc@0/qseecom@c1700000 qseecom_ta_mem)" = "$ta_phandle" ] ||
  die 'stock TA phandle differs'

printf 'FP6 fingerprint stock-heaps candidate: PASS\n'
printf 'candidate_sha256=%s kernel_sha256=%s dtb_sha256=%s\n' \
  "$FP6_FINGERPRINT_STOCK_HEAPS_BOOT_SHA256" \
  "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_SHA256" \
  "$FP6_FINGERPRINT_STOCK_HEAPS_DTB_SHA256"
printf 'ramdisk_unchanged=true modules_unchanged=true ta_load_attempted=false phone_accessed=false\n'
