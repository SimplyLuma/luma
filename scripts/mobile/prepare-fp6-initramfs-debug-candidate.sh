#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose a recovery-only FP6 control boot that pauses in postmarketOS's
# built-in initramfs debug shell before mounting or switching to the rootfs.

set -euo pipefail
umask 022

control_boot=${1:?usage: prepare-fp6-initramfs-debug-candidate.sh CONTROL_BOOT OUTPUT_DIR}
output_dir=${2:?missing output directory}
expected_control_sha=5885e62324115ae9cce929318df24251992ef705952dcbe6b16cef549a505efb
candidate=$output_dir/boot-fp6-initramfs-debug-v1.img

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp mkbootimg mktemp python3 sha256sum stat unpack_bootimg; do
	command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done
[ -f "$control_boot" ] || die 'control boot missing'
[ "$(hash "$control_boot")" = "$expected_control_sha" ] || die 'control boot hash differs'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

mkdir -p "$output_dir"
work=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() { [ ! -e "$work" ] || find "$work" -depth -delete; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/base" "$work/check"

unpack_bootimg --boot_img "$control_boot" --out "$work/base" --format=mkbootimg -0 >"$work/args0"
python3 - "$work/args0" "$candidate" <<'PY'
import os
import subprocess
import sys

args_path, output = sys.argv[1:]
args = open(args_path, "rb").read().split(b"\0")
if args and args[-1] == b"":
    args.pop()
try:
    index = args.index(b"--cmdline")
except ValueError:
    args.extend((b"--cmdline", b"pmos.debug-shell"))
else:
    current = args[index + 1].strip()
    args[index + 1] = (current + b" pmos.debug-shell").strip()
subprocess.run(
    ["mkbootimg", "--output", output, *(os.fsdecode(arg) for arg in args)],
    check=True,
)
PY

unpack_bootimg --boot_img "$candidate" --out "$work/check" >"$output_dir/boot-info.txt"
cmp "$work/base/kernel" "$work/check/kernel"
cmp "$work/base/ramdisk" "$work/check/ramdisk"
cmp "$work/base/dtb" "$work/check/dtb"
grep -Fq 'pmos.debug-shell' "$output_dir/boot-info.txt" || die 'debug-shell cmdline absent'
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] || die 'candidate exceeds boot partition'

{
	printf 'LUMA_FP6_INITRAMFS_DEBUG_VERSION=1\n'
	printf 'CONTROL_BOOT_SHA256=%s\n' "$expected_control_sha"
	printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
	printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
	printf 'KERNEL_UNCHANGED=true\n'
	printf 'RAMDISK_UNCHANGED=true\n'
	printf 'DTB_UNCHANGED=true\n'
	printf 'ROOTFS_MOUNTED=false\n'
	printf 'ROOTFS_MODIFIED=false\n'
	printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"

printf 'FP6 initramfs debug candidate: %s\n' "$candidate"
