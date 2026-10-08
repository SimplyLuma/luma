#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only structural verification for an FP6 QSEECom-owned-heaps boot image.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-dedicated-heaps-candidate.sh CANDIDATE SHMBRIDGE_BOOT KERNEL_BUNDLE}
base_boot=${2:?usage: verify-fp6-fingerprint-dedicated-heaps-candidate.sh CANDIDATE SHMBRIDGE_BOOT KERNEL_BUNDLE}
bundle=${3:?usage: verify-fp6-fingerprint-dedicated-heaps-candidate.sh CANDIDATE SHMBRIDGE_BOOT KERNEL_BUNDLE}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp fdtget mktemp sha256sum unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
for artifact in "$candidate" "$base_boot" "$bundle/Image.gz" \
  "$bundle/config" "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_CANDIDATE_READY" = true ] ||
  die 'dedicated-heaps boot release gate is closed'
[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_SHA256" ] ||
  die 'candidate checksum differs'
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" ] ||
  die 'SHM-Bridge base checksum differs'
[ "$(hash "$bundle/Image.gz")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_KERNEL_SHA256" ] ||
  die 'dedicated-heaps kernel differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_CONFIG_SHA256" ] ||
  die 'kernel configuration differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module differs'
[ "$(hash "$bundle/qseecomtee.ko")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOMTEE_KO_SHA256" ] ||
  die 'dedicated-heaps QSEECOM module differs'
[ "$FP6_FINGERPRINT_DEDICATED_HEAPS_QSEECOMTEE_KO_SHA256" != \
  "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ] ||
  die 'dedicated-heaps QSEECOM module unexpectedly matches the old module'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/candidate" "$work/base"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$work/candidate.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" >"$work/base.txt"

cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
[ "$(hash "$work/candidate/dtb")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_DTB_SHA256" ] ||
  die 'candidate DTB differs'
! cmp -s "$work/base/kernel" "$work/candidate/kernel" || die 'kernel did not change'
! cmp -s "$work/base/dtb" "$work/candidate/dtb" || die 'DTB did not change'

dtb=$work/candidate/dtb
[ "$(fdtget -t x "$dtb" /reserved-memory/qseecom-ta-pool size)" = \
  '0 1000000' ] || die 'TA pool size differs'
[ "$(fdtget -t x "$dtb" /reserved-memory/qseecom-apps-pool size)" = \
  '0 1400000' ] || die 'apps pool size differs'
[ "$(fdtget -t x "$dtb" /reserved-memory/qseecom-apps-pool alignment)" = \
  '0 400000' ] || die 'apps pool alignment differs'
[ "$(fdtget -t x "$dtb" /reserved-memory/qseecom-apps-pool alloc-ranges)" = \
  '0 80000000 0 80000000' ] || die 'apps pool range differs'
fdtget "$dtb" /reserved-memory/qseecom-apps-pool no-map >/dev/null ||
  die 'apps pool is not no-map'
! fdtget "$dtb" /reserved-memory/qseecom-apps-pool reusable >/dev/null 2>&1 ||
  die 'apps pool unexpectedly reusable'
ta_phandle=$(fdtget -t x "$dtb" /reserved-memory/qseecom-ta-pool phandle)
apps_phandle=$(fdtget -t x "$dtb" /reserved-memory/qseecom-apps-pool phandle)
child=/firmware/scm/qseecom-tee-heaps
! fdtget "$dtb" /firmware/scm memory-region >/dev/null 2>&1 ||
  die 'global SCM still owns a reserved pool'
! fdtget "$dtb" /firmware/scm qcom,qseecom-apps-region >/dev/null 2>&1 ||
  die 'global SCM unexpectedly owns the apps pool'
! fdtget "$dtb" /firmware/scm qcom,tzmem-whole-pool-shmbridge >/dev/null 2>&1 ||
  die 'global whole-pool SHM mode is unexpectedly enabled'
[ "$(fdtget "$dtb" "$child" compatible)" = 'luma,qseecom-tee-heaps' ] ||
  die 'QSEECom heap child compatible differs'
fdtget "$dtb" "$child" luma,dedicated-heaps >/dev/null ||
  die 'dedicated-heaps opt-in is absent'
[ "$(fdtget -t x "$dtb" "$child" memory-region)" = \
  "$ta_phandle $apps_phandle" ] || die 'QSEECom child pool ownership differs'
[ "$(fdtget "$dtb" "$child" memory-region-names)" = 'ta apps' ] ||
  die 'QSEECom child pool names differ'

printf 'FP6 fingerprint dedicated-heaps candidate: PASS\n'
printf 'candidate_sha256=%s kernel_sha256=%s dtb_sha256=%s\n' \
  "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_SHA256" \
  "$FP6_FINGERPRINT_DEDICATED_HEAPS_KERNEL_SHA256" \
  "$FP6_FINGERPRINT_DEDICATED_HEAPS_DTB_SHA256"
printf 'ramdisk_unchanged=true focaltech_module_unchanged=true qseecom_module_changed=true global_scm_pool=false ta_load_attempted=false phone_accessed=false\n'
