#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose the DT-only FP6 AppsBL QSEECom-contract successor from the physically
# accepted QSEE-log v11 boot. This never contacts a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-appsbl-contract-candidate.sh V11_BOOT OUTPUT_DIR}
output_dir=${2:?missing output directory}
overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-appsbl-contract.dtso
candidate=$output_dir/boot-fp6-luma-fingerprint-appsbl-contract-v12.img

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dtc fdtoverlay fdtget install mkbootimg mktemp python3 \
	sha256sum stat unpack_bootimg; do
	command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
for artifact in "$base_boot" "$overlay"; do
	[ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_QSEELOG_BOOT_SHA256" ] ||
	die 'accepted v11 boot differs'
[ "$(hash "$overlay")" = "$FP6_FINGERPRINT_APPSBL_CONTRACT_OVERLAY_SHA256" ] ||
	die 'AppsBL contract overlay differs'

run_mkbootimg_args0() {
	local args_file=$1 output=$2
	python3 - "$args_file" "$output" <<'PY'
import os
import subprocess
import sys

args_file, output = sys.argv[1:]
with open(args_file, "rb") as stream:
    args = stream.read().split(b"\0")
if args and args[-1] == b"":
    args.pop()
subprocess.run(
    ["mkbootimg", "--output", output, *(os.fsdecode(arg) for arg in args)],
    check=True,
)
PY
}

mkdir -p "$output_dir"
work=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/base" "$work/base-verify" "$work/roundtrip" "$work/candidate"
args_file=$work/mkbootimg.args0
roundtrip=$work/base-roundtrip.img
overlay_blob=$work/appsbl-contract.dtbo
enabled_dtb=$output_dir/milos-fairphone-fp6-fingerprint-appsbl-contract-v12.dtb

unpack_bootimg --boot_img "$base_boot" --out "$work/base-verify" \
	>"$work/base-info.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" \
	--format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
unpack_bootimg --boot_img "$roundtrip" --out "$work/roundtrip" \
	>"$work/roundtrip-info.txt"
cmp "$work/base-info.txt" "$work/roundtrip-info.txt"
cmp "$work/base-verify/kernel" "$work/roundtrip/kernel"
cmp "$work/base-verify/ramdisk" "$work/roundtrip/ramdisk"
cmp "$work/base-verify/dtb" "$work/roundtrip/dtb"
[ "$(hash "$work/base/kernel")" = "$FP6_FINGERPRINT_QSEELOG_KERNEL_SHA256" ] ||
	die 'base kernel differs'
[ "$(hash "$work/base/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
	die 'base ramdisk differs'
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_QSEELOG_DTB_SHA256" ] ||
	die 'base DTB differs'

dtc -@ -I dts -O dtb -o "$overlay_blob" "$overlay"
fdtoverlay -i "$work/base/dtb" -o "$enabled_dtb" "$overlay_blob"
install -m 0644 "$enabled_dtb" "$work/base/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" \
	>"$output_dir/boot-info.txt"

cmp "$work/base/kernel" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
cmp "$enabled_dtb" "$work/candidate/dtb"
dtb=$work/candidate/dtb
apps=/reserved-memory/qseecom-apps-pool
ta=/reserved-memory/qseecom-ta-pool
qsee=/soc@0/qseecom@c1700000
[ "$(fdtget "$dtb" /aliases qseecom_mem)" = "$apps" ] || die 'apps alias differs'
[ "$(fdtget "$dtb" /aliases qseecom_ta_mem)" = "$ta" ] || die 'TA alias differs'
[ "$(fdtget "$dtb" /aliases qcom_qseecom)" = "$qsee" ] || die 'QSEE alias differs'
apps_phandle=$(fdtget -t x "$dtb" "$apps" phandle)
ta_phandle=$(fdtget -t x "$dtb" "$ta" phandle)
[ "$(fdtget -t x "$dtb" "$qsee" memory-region)" = "$apps_phandle" ] || die 'QSEE memory-region differs'
[ "$(fdtget -t x "$dtb" "$qsee" qseecom_mem)" = "$apps_phandle" ] || die 'QSEE apps phandle differs'
[ "$(fdtget -t x "$dtb" "$qsee" qseecom_ta_mem)" = "$ta_phandle" ] || die 'QSEE TA phandle differs'
fdtget "$dtb" "$qsee" qcom,appsbl-qseecom-support >/dev/null || die 'AppsBL marker absent'
fdtget "$dtb" "$qsee" qcom,commonlib64-loaded-by-uefi >/dev/null || die 'commonlib64 marker absent'
fdtget "$dtb" /firmware/scm/qseecom-tee-heaps luma,dedicated-heaps >/dev/null || die 'dedicated heaps absent'
fdtget "$dtb" /firmware/scm luma,qsee-log-diagnostic >/dev/null || die 'QSEE log absent'
! fdtget "$dtb" /firmware/scm memory-region >/dev/null 2>&1 || die 'global SCM pool reintroduced'
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
	die 'candidate exceeds the 96 MiB boot partition'

{
	printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=12\n'
	printf 'SCOPE=fp6-fingerprint-appsbl-contract\n'
	printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_QSEELOG_BOOT_SHA256"
	printf 'KERNEL_SHA256=%s\n' "$(hash "$work/candidate/kernel")"
	printf 'DTB_SHA256=%s\n' "$(hash "$enabled_dtb")"
	printf 'RAMDISK_SHA256=%s\n' "$(hash "$work/candidate/ramdisk")"
	printf 'OVERLAY_SHA256=%s\n' "$FP6_FINGERPRINT_APPSBL_CONTRACT_OVERLAY_SHA256"
	printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
	printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
	printf 'HEADER_ROUNDTRIP_EXACT=true\n'
	printf 'KERNEL_UNCHANGED=true\n'
	printf 'RAMDISK_UNCHANGED=true\n'
	printf 'DEDICATED_HEAPS_RETAINED=true\n'
	printf 'QSEELOG_RETAINED=true\n'
	printf 'STOCK_APPSBL_CONTRACT_RESTORED=true\n'
	printf 'TRUSTLET_LOAD_ENABLED=false\n'
	printf 'TA_LOAD_ATTEMPTED=false\n'
	printf 'ENROLLMENT_ENABLED=false\n'
	printf 'AUTHENTICATION_ENABLED=false\n'
	printf 'PHONE_ACCESSED=false\n'
	printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$enabled_dtb"

printf 'FP6 fingerprint AppsBL-contract candidate: %s\n' "$candidate"
