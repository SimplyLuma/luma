#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

# Place the verified slim matched-CMA v14 boot payload at offset zero of the
# exact official QREL 16.95.0 boot partition, preserving its remaining tail
# and AVB footer for the FP6 UEFI loader.

set -euo pipefail
umask 022

stock_boot=${1:?usage: prepare-fp6-full-partition-v14-candidate.sh STOCK_BOOT V14_BOOT OUTPUT_DIR}
v14_boot=${2:?missing v14 boot}
output_dir=${3:?missing output directory}
stock_sha=${LUMA_FP6_FULL_BASE_SHA256_OVERRIDE:-bea4915daf875a340a71677a4706263a1a39d0a5b01da468e517783c9ce4579f}
stock_size=100663296
v14_sha=${LUMA_FP6_FULL_PAYLOAD_SHA256_OVERRIDE:-5e95ae02a8a4c33f0decc1745b7ac067be43663b1095f19ef4065602d0546720}
v14_size=${LUMA_FP6_FULL_PAYLOAD_SIZE_OVERRIDE:-26566656}
candidate_name=${LUMA_FP6_FULL_CANDIDATE_NAME:-boot-fp6-full-partition-matched-cma-v14.img}
candidate=$output_dir/$candidate_name

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
hash() { sha256sum "$1" | awk '{print $1}'; }

for tool in cmp dd mkdir python3 sha256sum stat tail; do
	command -v "$tool" >/dev/null 2>&1 || die "missing tool: $tool"
done
[ -f "$stock_boot" ] && [ -f "$v14_boot" ] || die 'input missing'
[ "$(hash "$stock_boot")" = "$stock_sha" ] || die 'stock boot hash differs'
[ "$(stat -c %s "$stock_boot")" = "$stock_size" ] || die 'stock boot size differs'
[ "$(hash "$v14_boot")" = "$v14_sha" ] || die 'v14 boot hash differs'
[ "$(stat -c %s "$v14_boot")" = "$v14_size" ] || die 'v14 boot size differs'
[ ! -e "$output_dir" ] || die "refuse to overwrite output: $output_dir"

mkdir -p "$output_dir"
dd if="$stock_boot" of="$candidate" bs=4M status=none
dd if="$v14_boot" of="$candidate" bs=4M conv=notrunc status=none
[ "$(stat -c %s "$candidate")" = "$stock_size" ] || die 'candidate size differs'
cmp -n "$v14_size" "$v14_boot" "$candidate"
python3 - "$stock_boot" "$candidate" "$v14_size" <<'PY'
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
	printf 'LUMA_FP6_FULL_PARTITION_MATCHED_CMA_VERSION=14\n'
	printf 'STOCK_BOOT_SHA256=%s\n' "$stock_sha"
	printf 'STOCK_BOOT_SIZE=%s\n' "$stock_size"
	printf 'V14_BOOT_SHA256=%s\n' "$v14_sha"
	printf 'V14_BOOT_SIZE=%s\n' "$v14_size"
	printf 'OFFICIAL_TAIL_OFFSET=%s\n' "$v14_size"
	printf 'OFFICIAL_AVB_FOOTER_SHA256=%s\n' "$stock_footer_sha"
	printf 'CANDIDATE_SHA256=%s\n' "$(hash "$candidate")"
	printf 'CANDIDATE_SIZE=%s\n' "$(stat -c %s "$candidate")"
	printf 'V14_PREFIX_EXACT=true\n'
	printf 'OFFICIAL_PARTITION_TAIL_EXACT=true\n'
	printf 'OFFICIAL_AVB_FOOTER_PRESERVED=true\n'
	printf 'REQUIRES_UNLOCKED_BOOTLOADER=true\n'
	printf 'PARTITION_WRITTEN=false\n'
} >"$output_dir/manifest.env"

printf 'FP6 full-partition matched-CMA v14 candidate: %s\n' "$candidate"
