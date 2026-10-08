#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Replace only the DTB in Luma's proven camera-enabled FP6 boot image.  The
# kernel and ramdisk remain byte-identical.  This script never contacts a
# phone, installs firmware, or writes a partition.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-audio.env"

base_boot=${1:?usage: prepare-fp6-audio-boot-candidate.sh BASE_BOOT AUDIO_DTB [OUTPUT_DIR]}
audio_dtb=${2:?usage: prepare-fp6-audio-boot-candidate.sh BASE_BOOT AUDIO_DTB [OUTPUT_DIR]}
output_dir=${3:-$repo_root/build/mobile/fp6-physical/fp6-audio-speaker-boot-v1}
candidate=$output_dir/boot-fp6-luma-audio-speaker-v1.img

for tool in cmp file install mkbootimg mktemp python3 sha256sum unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: missing boot-image tool: %s\n' "$tool" >&2
    exit 1
  }
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

[ -f "$base_boot" ] && [ -f "$audio_dtb" ] || {
  printf 'error: base boot image and audio DTB must exist\n' >&2
  exit 1
}
[ "$(sha256sum "$base_boot" | awk '{print $1}')" = \
  "$FP6_AUDIO_BASE_BOOT_SHA256" ] || {
  printf 'error: base boot image checksum differs\n' >&2
  exit 1
}
[ "$(sha256sum "$audio_dtb" | awk '{print $1}')" = \
  "$FP6_AUDIO_SPEAKER_DTB_SHA256" ] || {
  printf 'error: speaker DTB checksum differs\n' >&2
  exit 1
}
file "$audio_dtb" | grep -Fq 'Device Tree Blob' || {
  printf 'error: audio artifact is not a flattened device tree\n' >&2
  exit 1
}

mkdir -p "$output_dir"
work_dir=$(mktemp -d "$output_dir/repack.XXXXXX")
base_dir=$work_dir/base
candidate_dir=$work_dir/candidate
args_file=$work_dir/mkbootimg.args0
roundtrip=$work_dir/base-roundtrip.img
mkdir -p "$base_dir" "$candidate_dir"

unpack_bootimg --boot_img "$base_boot" --out "$base_dir" \
  --format=mkbootimg -0 >"$args_file"
run_mkbootimg_args0 "$args_file" "$roundtrip"
cmp "$base_boot" "$roundtrip"

install -m 0644 "$audio_dtb" "$base_dir/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" \
  >"$output_dir/boot-info.txt"

cmp "$base_dir/kernel" "$candidate_dir/kernel"
cmp "$base_dir/ramdisk" "$candidate_dir/ramdisk"
cmp "$audio_dtb" "$candidate_dir/dtb"

{
  printf 'LUMA_FP6_AUDIO_BOOT_CANDIDATE_VERSION=1\n'
  printf 'SCOPE=senary-mi2s-aw88261-playback\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_AUDIO_BASE_BOOT_SHA256"
  printf 'DTB_SHA256=%s\n' "$FP6_AUDIO_SPEAKER_DTB_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$(sha256sum "$candidate" | awk '{print $1}')"
  printf 'KERNEL_UNCHANGED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'DTB_REPLACED=true\n'
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-manifest.env" "$output_dir/boot-info.txt"

printf 'FP6 speaker boot candidate: %s\n' "$candidate"
