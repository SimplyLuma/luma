#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-stock-node-names-candidate.sh BOOT_IMAGE}
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp fdtget mktemp sha256sum stat unpack_bootimg; do
	command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
[ -f "$candidate" ] || die 'candidate is missing'
[ "$FP6_FINGERPRINT_STOCK_NODE_NAMES_BOOT_CANDIDATE_READY" = true ] || die 'release gate closed'
[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_STOCK_NODE_NAMES_BOOT_SHA256" ] || die 'candidate hash differs'
[ "$(stat -c %s "$candidate")" = "$FP6_FINGERPRINT_STOCK_NODE_NAMES_BOOT_SIZE" ] || die 'candidate size differs'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
unpack_bootimg --boot_img "$candidate" --out "$work" >"$work/info.txt"
[ "$(hash "$work/kernel")" = "$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256" ] || die 'kernel differs'
[ "$(hash "$work/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] || die 'ramdisk differs'
[ "$(hash "$work/dtb")" = "$FP6_FINGERPRINT_STOCK_NODE_NAMES_DTB_SHA256" ] || die 'DTB differs'

dtb=$work/dtb
apps=/reserved-memory/qseecom_region
ta=/reserved-memory/qseecom_ta_region
qsee=/soc@0/qseecom@c1700000
[ "$(fdtget "$dtb" /aliases qseecom_mem)" = "$apps" ] || die 'apps alias differs'
[ "$(fdtget "$dtb" /aliases qseecom_ta_mem)" = "$ta" ] || die 'TA alias differs'
[ "$(fdtget "$dtb" /aliases qcom_qseecom)" = "$qsee" ] || die 'QSEE alias differs'
apps_phandle=$(fdtget -t x "$dtb" "$apps" phandle)
ta_phandle=$(fdtget -t x "$dtb" "$ta" phandle)
[ "$(fdtget -t x "$dtb" "$qsee" memory-region)" = "$apps_phandle" ] || die 'QSEE memory-region differs'
[ "$(fdtget -t x "$dtb" "$qsee" qseecom_mem)" = "$apps_phandle" ] || die 'QSEE apps phandle differs'
[ "$(fdtget -t x "$dtb" "$qsee" qseecom_ta_mem)" = "$ta_phandle" ] || die 'QSEE TA phandle differs'
[ "$(fdtget -t x "$dtb" "$apps" size)" = '0 1400000' ] || die 'apps size differs'
[ "$(fdtget -t x "$dtb" "$ta" size)" = '0 1000000' ] || die 'TA size differs'
fdtget "$dtb" "$apps" no-map >/dev/null || die 'apps no-map absent'
fdtget "$dtb" "$ta" no-map >/dev/null || die 'TA no-map absent'
! fdtget "$dtb" "$apps" reusable >/dev/null 2>&1 || die 'apps unexpectedly reusable'
! fdtget "$dtb" "$ta" reusable >/dev/null 2>&1 || die 'TA unexpectedly reusable'
fdtget "$dtb" "$qsee" qcom,appsbl-qseecom-support >/dev/null || die 'AppsBL marker absent'
fdtget "$dtb" "$qsee" qcom,commonlib64-loaded-by-uefi >/dev/null || die 'commonlib64 marker absent'
fdtget "$dtb" /firmware/scm/qseecom-tee-heaps luma,dedicated-heaps >/dev/null || die 'dedicated heaps absent'
fdtget "$dtb" /firmware/scm luma,qsee-log-diagnostic >/dev/null || die 'QSEE log absent'
! fdtget "$dtb" /firmware/scm memory-region >/dev/null 2>&1 || die 'global SCM pool reintroduced'

printf 'FP6 fingerprint stock-node-name candidate verified: %s\n' "$candidate"
