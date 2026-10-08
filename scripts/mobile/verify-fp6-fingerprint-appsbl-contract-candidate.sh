#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Read-only structural verification for the DT-only FP6 AppsBL-contract image.

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

candidate=${1:?usage: verify-fp6-fingerprint-appsbl-contract-candidate.sh CANDIDATE V11_BOOT}
base_boot=${2:?missing v11 base boot}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp fdtget mktemp sha256sum stat unpack_bootimg; do
	command -v "$tool" >/dev/null 2>&1 || die "missing verification tool: $tool"
done
[ -f "$candidate" ] || die 'candidate is missing'
[ -f "$base_boot" ] || die 'v11 base is missing'
[ "$FP6_FINGERPRINT_APPSBL_CONTRACT_BOOT_CANDIDATE_READY" = true ] || die 'release gate closed'
[ "$(hash "$candidate")" = "$FP6_FINGERPRINT_APPSBL_CONTRACT_BOOT_SHA256" ] || die 'candidate hash differs'
[ "$(stat -c %s "$candidate")" = "$FP6_FINGERPRINT_APPSBL_CONTRACT_BOOT_SIZE" ] || die 'candidate size differs'
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_QSEELOG_BOOT_SHA256" ] || die 'v11 base differs'

work=$(mktemp -d)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/candidate" "$work/base"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >/dev/null
unpack_bootimg --boot_img "$base_boot" --out "$work/base" >/dev/null
cmp "$work/base/kernel" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
! cmp -s "$work/base/dtb" "$work/candidate/dtb" || die 'DTB did not change'
[ "$(hash "$work/candidate/kernel")" = "$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256" ] || die 'kernel differs'
[ "$(hash "$work/candidate/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] || die 'ramdisk differs'
[ "$(hash "$work/candidate/dtb")" = "$FP6_FINGERPRINT_APPSBL_CONTRACT_DTB_SHA256" ] || die 'DTB differs'

dtb=$work/candidate/dtb
apps=/reserved-memory/qseecom-apps-pool
ta=/reserved-memory/qseecom-ta-pool
qsee=/soc@0/qseecom@c1700000
[ "$(fdtget "$dtb" /aliases qseecom_mem)" = "$apps" ] || die 'apps alias differs'
[ "$(fdtget "$dtb" /aliases qseecom_ta_mem)" = "$ta" ] || die 'TA alias differs'
[ "$(fdtget "$dtb" /aliases qcom_qseecom)" = "$qsee" ] || die 'QSEE alias differs'
apps_phandle=$(fdtget -t x "$dtb" "$apps" phandle)
ta_phandle=$(fdtget -t x "$dtb" "$ta" phandle)
[ "$(fdtget -t x "$dtb" "$qsee" memory-region)" = "$apps_phandle" ] || die 'memory-region differs'
[ "$(fdtget -t x "$dtb" "$qsee" qseecom_mem)" = "$apps_phandle" ] || die 'apps phandle differs'
[ "$(fdtget -t x "$dtb" "$qsee" qseecom_ta_mem)" = "$ta_phandle" ] || die 'TA phandle differs'
fdtget "$dtb" "$qsee" qcom,appsbl-qseecom-support >/dev/null || die 'AppsBL marker absent'
fdtget "$dtb" "$qsee" qcom,commonlib64-loaded-by-uefi >/dev/null || die 'commonlib marker absent'
fdtget "$dtb" /firmware/scm/qseecom-tee-heaps luma,dedicated-heaps >/dev/null || die 'dedicated heaps absent'
fdtget "$dtb" /firmware/scm luma,qsee-log-diagnostic >/dev/null || die 'QSEE log absent'
! fdtget "$dtb" /firmware/scm memory-region >/dev/null 2>&1 || die 'global SCM pool reintroduced'

printf 'FP6 fingerprint AppsBL-contract candidate: PASS\n'
printf 'candidate_sha256=%s dtb_sha256=%s kernel_unchanged=true ramdisk_unchanged=true\n' \
	"$FP6_FINGERPRINT_APPSBL_CONTRACT_BOOT_SHA256" \
	"$FP6_FINGERPRINT_APPSBL_CONTRACT_DTB_SHA256"
printf 'stock_aliases=true stock_qseecom_node=true dedicated_heaps=true qsee_log=true trustlet_loaded=false phone_accessed=false\n'
