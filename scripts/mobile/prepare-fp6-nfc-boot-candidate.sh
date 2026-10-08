#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Replace only the DTB in the proven sensor RAM-boot image. NFC modules remain
# a separately verified live-staging bundle. This tool cannot contact a phone
# or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-nfc.env"

base_boot=${1:?usage: prepare-fp6-nfc-boot-candidate.sh SENSOR_BOOT NFC_BUNDLE OUTPUT_DIR}
bundle=${2:?usage: prepare-fp6-nfc-boot-candidate.sh SENSOR_BOOT NFC_BUNDLE OUTPUT_DIR}
output_dir=${3:?usage: prepare-fp6-nfc-boot-candidate.sh SENSOR_BOOT NFC_BUNDLE OUTPUT_DIR}
candidate=$output_dir/boot-fp6-luma-nfc-reader-v1.img
bundle_manifest=$bundle/manifest.env
dtb=$bundle/milos-fairphone-fp6-nfc.dtb

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | cut -d ' ' -f 1; }
for tool in cmp cut file grep install mkbootimg mktemp python3 sha256sum unpack_bootimg; do
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

for artifact in "$base_boot" "$bundle_manifest" "$dtb"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$bundle_manifest"; then
  die 'NFC bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$bundle_manifest"
[ "${LUMA_FP6_NFC_BUILD_VERSION:-}" = 1 ] || die 'NFC bundle version differs'
[ "${UPSTREAM_COMMIT:-}" = "$FP6_NFC_UPSTREAM_COMBINED_COMMIT" ] || die 'NFC upstream differs'
[ "${KERNEL_RELEASE:-}" = 7.1.2 ] || die 'kernel release differs'
[ "${BASE_DTB_SHA256:-}" = "$FP6_NFC_BASE_DTB_SHA256" ] || die 'base DTB differs'
[ "${OVERLAY_SHA256:-}" = "$FP6_NFC_OVERLAY_SHA256" ] || die 'overlay differs'
[ "${DTB_SHA256:-}" = "$FP6_NFC_DTB_SHA256" ] || die 'NFC DTB manifest differs'
[ "${KERNEL_RELINKED:-}" = false ] || die 'kernel provenance differs'
[ "${BASE_DTB_PRESERVED:-}" = true ] || die 'base DTB provenance differs'
[ "${NFC_OVERLAY_ONLY:-}" = true ] || die 'DTB scope differs'
[ "${CALIBRATION_BLOBS_INCLUDED:-}" = false ] || die 'calibration provenance differs'
[ "${READER_SCOPE_ONLY:-}" = true ] || die 'candidate scope differs'
[ "${RAM_BOOT_ONLY:-}" = true ] || die 'bundle is not RAM-boot-only'
[ "${PHONE_ACCESSED:-}" = false ] && [ "${PARTITION_WRITTEN:-}" = false ] ||
  die 'bundle provenance differs'
[ "$(hash "$base_boot")" = "$FP6_NFC_BASE_BOOT_SHA256" ] || die 'sensor base boot differs'
[ "$(hash "$dtb")" = "$FP6_NFC_DTB_SHA256" ] || die 'NFC DTB differs'
file "$dtb" | grep -Fq 'Device Tree Blob' || die 'artifact is not a DTB'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() { rm -rf -- "$work_dir"; }
trap cleanup EXIT HUP INT TERM
base_dir=$work_dir/base
candidate_dir=$work_dir/candidate
args_file=$work_dir/mkbootimg.args0
roundtrip=$work_dir/base-roundtrip.img
mkdir -p "$base_dir" "$candidate_dir"
unpack_bootimg --boot_img "$base_boot" --out "$base_dir" --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
cmp "$base_boot" "$roundtrip"
[ "$(hash "$base_dir/kernel")" = "$FP6_NFC_BASE_KERNEL_SHA256" ] || die 'sensor kernel differs'
[ "$(hash "$base_dir/dtb")" = "$FP6_NFC_BASE_DTB_SHA256" ] || die 'sensor DTB differs'
install -m 0644 "$dtb" "$base_dir/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" >"$output_dir/boot-info.txt"
cmp "$base_dir/kernel" "$candidate_dir/kernel"
cmp "$dtb" "$candidate_dir/dtb"
cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"

candidate_sha256=$(hash "$candidate")
hash_pinned=true
if [ "$FP6_NFC_BOOT_SHA256" = UNPACKAGED ]; then
  [ "${LUMA_ALLOW_UNPINNED_FP6_NFC_BOOT:-}" = 1 ] || die 'NFC boot hash is not pinned'
  hash_pinned=false
else
  [ "$candidate_sha256" = "$FP6_NFC_BOOT_SHA256" ] || die 'NFC boot checksum differs'
fi
{
  printf 'LUMA_FP6_NFC_BOOT_CANDIDATE_VERSION=1\n'
  printf 'SCOPE=fp6-nfc-reader-diagnostic\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_NFC_BASE_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$FP6_NFC_BASE_KERNEL_SHA256"
  printf 'BASE_DTB_SHA256=%s\n' "$FP6_NFC_BASE_DTB_SHA256"
  printf 'DTB_SHA256=%s\n' "$FP6_NFC_DTB_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha256"
  printf 'CANDIDATE_HASH_PINNED=%s\n' "$hash_pinned"
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'DTB_REPLACED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'MODULES_STAGED=false\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-manifest.env" "$output_dir/boot-info.txt"

printf 'FP6 NFC reader RAM-boot candidate: %s\n' "$candidate"
