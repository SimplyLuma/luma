#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only structural verification for the opt-in FP6 QSEE-log diagnostic
# boot image. This script never contacts a phone.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-qseelog-candidate.sh CANDIDATE V10_BOOT KERNEL_BUNDLE}
base_boot=${2:?usage: verify-fp6-fingerprint-qseelog-candidate.sh CANDIDATE V10_BOOT KERNEL_BUNDLE}
bundle=${3:?usage: verify-fp6-fingerprint-qseelog-candidate.sh CANDIDATE V10_BOOT KERNEL_BUNDLE}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp fdtget mktemp sha256sum stat unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
for artifact in "$candidate" "$base_boot" "$bundle/Image.gz" \
  "$bundle/config" "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ "$FP6_FINGERPRINT_QSEELOG_BUILD_READY" = true ] ||
  die 'QSEE-log kernel release gate is closed'
[ "$FP6_FINGERPRINT_QSEELOG_BOOT_CANDIDATE_READY" = true ] ||
  die 'QSEE-log boot release gate is closed'
[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_QSEELOG_BOOT_SHA256" ] ||
  die 'candidate checksum differs'
[ "$(stat -c %s "$candidate")" = "$FP6_FINGERPRINT_QSEELOG_BOOT_SIZE" ] ||
  die 'candidate size differs'
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_SHA256" ] ||
  die 'accepted v10 base checksum differs'
[ "$(hash "$bundle/Image.gz")" = "$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256" ] ||
  die 'QSEE-log kernel differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_QSEELOG_CONFIG_SHA256" ] ||
  die 'kernel configuration differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module differs'
[ "$(hash "$bundle/qseecomtee.ko")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOMTEE_KO_SHA256" ] ||
  die 'QSEECom module differs'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/candidate" "$work/base"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$work/candidate.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" >"$work/base.txt"

cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
[ "$(hash "$work/candidate/dtb")" = "$FP6_FINGERPRINT_QSEELOG_DTB_SHA256" ] ||
  die 'candidate DTB differs'
! cmp -s "$work/base/kernel" "$work/candidate/kernel" || die 'kernel did not change'
! cmp -s "$work/base/dtb" "$work/candidate/dtb" || die 'DTB did not change'

dtb=$work/candidate/dtb
fdtget "$dtb" /firmware/scm luma,qsee-log-diagnostic >/dev/null ||
  die 'QSEE-log diagnostic opt-in is absent'
[ "$(fdtget -t x "$dtb" /reserved-memory/qseecom-ta-pool size)" = \
  '0 1000000' ] || die 'TA pool size differs'
[ "$(fdtget -t x "$dtb" /reserved-memory/qseecom-apps-pool size)" = \
  '0 1400000' ] || die 'apps pool size differs'
ta_phandle=$(fdtget -t x "$dtb" /reserved-memory/qseecom-ta-pool phandle)
apps_phandle=$(fdtget -t x "$dtb" /reserved-memory/qseecom-apps-pool phandle)
child=/firmware/scm/qseecom-tee-heaps
[ "$(fdtget "$dtb" "$child" compatible)" = 'luma,qseecom-tee-heaps' ] ||
  die 'QSEECom heap child compatible differs'
[ "$(fdtget -t x "$dtb" "$child" memory-region)" = \
  "$ta_phandle $apps_phandle" ] || die 'QSEECom child pool ownership differs'
[ "$(fdtget "$dtb" "$child" memory-region-names)" = 'ta apps' ] ||
  die 'QSEECom child pool names differ'
! fdtget "$dtb" /firmware/scm memory-region >/dev/null 2>&1 ||
  die 'global SCM unexpectedly owns a reserved pool'
! fdtget "$dtb" /firmware/scm qcom,tzmem-whole-pool-shmbridge >/dev/null 2>&1 ||
  die 'global whole-pool SHM mode is unexpectedly enabled'

printf 'FP6 fingerprint QSEE-log candidate: PASS\n'
printf 'candidate_sha256=%s kernel_sha256=%s dtb_sha256=%s\n' \
  "$FP6_FINGERPRINT_QSEELOG_BOOT_SHA256" \
  "$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256" \
  "$FP6_FINGERPRINT_QSEELOG_DTB_SHA256"
printf 'ramdisk_unchanged=true modules_unchanged=true qsee_log_opt_in=true trustlet_loaded=false phone_accessed=false\n'
