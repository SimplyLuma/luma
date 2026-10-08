#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Replace only the DTB in the proven FP6 control boot image. The kernel and
# ramdisk remain byte-identical. This script never contacts or changes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-camera.env"

control_boot=${1:?usage: prepare-fp6-camera-boot-candidate.sh CONTROL_BOOT CAMERA_DTB [OUTPUT_DIR]}
camera_dtb=${2:?usage: prepare-fp6-camera-boot-candidate.sh CONTROL_BOOT CAMERA_DTB [OUTPUT_DIR]}
output_dir=${3:-$repo_root/build/mobile/fp6-physical/fp6-camera-catcrafts1}
candidate=$output_dir/boot-fp6-luma-camera-catcrafts1.img

for tool in cmp file install mkbootimg mktemp python3 sha256sum unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing boot-image tool: %s\n' "$tool" >&2
    exit 1
  }
done

run_mkbootimg_args0() {
  local args_file=$1 output=$2
  # BSD xargs drops the empty --board value emitted for this image. Preserve
  # every NUL-delimited argument (including empty strings) so the control
  # round-trip remains byte-exact on both macOS and Linux hosts.
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
[ -f "$control_boot" ] && [ -f "$camera_dtb" ] || {
  printf 'error: control boot image and camera DTB must exist\n' >&2
  exit 1
}
[ "$(sha256sum "$control_boot" | awk '{print $1}')" = \
  "$FP6_CAMERA_CONTROL_BOOT_SHA256" ] || {
  printf 'error: control boot image checksum differs\n' >&2
  exit 1
}
file "$camera_dtb" | grep -Fq 'Device Tree Blob' || {
  printf 'error: camera artifact is not a flattened device tree\n' >&2
  exit 1
}

mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/repack.XXXXXX")
control_dir=$work_dir/control
candidate_dir=$work_dir/candidate
args_file=$work_dir/mkbootimg.args0
roundtrip=$work_dir/control-roundtrip.img
mkdir -p "$control_dir" "$candidate_dir"

unpack_bootimg --boot_img "$control_boot" --out "$control_dir" \
  --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
cmp "$control_boot" "$roundtrip"

install -m 0644 "$camera_dtb" "$control_dir/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" \
  >"$output_dir/boot-info.txt"

cmp "$control_dir/kernel" "$candidate_dir/kernel"
cmp "$control_dir/ramdisk" "$candidate_dir/ramdisk"
cmp "$camera_dtb" "$candidate_dir/dtb"

{
  printf 'LUMA_FP6_CAMERA_BOOT_CANDIDATE_VERSION=2\n'
  printf 'SCOPE=imx896-dw9784-ov13b10-s5kkd1sp\n'
  printf 'CONTROL_BOOT_SHA256=%s\n' "$FP6_CAMERA_CONTROL_BOOT_SHA256"
  printf 'DTB_SHA256=%s\n' "$(sha256sum "$camera_dtb" | awk '{print $1}')"
  printf 'CANDIDATE_SHA256=%s\n' "$(sha256sum "$candidate" | awk '{print $1}')"
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'DTB_REPLACED=true\n'
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-manifest.env" "$output_dir/boot-info.txt"

printf 'FP6 CatCrafts-derived camera boot candidate: %s\n' "$candidate"
