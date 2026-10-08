#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Replace only the kernel payload in the proven FP6 control boot image. This
# script creates and verifies an artifact; it never contacts or flashes a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
control_boot=${1:?usage: prepare-fp6-ifpc-boot-candidate.sh CONTROL_BOOT IMAGE_GZ [OUTPUT_DIR] [LABEL]}
kernel_image=${2:?usage: prepare-fp6-ifpc-boot-candidate.sh CONTROL_BOOT IMAGE_GZ [OUTPUT_DIR] [LABEL]}
output_dir=${3:-$repo_root/build/mobile/fp6-physical/kernel-ifpc-test1}
candidate_label=${4:-ifpc-test1}

expected_control_sha=5885e62324115ae9cce929318df24251992ef705952dcbe6b16cef549a505efb
case "$candidate_label" in
  ''|*[!a-z0-9-]*)
    printf 'error: candidate label must contain only lowercase letters, digits, and hyphens\n' >&2
    exit 1
    ;;
esac
candidate=$output_dir/boot-fp6-luma-$candidate_label.img

for tool in cmp file install mkbootimg mktemp sha256sum unpack_bootimg xargs; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required boot-image tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

[ -f "$control_boot" ] && [ -f "$kernel_image" ] || {
  printf 'error: control boot image and diagnostic kernel must exist\n' >&2
  exit 1
}
[ "$(sha256sum "$control_boot" | awk '{print $1}')" = "$expected_control_sha" ] || {
  printf 'error: control boot image checksum differs from the proven boot_b image\n' >&2
  exit 1
}
file "$kernel_image" | grep -Fq 'gzip compressed data' || {
  printf 'error: diagnostic kernel is not an Image.gz payload\n' >&2
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
xargs -0 mkbootimg --output "$roundtrip" <"$args_file"
cmp "$control_boot" "$roundtrip"

install -m 0644 "$kernel_image" "$control_dir/kernel"
xargs -0 mkbootimg --output "$candidate" <"$args_file"

unpack_bootimg --boot_img "$candidate" --out "$candidate_dir" \
  >"$output_dir/boot-info.txt"
cmp "$kernel_image" "$candidate_dir/kernel"
cmp "$control_dir/ramdisk" "$candidate_dir/ramdisk"
cmp "$control_dir/dtb" "$candidate_dir/dtb"

kernel_sha=$(sha256sum "$kernel_image" | awk '{print $1}')
candidate_sha=$(sha256sum "$candidate" | awk '{print $1}')
{
  printf 'LUMA_FP6_BOOT_CANDIDATE_VERSION=1\n'
  printf 'CANDIDATE_LABEL=%s\n' "$candidate_label"
  printf 'CONTROL_BOOT_SHA256=%s\n' "$expected_control_sha"
  printf 'KERNEL_SHA256=%s\n' "$kernel_sha"
  printf 'CANDIDATE_SHA256=%s\n' "$candidate_sha"
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'DTB_UNCHANGED=true\n'
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-manifest.env" "$output_dir/boot-info.txt"

printf 'FP6 IFPC diagnostic boot candidate: %s\n' "$candidate"
