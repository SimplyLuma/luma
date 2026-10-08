#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an offline FP6 QSEE-log diagnostic boot candidate from the accepted
# dedicated-heaps v10 image. Only the kernel and one opt-in DT property change;
# ramdisk and boot-header fields remain exact. This never contacts a phone.

set -euo pipefail
umask 022

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
# shellcheck disable=SC1091
. "$repo_root/config/mobile/fp6-fingerprint.env"

base_boot=${1:?usage: prepare-fp6-fingerprint-qseelog-candidate.sh V10_BOOT QSEELOG_KERNEL_BUNDLE OUTPUT_DIR}
bundle=${2:?missing QSEE-log kernel bundle}
output_dir=${3:?missing output directory}
overlay=$repo_root/config/mobile/fp6-fingerprint/milos-fairphone-fp6-qsee-log.dtso
candidate=$output_dir/boot-fp6-luma-fingerprint-qseelog-v11.img
manifest=$bundle/manifest.env

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dtc fdtoverlay fdtget install mkbootimg mktemp python3 \
  sha256sum stat unpack_bootimg; do
  command -v "$tool" >/dev/null 2>&1 || die "missing composition tool: $tool"
done
for artifact in "$base_boot" "$bundle/Image.gz" "$bundle/config" \
  "$bundle/focaltech_fp.ko" "$bundle/qseecomtee.ko" "$manifest" "$overlay"; do
  [ -f "$artifact" ] || die "required artifact is missing: $artifact"
done
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"
[ "$(hash "$base_boot")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_SHA256" ] ||
  die 'accepted dedicated-heaps boot differs'
[ "$(hash "$overlay")" = "$FP6_FINGERPRINT_QSEELOG_OVERLAY_SHA256" ] ||
  die 'QSEE-log overlay differs'
if grep -Evq '^[A-Z0-9_]+=[A-Za-z0-9._,-]+$' "$manifest"; then
  die 'kernel bundle manifest contains unexpected syntax'
fi
# shellcheck disable=SC1090
. "$manifest"
[ "$KERNEL_RELEASE" = 7.1.2 ] || die 'kernel release differs'
[ "$TZMEM_MODE" = shmbridge ] || die 'kernel is not in SHM-Bridge mode'
[ "$STOCK_HEAPS_ENABLED" = true ] || die 'stock-heaps base patch is absent'
[ "$DEDICATED_HEAPS_ENABLED" = true ] || die 'dedicated heaps are absent'
[ "$QSEELOG_ENABLED" = true ] || die 'QSEE log patch is absent'
[ "$QSEELOG_PATCH_COUNT" = "$FP6_FINGERPRINT_QSEELOG_PATCH_COUNT" ] ||
  die 'QSEE-log patch count differs'
[ "$QSEELOG_PATCHSET_SHA256" = "$FP6_FINGERPRINT_QSEELOG_PATCHSET_SHA256" ] ||
  die 'QSEE-log patchset differs'
[ "$(hash "$bundle/config")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_CONFIG_SHA256" ] ||
  die 'kernel configuration differs'

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
overlay_blob=$work/qsee-log.dtbo
enabled_dtb=$output_dir/milos-fairphone-fp6-fingerprint-qseelog-v11.dtb

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
[ "$(hash "$work/base/kernel")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_KERNEL_SHA256" ] ||
  die 'base kernel differs'
[ "$(hash "$work/base/ramdisk")" = "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256" ] ||
  die 'base ramdisk differs'
[ "$(hash "$work/base/dtb")" = "$FP6_FINGERPRINT_DEDICATED_HEAPS_DTB_SHA256" ] ||
  die 'base DTB differs'

dtc -@ -I dts -O dtb -o "$overlay_blob" "$overlay"
fdtoverlay -i "$work/base/dtb" -o "$enabled_dtb" "$overlay_blob"
install -m 0644 "$bundle/Image.gz" "$work/base/kernel"
install -m 0644 "$enabled_dtb" "$work/base/dtb"
run_mkbootimg_args0 "$args_file" "$candidate"
unpack_bootimg --boot_img "$candidate" --out "$work/candidate" \
  >"$output_dir/boot-info.txt"

cmp "$bundle/Image.gz" "$work/candidate/kernel"
cmp "$enabled_dtb" "$work/candidate/dtb"
cmp "$work/base/ramdisk" "$work/candidate/ramdisk"
fdtget "$work/candidate/dtb" /firmware/scm luma,qsee-log-diagnostic >/dev/null ||
  die 'QSEE-log opt-in property is absent'
fdtget "$work/candidate/dtb" /firmware/scm/qseecom-tee-heaps luma,dedicated-heaps >/dev/null ||
  die 'dedicated-heaps child is absent'
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] ||
  die 'candidate exceeds the 96 MiB boot partition'

{
  printf 'LUMA_FP6_FINGERPRINT_BOOT_CANDIDATE_VERSION=11\n'
  printf 'SCOPE=fp6-fingerprint-qseelog-diagnostic\n'
  printf 'BASE_BOOT_SHA256=%s\n' "$FP6_FINGERPRINT_DEDICATED_HEAPS_BOOT_SHA256"
  printf 'KERNEL_SHA256=%s\n' "$(hash "$bundle/Image.gz")"
  printf 'CONFIG_SHA256=%s\n' "$(hash "$bundle/config")"
  printf 'DTB_SHA256=%s\n' "$(hash "$enabled_dtb")"
  printf 'RAMDISK_SHA256=%s\n' "$FP6_FINGERPRINT_BASE_RAMDISK_SHA256"
  printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
  printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
  printf 'HEADER_ROUNDTRIP_EXACT=true\n'
  printf 'QSEELOG_ENABLED=true\n'
  printf 'QSEELOG_BUFFER_SIZE=%s\n' "$FP6_FINGERPRINT_QSEELOG_BUFFER_SIZE"
  printf 'DEDICATED_HEAPS_RETAINED=true\n'
  printf 'RAMDISK_UNCHANGED=true\n'
  printf 'TRUSTLET_LOAD_ENABLED=false\n'
  printf 'TA_LOAD_ATTEMPTED=false\n'
  printf 'ENROLLMENT_ENABLED=false\n'
  printf 'AUTHENTICATION_ENABLED=false\n'
  printf 'PHONE_ACCESSED=false\n'
  printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/boot-manifest.env"
chmod 0644 "$output_dir/boot-info.txt" "$output_dir/boot-manifest.env" "$enabled_dtb"

printf 'FP6 fingerprint QSEE-log boot candidate: %s\n' "$candidate"
