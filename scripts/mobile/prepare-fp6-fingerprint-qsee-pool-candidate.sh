#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an offline FP6 fingerprint QSEE-pool image from the physically
# accepted ELF64-loader boot. Only the DTB changes: the exact kernel, ramdisk,
# parsed boot-header fields, and installed module ABI remain untouched. This
# script cannot contact a phone or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-qsee-pool-candidate.sh ELF64_BOOT OUTPUT_DIR}
output_dir=${2:?usage: prepare-fp6-fingerprint-qsee-pool-candidate.sh ELF64_BOOT OUTPUT_DIR}
overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-pool.dtso
candidate=$output_dir/boot-fp6-luma-fingerprint-qsee-pool-low32-v6.img

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dtc fdtoverlay fdtget install mkbootimg mktemp python3 \
  sha256sum stat unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
[ -f "$base_boot" ] || die "ELF64-loader base is missing: $base_boot"
[ -f "$overlay" ] || die "QSEE pool overlay is missing: $overlay"
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256" ] ||
  die 'physically accepted ELF64-loader boot checksum differs'
[ "$(hash "$overlay")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_OVERLAY_SHA256" ] ||
  die 'QSEE pool overlay checksum differs'
[ "$FP6_FINGERPRINT_QSEE_POOL_KERNEL_ABI_UNCHANGED" = true ] ||
  die 'kernel ABI preservation gate is closed'

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
overlay_blob=$work/qsee-pool.dtbo
enabled_dtb=$output_dir/milos-fairphone-fp6-fingerprint-qsee-pool-low32-v6.dtb

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
[ "$(hash "$work/base/kernel")" = "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256" ] ||
  die 'ELF64-loader kernel checksum differs'
[ "$(hash "$work/base/ramdisk")" = "$FP6_FINGERPRINT_MDT_ELF64_RAMDISK_SHA256" ] ||
  die 'ELF64-loader ramdisk checksum differs'
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_MDT_ELF64_DTB_SHA256" ] ||
  die 'ELF64-loader DTB checksum differs'

dtc -@ -I dts -O dtb -o "$overlay_blob" "$overlay"
fdtoverlay -i "$work/base/dtb" -o "$enabled_dtb" "$overlay_blob"

[ "$(fdtget "$enabled_dtb" /reserved-memory/qseecom-ta-pool compatible)" = \
  shared-dma-pool ] || die 'QSEE pool compatible differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-ta-pool size)" = \
  '0 1000000' ] || die 'QSEE pool size differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-ta-pool alignment)" = \
  '0 400000' ] || die 'QSEE pool alignment differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-ta-pool alloc-ranges)" = \
  '0 80000000 0 80000000' ] || die 'QSEE pool allocation range differs'
fdtget "$enabled_dtb" /reserved-memory/qseecom-ta-pool no-map >/dev/null ||
  die 'QSEE pool is not marked no-map'
pool_phandle=$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-ta-pool phandle)
[ "$(fdtget -t x "$enabled_dtb" /firmware/scm memory-region)" = "$pool_phandle" ] ||
  die 'SCM memory-region does not reference the QSEE pool'

install -m 0644 "$enabled_dtb" "$work/base/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" \
  >"$output_dir/boot-info.txt"
cmp "$work/base/kernel" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
cmp "$enabled_dtb" "$work/candidate/dtb"
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
  die 'candidate exceeds the 96 MiB boot partition'

candidate_sha=$(hash "$candidate")
{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=6\n'
  printf 'SCOPE=fp6-fingerprint-qsee-pool-low32\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_MDT_ELF64_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256"
  printf 'BASE_DTB_SHA256=%s\n' "$FP6_FINGERPRINT_MDT_ELF64_DTB_SHA256"
  printf 'DTB_SHA256=%s\n' "$(hash "$enabled_dtb")"
  printf 'RAMDISK_SHA256=%s\n' "$FP6_FINGERPRINT_MDT_ELF64_RAMDISK_SHA256"
  printf 'OVERLAY_SHA256=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_LOW32_OVERLAY_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'BASE_PARTITION_SLACK_OMITTED=true\n'
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'MODULE_ABI_UNCHANGED=true\n'
  printf 'DTB_CHANGED=true\n'
  printf 'QSEE_POOL_SIZE_MBYTES=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_SIZE_MBYTES"
  printf 'QSEE_POOL_ALIGNMENT_MBYTES=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_ALIGNMENT_MBYTES"
  printf 'QSEE_POOL_ALLOC_RANGE_START=0x80000000\n'
  printf 'QSEE_POOL_ALLOC_RANGE_SIZE=0x80000000\n'
  printf 'QSEE_POOL_REUSABLE=false\n'
  printf 'QSEE_POOL_NO_MAP=true\n'
  printf 'QSEE_POOL_OWNER=qcom-scm\n'
  printf 'TRUSTLET_LOAD_ENABLED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$enabled_dtb"

printf 'FP6 fingerprint low-32-bit QSEE-pool boot candidate: %s\n' "$candidate"
