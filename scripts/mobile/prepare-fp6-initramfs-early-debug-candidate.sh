#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Compose an FP6 recovery image that enters the stock postmarketOS debug shell
# before udev, Plymouth, display/GPU initialization, or rootfs discovery.

set -euo pipefail
umask 022

control_boot=${1:?usage: prepare-fp6-initramfs-early-debug-candidate.sh CONTROL_BOOT OUTPUT_DIR}
output_dir=${2:?missing output directory}
expected_control_sha=5885e62324115ae9cce929318df24251992ef705952dcbe6b16cef549a505efb
candidate=$output_dir/boot-fp6-initramfs-early-debug-v3.img

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cpio gzip mkbootimg mktemp python3 sha256sum stat touch unpack_bootimg; do
	command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done
[ -f "$control_boot" ] || die 'control boot missing'
[ "$(hash "$control_boot")" = "$expected_control_sha" ] || die 'control boot hash differs'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

mkdir -p "$output_dir"
work=$(mktemp -d "$output_dir/repack.XXXXXX")
cleanup() { [ ! -e "$work" ] || find "$work" -depth -delete; }
trap cleanup EXIT HUP INT TERM
mkdir -p "$work/base" "$work/check" "$work/ramdisk" "$work/overlay"

unpack_bootimg --boot_img "$control_boot" --out "$work/base" --format=mkbootimg -0 >"$work/args0"
(
	cd "$work/ramdisk"
	gzip -dc "$work/base/ramdisk" | cpio -idm --quiet
)
python3 - "$work/ramdisk/init_2nd.sh" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text()
anchor = "setup_udev\n\nsetup_usb_network\nstart_unudhcpd\n"
early = """# Recovery-only gate: expose USB networking/ACM and stop before udev,
# Plymouth, display/GPU initialization, or rootfs discovery.
if [ "$debug_shell" = "y" ]; then
\tsetup_usb_network
\tstart_unudhcpd
\tdebug_shell
fi

"""
anchor_count = text.count(anchor)
if anchor_count != 1:
    raise SystemExit(f"initramfs debug anchor differs: early={anchor_count}")
text = text.replace(anchor, early + anchor, 1)
path.write_text(text)
PY

install -m 0755 "$work/ramdisk/init_2nd.sh" "$work/overlay/init_2nd.sh"
touch -h -d '2026-08-22 00:00:00 UTC' "$work/overlay" "$work/overlay/init_2nd.sh"
(
	cd "$work/overlay"
	find . -print0 | LC_ALL=C sort -z |
		cpio --null --create --format=newc --owner=0:0 --reproducible --quiet >"$work/overlay.cpio"
)
gzip -dc "$work/base/ramdisk" >"$work/base.cpio"
python3 - "$work/base.cpio" "$work/overlay.cpio" "$work/combined.cpio" <<'PY'
import pathlib
import sys

base, overlay, output = map(pathlib.Path, sys.argv[1:])
output.write_bytes(base.read_bytes() + overlay.read_bytes())
PY
gzip -n <"$work/combined.cpio" >"$work/ramdisk-early-debug.cpio.gz"
python3 - "$work/args0" "$work/ramdisk-early-debug.cpio.gz" "$candidate" <<'PY'
import os
import subprocess
import sys

args_path, ramdisk, output = sys.argv[1:]
args = open(args_path, "rb").read().split(b"\0")
if args and args[-1] == b"":
    args.pop()
try:
    index = args.index(b"--ramdisk")
except ValueError as error:
    raise SystemExit("ramdisk argument absent") from error
args[index + 1] = os.fsencode(ramdisk)
try:
    index = args.index(b"--cmdline")
except ValueError:
    args.extend((b"--cmdline", b"pmos.debug-shell"))
else:
    args[index + 1] = (args[index + 1].strip() + b" pmos.debug-shell").strip()
subprocess.run(
    ["mkbootimg", "--output", output, *(os.fsdecode(arg) for arg in args)],
    check=True,
)
PY

unpack_bootimg --boot_img "$candidate" --out "$work/check" >"$output_dir/boot-info.txt"
grep -Fq 'pmos.debug-shell' "$output_dir/boot-info.txt" || die 'debug-shell cmdline absent'
[ "$(hash "$work/base/kernel")" = "$(hash "$work/check/kernel")" ] || die 'kernel changed'
[ "$(hash "$work/base/dtb")" = "$(hash "$work/check/dtb")" ] || die 'DTB changed'
[ "$(hash "$work/ramdisk-early-debug.cpio.gz")" = "$(hash "$work/check/ramdisk")" ] || die 'ramdisk repack differs'
[ "$(stat -c %s "$candidate")" -le $((96 * 1024 * 1024)) ] || die 'candidate exceeds boot partition'

{
	printf 'LUMA_FP6_INITRAMFS_EARLY_DEBUG_VERSION=3\n'
	printf 'CONTROL_BOOT_SHA256=%s\n' "$expected_control_sha"
	printf 'KERNEL_SHA256=%s\n' "$(hash "$work/check/kernel")"
	printf 'RAMDISK_SHA256=%s\n' "$(hash "$work/check/ramdisk")"
	printf 'DTB_SHA256=%s\n' "$(hash "$work/check/dtb")"
	printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
	printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
	printf 'EARLY_DEBUG_BEFORE_UDEV=true\n'
	printf 'ORIGINAL_INITRAMFS_ARCHIVE_PRESERVED=true\n'
	printf 'OVERRIDE_ARCHIVE_APPENDED=true\n'
	printf 'ROOTFS_MOUNTED=false\n'
	printf 'ROOTFS_MODIFIED=false\n'
	printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"

printf 'FP6 initramfs early-debug candidate: %s\n' "$candidate"
