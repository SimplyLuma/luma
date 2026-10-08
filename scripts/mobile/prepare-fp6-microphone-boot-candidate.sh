#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Replace only the DTB in Luma's physically proven FP6 audio boot image. The
# kernel, ramdisk, and every boot-header field remain byte-identical. Keeping
# the kernel is required because the installed module set uses split BTF tied
# to its exact base-type graph. This script is offline: it cannot contact a
# phone or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-microphone.env"

base_boot=${1:?usage: prepare-fp6-microphone-boot-candidate.sh BASE_BOOT MICROPHONE_BUNDLE [OUTPUT_DIR]}
bundle=${2:?usage: prepare-fp6-microphone-boot-candidate.sh BASE_BOOT MICROPHONE_BUNDLE [OUTPUT_DIR]}
output_dir=${3:-$repo_root/build/mobile/fp6-physical/fp6-microphone-boot-v2}
candidate=$output_dir/boot-fp6-luma-microphone-v2.img
bundle_manifest=$bundle/manifest.env
dtb=$bundle/milos-fairphone-fp6.dtb

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

for tool in cmp cut file grep install mkbootimg mktemp python3 sha256sum \
  unpack_bootimg; do
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

[ -f "$base_boot" ] || die 'base boot image is missing'
[ -f "$bundle_manifest" ] || die 'microphone bundle manifest is missing'
[ -f "$dtb" ] || die 'microphone DTB is missing'

if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$bundle_manifest"; then
  die 'bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$bundle_manifest"

[ "${LUMA_FP6_MICROPHONE_BUILD_VERSION:-}" = 1 ] ||
  die 'unsupported microphone bundle version'
[ "${KERNEL_RELEASE:-}" = 7.1.2 ] || die 'bundle kernel differs'
[ "${RAM_BOOT_ONLY:-}" = true ] || die 'bundle is not RAM-boot-only'
[ "${PHONE_ACCESSED:-}" = false ] || die 'bundle provenance differs'
[ "${MODULES_INSTALLED:-}" = false ] || die 'bundle provenance differs'
[ "${PARTITION_WRITTEN:-}" = false ] || die 'bundle provenance differs'

[ "$(sha256sum "$base_boot" | cut -d ' ' -f 1)" = \
  "$FP6_MICROPHONE_BASE_BOOT_SHA256" ] || die 'base boot checksum differs'
[ "$(sha256sum "$dtb" | cut -d ' ' -f 1)" = \
  "$FP6_MICROPHONE_DTB_SHA256" ] || die 'DTB checksum differs'
[ "$IMAGE_SHA256" = "$FP6_MICROPHONE_V1_REBUILT_KERNEL_SHA256" ] ||
  die 'builder manifest kernel checksum differs'
[ "$DTB_SHA256" = "$FP6_MICROPHONE_DTB_SHA256" ] ||
  die 'manifest DTB checksum differs'
file "$dtb" | grep -Fq 'Device Tree Blob' || die 'artifact is not a DTB'

if [ -e "$candidate" ]; then
  die "refuse to overwrite existing candidate: $candidate"
fi
mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT HUP INT TERM
base_dir=$work_dir/base
candidate_dir=$work_dir/candidate
args_file=$work_dir/mkbootimg.args0
roundtrip=$work_dir/base-roundtrip.img
mkdir -p "$base_dir" "$candidate_dir"

unpack_bootimg --boot_img "$base_boot" --out "$base_dir" \
  --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
cmp "$base_boot" "$roundtrip"
[ "$(sha256sum "$base_dir/kernel" | cut -d ' ' -f 1)" = \
  "$FP6_MICROPHONE_PROVEN_KERNEL_SHA256" ] ||
  die 'proven base kernel checksum differs'

install -m 0644 "$dtb" "$base_dir/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" \
  >"$output_dir/boot-info.txt"

cmp "$base_dir/kernel" "$candidate_dir/kernel"
cmp "$dtb" "$candidate_dir/dtb"
cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"
candidate_sha256=$(sha256sum "$candidate" | cut -d ' ' -f 1)
[ "$candidate_sha256" = "$FP6_MICROPHONE_V2_BOOT_SHA256" ] ||
  die 'DTB-only candidate checksum differs'

{
  printf 'LUMA_FP6_MICROPHONE_BOOT_CANDIDATE_VERSION=2\n'
  printf 'SCOPE=wcd9378-soundwire-capture\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_MICROPHONE_BASE_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$FP6_MICROPHONE_PROVEN_KERNEL_SHA256"
  printf 'DTB_SHA256=%s\n' "$FP6_MICROPHONE_DTB_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha256"
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'DTB_REPLACED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-manifest.env" "$output_dir/boot-info.txt"

printf 'FP6 microphone RAM-boot candidate: %s\n' "$candidate"
