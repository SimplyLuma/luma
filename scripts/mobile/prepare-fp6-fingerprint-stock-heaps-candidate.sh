#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an offline FP6 stock-QSEECOM-heaps diagnostic from the physically
# accepted SHM-Bridge boot. The exact ramdisk and boot-header fields remain
# unchanged. This script never contacts a phone or writes a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-stock-heaps-candidate.sh SHMBRIDGE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
bundle=${2:?usage: prepare-fp6-fingerprint-stock-heaps-candidate.sh SHMBRIDGE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
output_dir=${3:?usage: prepare-fp6-fingerprint-stock-heaps-candidate.sh SHMBRIDGE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-stock-heaps.dtso
candidate=$output_dir/boot-fp6-luma-fingerprint-stock-heaps-v8.img
manifest=$bundle/manifest.env

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dtc fdtoverlay fdtget grep install mkbootimg mktemp python3 \
  sha256sum stat unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
for artifact in "$base_boot" "$bundle/Image.gz" "$bundle/config" \
  "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko" "$manifest" "$overlay"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" ] ||
  die 'accepted SHM-Bridge base checksum differs'
[ "$(hash "$overlay")" = "$FP6_FINGERPRINT_STOCK_HEAPS_OVERLAY_SHA256" ] ||
  die 'stock-heaps overlay checksum differs'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'kernel bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"
[ "$KERNEL_RELEASE" = 7.1.2 ] || die 'kernel release differs'
[ "$TZMEM_MODE" = shmbridge ] || die 'kernel is not in SHM-Bridge mode'
[ "$STOCK_HEAPS_ENABLED" = true ] || die 'stock-heaps kernel gate is closed'
[ "$STOCK_HEAPS_PATCHSET_SHA256" = "$FP6_FINGERPRINT_STOCK_HEAPS_PATCHSET_SHA256" ] ||
  die 'stock-heaps patchset differs'
[ "$(hash "$bundle/Image.gz")" = "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_SHA256" ] ||
  die 'stock-heaps kernel checksum differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256" ] ||
  die 'kernel configuration differs'
[ "$(hash "$bundle/focaltech_fp.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module differs'
[ "$(hash "$bundle/qseecomtee.ko")" = "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ] ||
  die 'QSEECOM module differs'

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
overlay_blob=$work/stock-heaps.dtbo
enabled_dtb=$output_dir/milos-fairphone-fp6-fingerprint-stock-heaps-v8.dtb

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
[ "$(hash "$work/base/kernel")" = "$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256" ] ||
  die 'base SHM-Bridge kernel differs'
[ "$(hash "$work/base/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
  die 'base ramdisk differs'
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" ] ||
  die 'base low-32-bit DTB differs'

dtc -@ -I dts -O dtb -o "$overlay_blob" "$overlay"
fdtoverlay -i "$work/base/dtb" -o "$enabled_dtb" "$overlay_blob"

[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool size)" = \
  '0 1400000' ] || die 'apps pool size differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool alignment)" = \
  '0 400000' ] || die 'apps pool alignment differs'
[ "$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool alloc-ranges)" = \
  '0 80000000 0 80000000' ] || die 'apps pool range differs'
fdtget "$enabled_dtb" /reserved-memory/qseecom-apps-pool no-map >/dev/null ||
  die 'apps pool is not no-map'
! fdtget "$enabled_dtb" /reserved-memory/qseecom-apps-pool reusable >/dev/null 2>&1 ||
  die 'apps pool unexpectedly reusable'
apps_phandle=$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-apps-pool phandle)
ta_phandle=$(fdtget -t x "$enabled_dtb" /reserved-memory/qseecom-ta-pool phandle)
[ "$(fdtget -t x "$enabled_dtb" /firmware/scm qcom,qseecom-apps-region)" = "$apps_phandle" ] ||
  die 'SCM apps-region phandle differs'
fdtget "$enabled_dtb" /firmware/scm qcom,tzmem-whole-pool-shmbridge >/dev/null ||
  die 'whole-pool SHM gate is absent'
[ "$(fdtget -t x "$enabled_dtb" /soc@0/qseecom@c1700000 qseecom_mem)" = "$apps_phandle" ] ||
  die 'stock qseecom apps phandle differs'
[ "$(fdtget -t x "$enabled_dtb" /soc@0/qseecom@c1700000 qseecom_ta_mem)" = "$ta_phandle" ] ||
  die 'stock qseecom TA phandle differs'
fdtget "$enabled_dtb" /soc@0/qseecom@c1700000 qcom,appsbl-qseecom-support >/dev/null ||
  die 'AppsBL QSEECOM marker is absent'
fdtget "$enabled_dtb" /soc@0/qseecom@c1700000 qcom,commonlib64-loaded-by-uefi >/dev/null ||
  die 'UEFI commonlib64 marker is absent'

install -m 0644 "$bundle/Image.gz" "$work/base/kernel"
install -m 0644 "$enabled_dtb" "$work/base/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" \
  >"$output_dir/boot-info.txt"
cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
cmp "$enabled_dtb" "$work/candidate/dtb"
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
  die 'candidate exceeds the 96 MiB boot partition'

candidate_sha=$(hash "$candidate")
{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=8\n'
  printf 'SCOPE=fp6-fingerprint-stock-heaps-boot-only\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_SHA256"
  printf 'CONFIG_SHA256=%s\n' "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256"
  printf 'BASE_DTB_SHA256=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256"
  printf 'DTB_SHA256=%s\n' "$(hash "$enabled_dtb")"
  printf 'RAMDISK_SHA256=%s\n' "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256"
  printf 'OVERLAY_SHA256=%s\n' "$FP6_FINGERPRINT_STOCK_HEAPS_OVERLAY_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'KERNEL_CHANGED=true\n'
  printf 'DTB_CHANGED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'MODULE_BINARIES_UNCHANGED=true\n'
  printf 'TA_POOL_SIZE_MBYTES=16\n'
  printf 'APPS_POOL_SIZE_MBYTES=20\n'
  printf 'POOL_ALIGNMENT_MBYTES=4\n'
  printf 'POOLS_BELOW_4G=true\n'
  printf 'POOLS_REUSABLE=false\n'
  printf 'POOLS_NO_MAP=true\n'
  printf 'WHOLE_TA_POOL_SHMBRIDGE=true\n'
  printf 'WHOLE_APPS_POOL_SHMBRIDGE=true\n'
  printf 'APP_REGION_NOTIFICATION=false\n'
  printf 'TRUSTLET_LOAD_ENABLED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$enabled_dtb"

printf 'FP6 fingerprint stock-heaps boot candidate: %s\n' "$candidate"
