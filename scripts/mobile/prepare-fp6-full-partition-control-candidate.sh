#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Recreate the FP6 boot-partition layout used by the original successful
# postmarketOS installation: Linux boot payload at offset zero with the exact
# official QREL 16.95.0 partition tail and AVB footer retained.

set -euo pipefail
umask 022

stock_boot=${1:?usage: prepare-fp6-full-partition-control-candidate.sh STOCK_BOOT CONTROL_BOOT OUTPUT_DIR}
control_boot=${2:?missing control boot}
output_dir=${3:?missing output directory}
stock_sha=bea4915daf875a340a71677a4706263a1a39d0a5b01da468e517783c9ce4579f
stock_size=100663296
control_sha=5885e62324115ae9cce929318df24251992ef705952dcbe6b16cef549a505efb
control_size=27504640
candidate=$output_dir/boot-fp6-full-partition-control-v1.img

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dd mkdir python3 sha256sum stat tail; do
	command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done
[ -f "$stock_boot" ] && [ -f "$control_boot" ] || die 'input missing'
[ "$(hash "$stock_boot")" = "$stock_sha" ] || die 'stock boot hash differs'
[ "$(stat -c %s "$stock_boot")" = "$stock_size" ] || die 'stock boot size differs'
[ "$(hash "$control_boot")" = "$control_sha" ] || die 'control boot hash differs'
[ "$(stat -c %s "$control_boot")" = "$control_size" ] || die 'control boot size differs'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

mkdir -p "$output_dir"
dd if="$stock_boot" of="$candidate" bs=4M status=none
dd if="$control_boot" of="$candidate" bs=4M conv=notrunc status=none
[ "$(stat -c %s "$candidate")" = "$stock_size" ] || die 'candidate size differs'
cmp -n "$control_size" "$control_boot" "$candidate"
python3 - "$stock_boot" "$candidate" "$control_size" <<'PY'
import pathlib
import sys

stock_path, candidate_path, prefix_size = sys.argv[1:]
prefix_size = int(prefix_size)
stock = pathlib.Path(stock_path).read_bytes()
candidate = pathlib.Path(candidate_path).read_bytes()
if candidate[prefix_size:] != stock[prefix_size:]:
    raise SystemExit("official partition tail differs")
PY

stock_footer_sha=$(tail -c 64 "$stock_boot" | sha256sum | awk '{print $1}')
candidate_footer_sha=$(tail -c 64 "$candidate" | sha256sum | awk '{print $1}')
[ "$candidate_footer_sha" = "$stock_footer_sha" ] || die 'AVB footer differs'

{
	printf 'LUMA_FP6_FULL_PARTITION_CONTROL_VERSION=1\n'
	printf 'STOCK_BOOT_SHA256=%s\n' "$stock_sha"
	printf 'STOCK_BOOT_SIZE=%s\n' "$stock_size"
	printf 'CONTROL_BOOT_SHA256=%s\n' "$control_sha"
	printf 'CONTROL_BOOT_SIZE=%s\n' "$control_size"
	printf 'OFFICIAL_TAIL_OFFSET=%s\n' "$control_size"
	printf 'OFFICIAL_AVB_FOOTER_SHA256=%s\n' "$stock_footer_sha"
	printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
	printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
	printf 'CONTROL_PREFIX_EXACT=true\n'
	printf 'OFFICIAL_PARTITION_TAIL_EXACT=true\n'
	printf 'OFFICIAL_AVB_FOOTER_PRESERVED=true\n'
	printf 'REQUIRES_UNLOCKED_BOOTLOADER=true\n'
	printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"

printf 'FP6 full-partition control candidate: %s\n' "$candidate"
