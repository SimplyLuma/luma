#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Replace only the DTB in the accepted v3 RAM-boot image for the AMIC3 mapping
# trial. Kernel, ramdisk, and boot-header fields remain byte-identical. This is
# offline tooling and cannot contact a phone or write a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-microphone.env"

base_boot=${1:?usage: prepare-fp6-amic3-boot-candidate.sh V3_BOOT AMIC3_BUNDLE [OUTPUT_DIR]}
bundle=${2:?usage: prepare-fp6-amic3-boot-candidate.sh V3_BOOT AMIC3_BUNDLE [OUTPUT_DIR]}
output_dir=${3:-$repo_root/build/mobile/fp6-physical/fp6-microphone-boot-v4}
candidate=$output_dir/boot-fp6-luma-microphone-v4.img
bundle_manifest=$bundle/manifest.env
dtb=$bundle/milos-fairphone-fp6.dtb

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
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
  die 'AMIC3 manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$bundle_manifest"
[ "${LUMA_FP6_AMIC3_ROUTING_BUILD_VERSION:-}" = 1 ] || die 'bundle version differs'
[ "${KERNEL_RELEASE:-}" = 7.1.2 ] || die 'kernel release differs'
[ "${BASE_KERNEL_SHA256:-}" = "$FP6_MICROPHONE_V3_RELINKED_KERNEL_SHA256" ] || die 'base kernel differs'
[ "${PATCH_SHA256:-}" = "$FP6_MICROPHONE_AMIC3_V1_PATCH_SHA256" ] || die 'mapping patch differs'
[ "${WCD9378_SDW_KO_SHA256:-}" = "$FP6_MICROPHONE_AMIC3_V1_WCD9378_SDW_KO_SHA256" ] || die 'module differs'
[ "${DTB_SHA256:-}" = "$FP6_MICROPHONE_AMIC3_V1_DTB_SHA256" ] || die 'DTB manifest differs'
[ "${MODULE_BTF_BASE:-}" = v3 ] || die 'module BTF base differs'
[ "${RAM_BOOT_ONLY:-}" = true ] || die 'bundle is not RAM-boot-only'
[ "${PHONE_ACCESSED:-}" = false ] || die 'bundle provenance differs'
[ "${PARTITION_WRITTEN:-}" = false ] || die 'bundle provenance differs'

[ "$(sha256sum "$base_boot" | cut -d ' ' -f 1)" = "$FP6_MICROPHONE_V3_BOOT_SHA256" ] || die 'v3 base boot differs'
[ "$(sha256sum "$dtb" | cut -d ' ' -f 1)" = "$FP6_MICROPHONE_AMIC3_V1_DTB_SHA256" ] || die 'AMIC3 DTB differs'
file "$dtb" | grep -Fq 'Device Tree Blob' || die 'artifact is not a DTB'
[ ! -e "$candidate" ] || die "refuse to overwrite existing candidate: $candidate"

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
[ "$(sha256sum "$base_dir/kernel" | cut -d ' ' -f 1)" = "$FP6_MICROPHONE_V3_RELINKED_KERNEL_SHA256" ] || die 'v3 kernel differs'
install -m 0644 "$dtb" "$base_dir/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" >"$output_dir/boot-info.txt"
cmp "$base_dir/kernel" "$candidate_dir/kernel"
cmp "$dtb" "$candidate_dir/dtb"
cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"

candidate_sha256=$(sha256sum "$candidate" | cut -d ' ' -f 1)
hash_pinned=true
if [ "$FP6_MICROPHONE_V4_BOOT_SHA256" = UNPACKAGED ]; then
  [ "${LUMA_ALLOW_UNPINNED_FP6_MICROPHONE_V4:-}" = 1 ] || die 'v4 hash is not pinned'
  hash_pinned=false
else
  [ "$candidate_sha256" = "$FP6_MICROPHONE_V4_BOOT_SHA256" ] || die 'v4 checksum differs'
fi
{
  printf 'LUMA_FP6_MICROPHONE_BOOT_CANDIDATE_VERSION=4\n'
  printf 'SCOPE=wcd9378-amic3-master-port-routing\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_MICROPHONE_V3_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$FP6_MICROPHONE_V3_RELINKED_KERNEL_SHA256"
  printf 'DTB_SHA256=%s\n' "$FP6_MICROPHONE_AMIC3_V1_DTB_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha256"
  printf 'CANDIDATE_HASH_PINNED=%s\n' "$hash_pinned"
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'DTB_REPLACED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'RAM_BOOT_ONLY=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-manifest.env" "$output_dir/boot-info.txt"
printf 'FP6 AMIC3 RAM-boot candidate: %s\n' "$candidate"
