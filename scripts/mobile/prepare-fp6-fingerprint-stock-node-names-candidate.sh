#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose the DT-only FP6 stock-node-name successor from the physically
# accepted AppsBL-contract v12 boot. This never contacts a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-stock-node-names-candidate.sh V12_BOOT OUTPUT_DIR}
output_dir=${2:?missing output directory}
candidate=$output_dir/boot-fp6-luma-fingerprint-stock-node-names-v13.img

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dtc fdtget install mkbootimg mktemp python3 sha256sum stat unpack_bootimg; do
	command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
[ -f "$base_boot" ] || die "required artifact is missing: $base_boot"
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_APPSBL_CONTRACT_BOOT_SHA256" ] ||
	die 'accepted v12 boot differs'

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
renamed_dtb=$output_dir/milos-fairphone-fp6-fingerprint-stock-node-names-v13.dtb

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
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_APPSBL_CONTRACT_DTB_SHA256" ] ||
	die 'base DTB differs'

# Device-tree overlays cannot rename an existing node. Decompile the exact
# accepted DTB, replace only the two stock-visible path components, and prove
# below that reversing those two substitutions restores identical normalized
# DTS. This keeps every property, phandle, and value unchanged.
dtc -I dtb -O dts -o "$work/base.dts" "$work/base/dtb" 2>"$work/base-dtc.warn"
python3 - "$work/base.dts" "$work/renamed.dts" <<'PY'
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
text = source.read_text()
replacements = {
    "qseecom-apps-pool": "qseecom_region",
    "qseecom-ta-pool": "qseecom_ta_region",
}
expected = {
    "qseecom-apps-pool": 3,
    "qseecom-ta-pool": 3,
}
for old, new in replacements.items():
    count = text.count(old)
    if count != expected[old]:
        raise SystemExit(f"unexpected {old!r} occurrence count: {count}")
    text = text.replace(old, new)
output.write_text(text)
PY
dtc -@ -I dts -O dtb -o "$renamed_dtb" "$work/renamed.dts" \
	2>"$work/renamed-dtc.warn"
dtc -I dtb -O dts -o "$work/renamed-normalized.dts" "$renamed_dtb" \
	2>"$work/renamed-normalized-dtc.warn"
python3 - "$work/renamed-normalized.dts" "$work/reverted-normalized.dts" <<'PY'
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
text = source.read_text()
text = text.replace("qseecom_region", "qseecom-apps-pool")
text = text.replace("qseecom_ta_region", "qseecom-ta-pool")
output.write_text(text)
PY
cmp "$work/base.dts" "$work/reverted-normalized.dts"

install -m 0644 "$renamed_dtb" "$work/base/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" \
	>"$output_dir/boot-info.txt"

cmp "$work/base/kernel" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
cmp "$renamed_dtb" "$work/candidate/dtb"
dtb=$work/candidate/dtb
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
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
	die 'candidate exceeds the 96 MiB boot partition'

{
	printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=13\n'
	printf 'SCOPE=fp6-fingerprint-stock-node-names\n'
	printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_APPSBL_CONTRACT_BOOT_SHA256"
	printf 'KERNEL_SHA256=%s\n' "$(hash "$work/candidate/kernel")"
	printf 'DTB_SHA256=%s\n' "$(hash "$renamed_dtb")"
	printf 'RAMDISK_SHA256=%s\n' "$(hash "$work/candidate/ramdisk")"
	printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
	printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
	printf 'HEADER_ROUNDTRIP_EXACT=true\n'
	printf 'NORMALIZED_DTS_DIFF_LIMITED_TO_NODE_NAMES=true\n'
	printf 'KERNEL_UNCHANGED=true\n'
	printf 'RAMDISK_UNCHANGED=true\n'
	printf 'POOL_PROPERTIES_UNCHANGED=true\n'
	printf 'DEDICATED_HEAPS_RETAINED=true\n'
	printf 'QSEELOG_RETAINED=true\n'
	printf 'STOCK_NODE_NAMES_RESTORED=true\n'
	printf 'TRUSTLET_LOAD_ENABLED=false\n'
	printf 'TA_LOAD_ATTEMPTED=false\n'
	printf 'ENROLLMENT_ENABLED=false\n'
	printf 'AUTHENTICATION_ENABLED=false\n'
	printf 'PHONE_ACCESSED=false\n'
	printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$renamed_dtb"

printf 'FP6 fingerprint stock-node-name candidate: %s\n' "$candidate"
