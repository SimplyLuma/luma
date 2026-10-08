#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an offline FP6 SHM-Bridge diagnostic from the physically healthy
# low-32-bit QSEE-pool boot. Only the kernel changes. The accepted low-32-bit
# DTB, ramdisk, header fields, and installed module ABI remain exact. This
# script cannot contact a phone or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-shmbridge-candidate.sh LOW32_BOOT KERNEL_BUNDLE OUTPUT_DIR}
bundle=${2:?usage: prepare-fp6-fingerprint-shmbridge-candidate.sh LOW32_BOOT KERNEL_BUNDLE OUTPUT_DIR}
output_dir=${3:?usage: prepare-fp6-fingerprint-shmbridge-candidate.sh LOW32_BOOT KERNEL_BUNDLE OUTPUT_DIR}
candidate=$output_dir/boot-fp6-luma-fingerprint-shmbridge-v7.img
bundle_manifest=$bundle/manifest.env
kernel=$bundle/Image.gz
module_focal=$bundle/focaltech_fp.ko
module_qsee=$bundle/qseecomtee.ko

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp grep install mkbootimg mktemp python3 sha256sum stat \
  unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 ||
    die "missing boot-image tool: $tool"
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
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_SHA256" ] ||
  die 'physically healthy low-32-bit boot checksum differs'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$bundle_manifest"; then
  die 'fingerprint bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$bundle_manifest"
[ "${LUMA_FP6_FINGERPRINT_BUILD_VERSION:-}" = 1 ] ||
  die 'bundle version differs'
[ "${KERNEL_RELEASE:-}" = 7.1.2 ] || die 'kernel release differs'
[ "${QSEECOM_PATCHSET_SHA256:-}" = "$FP6_FINGERPRINT_QSEECOM_PATCHSET_SHA256" ] ||
  die 'QSEECOM patchset differs'
[ "${FOCALTECH_PATCHSET_SHA256:-}" = "$FP6_FINGERPRINT_DRIVER_PATCHSET_SHA256" ] ||
  die 'FocalTech patchset differs'
[ "${TZMEM_MODE:-}" = shmbridge ] || die 'bundle is not SHM-Bridge mode'
[ "${SHMBRIDGE_DIAGNOSTIC_PATCH_COUNT:-}" = \
  "$FP6_FINGERPRINT_SHMBRIDGE_PATCH_COUNT" ] ||
  die 'SHM-Bridge diagnostic patch count differs'
[ "${SHMBRIDGE_DIAGNOSTIC_PATCHSET_SHA256:-}" = \
  "$FP6_FINGERPRINT_SHMBRIDGE_PATCHSET_SHA256" ] ||
  die 'SHM-Bridge diagnostic patchset differs'
[ "${PROPRIETARY_PAYLOADS_INCLUDED:-}" = false ] ||
  die 'bundle payload policy differs'
[ "${PHONE_ACCESSED:-}" = false ] && [ "${PARTITION_WRITTEN:-}" = false ] ||
  die 'bundle provenance differs'
[ "$(hash "$kernel")" = "${IMAGE_SHA256:-}" ] ||
  die 'kernel checksum differs'
[ "$(hash "$kernel")" != "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256" ] ||
  die 'SHM-Bridge kernel unexpectedly equals the generic kernel'
[ "$(hash "$module_focal")" = "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256" ] ||
  die 'FocalTech module ABI/binary differs'
[ "$(hash "$module_qsee")" = "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256" ] ||
  die 'QSEECOM module ABI/binary differs'
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
[ "$(hash "$base_dir/kernel")" = "$FP6_FINGERPRINT_MDT_ELF64_KERNEL_SHA256" ] ||
  die 'generic ELF64-loader kernel checksum differs'
[ "$(hash "$base_dir/dtb")" = "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256" ] ||
  die 'low-32-bit DTB checksum differs'
[ "$(hash "$base_dir/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
  die 'base ramdisk checksum differs'

install -m 0644 "$kernel" "$base_dir/kernel"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" \
  >"$output_dir/boot-info.txt"
cmp "$kernel" "$candidate_dir/kernel"
cmp "$base_dir/dtb" "$candidate_dir/dtb"
cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
  die 'candidate exceeds the 96 MiB boot partition'

candidate_sha=$(hash "$candidate")
{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=7\n'
  printf 'SCOPE=fp6-fingerprint-shmbridge-diagnostic\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_LOW32_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$IMAGE_SHA256"
  printf 'CONFIG_SHA256=%s\n' "$CONFIG_SHA256"
  printf 'DTB_SHA256=%s\n' "$FP6_FINGERPRINT_QSEE_POOL_LOW32_DTB_SHA256"
  printf 'RAMDISK_SHA256=%s\n' "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256"
  printf 'FOCALTECH_FP_KO_SHA256=%s\n' "$FP6_FINGERPRINT_LISTENER_V2_FOCALTECH_KO_SHA256"
  printf 'QSEECOMTEE_KO_SHA256=%s\n' "$FP6_FINGERPRINT_LISTENER_V2_QSEECOMTEE_KO_SHA256"
  printf 'SHMBRIDGE_PATCHSET_SHA256=%s\n' "$FP6_FINGERPRINT_SHMBRIDGE_PATCHSET_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'BASE_PARTITION_SLACK_OMITTED=true\n'
  printf 'KERNEL_CHANGED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'DTB_UNCHANGED=true\n'
  printf 'MODULE_BINARIES_UNCHANGED=true\n'
  printf 'QSEE_POOL_LOW32_RETAINED=true\n'
  printf 'TZMEM_MODE=shmbridge\n'
  printf 'BOUNDED_DIAGNOSTICS=true\n'
  printf 'TRUSTLET_LOAD_ENABLED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'PROPRIETARY_PAYLOADS_INCLUDED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env"

printf 'FP6 fingerprint SHM-Bridge candidate: %s\n' "$candidate"
