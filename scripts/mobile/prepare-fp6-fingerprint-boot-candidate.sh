#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an offline FP6 fingerprint ELF64-loader boot image from the exact,
# physically accepted listener-v2 boot and the validated kernel bundle. Only
# the kernel changes; the accepted DTB, ramdisk, and boot-header fields are
# preserved. This script cannot contact a phone or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-boot-candidate.sh BASE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
bundle=${2:?usage: prepare-fp6-fingerprint-boot-candidate.sh BASE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
output_dir=${3:?usage: prepare-fp6-fingerprint-boot-candidate.sh BASE_BOOT KERNEL_BUNDLE OUTPUT_DIR}
candidate=$output_dir/boot-fp6-luma-fingerprint-loader-elf64-v3.img
bundle_manifest=$bundle/manifest.env
kernel=$bundle/Image.gz
module_focal=$bundle/focaltech_fp.ko
module_qsee=$bundle/qseecomtee.ko

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp cut file grep install mkbootimg \
  mktemp python3 sha256sum stat unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing boot-image tool: $tool"
done

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

for artifact in "$base_boot" "$bundle_manifest" "$kernel" "$module_focal" \
  "$module_qsee"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_LISTENER_V2_BOOT_SHA256" ] ||
  die 'physically accepted listener-v2 boot checksum differs'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$bundle_manifest"; then
  die 'fingerprint bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$bundle_manifest"
[ "${LUMA_FP6_FINGERPRINT_BUILD_VERSION:-}" = 1 ] || die 'bundle version differs'
[ "${KERNEL_RELEASE:-}" = 7.1.2 ] || die 'kernel release differs'
[ "${QSEECOM_PATCHSET_SHA256:-}" = "$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256" ] ||
  die 'QSEECOM patchset differs'
[ "${FOCALTECH_PATCHSET_SHA256:-}" = "$FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256" ] ||
  die 'FocalTech patchset differs'
[ "${PROPRIETARY_PAYLOADS_INCLUDED:-}" = false ] || die 'bundle payload policy differs'
[ "${PHONE_ACCESSED:-}" = false ] && [ "${PARTITION_WRITTEN:-}" = false ] ||
  die 'bundle provenance differs'
[ "$(hash "$kernel")" = "${IMAGE_SHA256:-}" ] || die 'kernel checksum differs'
[ "$(hash "$kernel")" != "$FP6_FINGERPRINT_CANDIDATE_KERNEL_SHA256" ] ||
  die 'ELF64-loader kernel unexpectedly equals enumeration-v1'
[ "$(hash "$module_focal")" = "${FOCALTECH_FP_KO_SHA256:-}" ] ||
  die 'FocalTech module checksum differs'
[ "$(hash "$module_qsee")" = "${QSEECOMTEE_KO_SHA256:-}" ] ||
  die 'QSEECOM module checksum differs'
gzip -t "$kernel"
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT HUP INT TERM
base_dir=$work_dir/base
base_verify_dir=$work_dir/base-verify
candidate_dir=$work_dir/candidate
roundtrip_dir=$work_dir/roundtrip
args_file=$work_dir/mkbootimg.args0
roundtrip=$work_dir/base-roundtrip.img
enabled_dtb=$output_dir/milos-fairphone-fp6-fingerprint-loader-elf64-v3.dtb
mkdir -p "$base_dir" "$base_verify_dir" "$candidate_dir" "$roundtrip_dir"

unpack_bootimg --boot_img "$base_boot" --out "$base_verify_dir" \
  >"$work_dir/base-info.txt"
unpack_bootimg --boot_img "$base_boot" --out "$base_dir" \
  --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
unpack_bootimg --boot_img "$roundtrip" --out "$roundtrip_dir" \
  >"$work_dir/roundtrip-info.txt"
cmp "$work_dir/base-info.txt" "$work_dir/roundtrip-info.txt"
cmp "$base_verify_dir/kernel" "$roundtrip_dir/kernel"
cmp "$base_verify_dir/ramdisk" "$roundtrip_dir/ramdisk"
cmp "$base_verify_dir/dtb" "$roundtrip_dir/dtb"
[ "$(hash "$base_dir/kernel")" = "$FP6_FINGERPRINT_LISTENER_V2_KERNEL_SHA256" ] ||
  die 'listener-v2 kernel checksum differs'
[ "$(hash "$base_dir/dtb")" = "$FP6_FINGERPRINT_LISTENER_V2_DTB_SHA256" ] ||
  die 'listener-v2 DTB checksum differs'
[ "$(hash "$base_dir/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
  die 'listener-v2 ramdisk checksum differs'

install -m 0644 "$kernel" "$base_dir/kernel"
install -m 0644 "$base_dir/dtb" "$enabled_dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" \
  >"$output_dir/boot-info.txt"
cmp "$kernel" "$candidate_dir/kernel"
cmp "$enabled_dtb" "$candidate_dir/dtb"
cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
  die 'candidate exceeds the 96 MiB boot partition'

candidate_sha=$(hash "$candidate")
{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=3\n'
  printf 'SCOPE=fp6-fingerprint-loader-elf64\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_LISTENER_V2_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$IMAGE_SHA256"
  printf 'DTB_SHA256=%s\n' "$(hash "$enabled_dtb")"
  printf 'RAMDISK_SHA256=%s\n' "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256"
  printf 'FOCALTECH_FP_KO_SHA256=%s\n' "$FOCALTECH_FP_KO_SHA256"
  printf 'QSEECOMTEE_KO_SHA256=%s\n' "$QSEECOMTEE_KO_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'BASE_PARTITION_SLACK_OMITTED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'DTB_UNCHANGED=true\n'
  printf 'SENSOR_NODE_ENABLED=true\n'
  printf 'LISTENER_REGISTER_ABI=smcinvoke-command-6-with-legacy-fallback\n'
  printf 'QSEECOM_CONTIGUOUS_ELF64_AARCH64=true\n'
  printf 'TRUSTLET_LOAD_ENABLED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$enabled_dtb"

printf 'FP6 fingerprint ELF64-loader boot candidate: %s\n' "$candidate"
