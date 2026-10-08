#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose one-variable boot-only successors to the rejected stock-heaps v8
# image. kernel-only uses the stock-heaps kernel with the accepted v7 DTB;
# dt-only uses the accepted v7 kernel with the stock-heaps DTB. The ramdisk,
# modules, and boot-header fields stay exact. This script never contacts a
# phone or writes a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

mode=${1:?usage: prepare-fp6-fingerprint-stock-heaps-isolation-candidate.sh MODE SHMBRIDGE_BOOT STOCK_KERNEL_BUNDLE SHMBRIDGE_KERNEL_BUNDLE OUTPUT_DIR}
base_boot=${2:?missing accepted SHM-Bridge boot}
stock_bundle=${3:?missing stock-heaps kernel bundle}
shm_bundle=${4:?missing accepted SHM-Bridge kernel bundle}
output_dir=${5:?missing output directory}
overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-stock-heaps.dtso

case $mode in
  kernel-only|dt-only) ;;
  *) printf 'error: mode must be kernel-only or dt-only\n' >&2; exit 1 ;;
esac

candidate=$output_dir/boot-fp6-luma-fingerprint-stock-heaps-$mode-v9.img
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dtc fdtoverlay fdtget install mkbootimg mktemp python3 \
  sha256sum stat unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
for artifact in "$base_boot" "$stock_bundle/Image.gz" "$stock_bundle/config" \
  "$stock_bundle/focaltech_fp.ko" "$stock_bundle/qseecomtee.ko" \
  "$stock_bundle/manifest.env" "$shm_bundle/Image.gz" "$shm_bundle/config" \
  "$shm_bundle/focaltech_fp.ko" "$shm_bundle/qseecomtee.ko" \
  "$shm_bundle/manifest.env" "$overlay"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256" ] ||
  die 'accepted SHM-Bridge boot differs'
[ "$(hash "$stock_bundle/Image.gz")" = "$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_SHA256" ] ||
  die 'stock-heaps kernel differs'
[ "$(hash "$shm_bundle/Image.gz")" = "$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256" ] ||
  die 'accepted SHM-Bridge kernel differs'
[ "$(hash "$stock_bundle/config")" = "$FP6_FINGERPRINT_STOCK_HEAPS_CONFIG_SHA256" ] ||
  die 'stock-heaps configuration differs'
[ "$(hash "$shm_bundle/config")" = "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256" ] ||
  die 'SHM-Bridge configuration differs'
cmp "$stock_bundle/config" "$shm_bundle/config"
cmp "$stock_bundle/focaltech_fp.ko" "$shm_bundle/focaltech_fp.ko"
cmp "$stock_bundle/qseecomtee.ko" "$shm_bundle/qseecomtee.ko"
[ "$(hash "$overlay")" = "$FP6_FINGERPRINT_STOCK_HEAPS_OVERLAY_SHA256" ] ||
  die 'stock-heaps overlay differs'

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

unpack_bootimg --boot_img "$base_boot" --out "$work/base-verify" >"$work/base-info.txt"
unpack_bootimg --boot_img "$base_boot" --out "$work/base" --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
unpack_bootimg --boot_img "$roundtrip" --out "$work/roundtrip" >"$work/roundtrip-info.txt"
cmp "$work/base-info.txt" "$work/roundtrip-info.txt"
cmp "$work/base-verify/kernel" "$work/roundtrip/kernel"
cmp "$work/base-verify/ramdisk" "$work/roundtrip/ramdisk"
cmp "$work/base-verify/dtb" "$work/roundtrip/dtb"
[ "$(hash "$work/base/kernel")" = "$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256" ] ||
  die 'base kernel differs'
[ "$(hash "$work/base/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
  die 'base ramdisk differs'
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" ] ||
  die 'base DTB differs'

case $mode in
  kernel-only)
    install -m 0644 "$stock_bundle/Image.gz" "$work/base/kernel"
    selected_kernel_sha=$FP6_FINGERPRINT_STOCK_HEAPS_KERNEL_SHA256
    selected_dtb_sha=$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256
    kernel_changed=true
    dtb_changed=false
    ;;
  dt-only)
    overlay_blob=$work/stock-heaps.dtbo
    enabled_dtb=$output_dir/milos-fairphone-fp6-fingerprint-stock-heaps-dt-only-v9.dtb
    dtc -@ -I dts -O dtb -o "$overlay_blob" "$overlay"
    fdtoverlay -i "$work/base/dtb" -o "$enabled_dtb" "$overlay_blob"
    install -m 0644 "$enabled_dtb" "$work/base/dtb"
    selected_kernel_sha=$FP6_FINGERPRINT_SHMBRIDGE_KERNEL_SHA256
    selected_dtb_sha=$(hash "$enabled_dtb")
    kernel_changed=false
    dtb_changed=true
    ;;
esac

run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" >"$output_dir/boot-info.txt"
[ "$(hash "$work/candidate/kernel")" = "$selected_kernel_sha" ] || die 'candidate kernel differs'
[ "$(hash "$work/candidate/dtb")" = "$selected_dtb_sha" ] || die 'candidate DTB differs'
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
  die 'candidate exceeds the 96 MiB boot partition'

if [ "$mode" = kernel-only ]; then
  ! fdtget "$work/candidate/dtb" /reserved-memory/qseecom-apps-pool size >/dev/null 2>&1 ||
    die 'kernel-only candidate unexpectedly contains apps pool'
  ! fdtget "$work/candidate/dtb" /firmware/scm qcom,tzmem-whole-pool-shmbridge >/dev/null 2>&1 ||
    die 'kernel-only candidate unexpectedly enables whole-pool mode'
else
  [ "$(fdtget -t x "$work/candidate/dtb" /reserved-memory/qseecom-apps-pool size)" = '0 1400000' ] ||
    die 'DT-only apps pool size differs'
  fdtget "$work/candidate/dtb" /firmware/scm qcom,tzmem-whole-pool-shmbridge >/dev/null ||
    die 'DT-only whole-pool property is absent'
fi

candidate_sha=$(hash "$candidate")
{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=9\n'
  printf 'SCOPE=fp6-fingerprint-stock-heaps-isolation-%s\n' "$mode"
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_SHMBRIDGE_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$selected_kernel_sha"
  printf 'CONFIG_SHA256=%s\n' "$FP6_FINGERPRINT_SHMBRIDGE_CONFIG_SHA256"
  printf 'DTB_SHA256=%s\n' "$selected_dtb_sha"
  printf 'RAMDISK_SHA256=%s\n' "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'KERNEL_CHANGED=%s\n' "$kernel_changed"
  printf 'DTB_CHANGED=%s\n' "$dtb_changed"
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'MODULE_BINARIES_UNCHANGED=true\n'
  printf 'SINGLE_VARIABLE_ISOLATION=true\n'
  printf 'TRUSTLET_LOAD_ENABLED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env"

printf 'FP6 fingerprint stock-heaps %s isolation candidate: %s\n' "$mode" "$candidate"
