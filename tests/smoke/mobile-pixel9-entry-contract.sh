#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT
valid=$test_root/valid-arm64-pe.img
raw=$test_root/raw-arm64.img
valid_report=$test_root/valid.env
raw_report=$test_root/raw.env

python3 - "$valid" "$raw" <<'PY'
import pathlib
import struct
import sys

valid_path = pathlib.Path(sys.argv[1])
raw_path = pathlib.Path(sys.argv[2])

valid = bytearray(0x400)
valid[0:2] = b'MZ'
struct.pack_into('<Q', valid, 16, len(valid))
valid[56:60] = b'ARMd'
struct.pack_into('<I', valid, 0x3c, 0x40)
valid[0x40:0x44] = b'PE\0\0'
struct.pack_into('<HHIIIHH', valid, 0x44, 0xaa64, 1, 0, 0, 0, 0xa0, 0x0222)
optional = 0x58
struct.pack_into('<H', valid, optional, 0x20b)
struct.pack_into('<I', valid, optional + 16, 0x10000)
struct.pack_into('<I', valid, optional + 20, 0x10000)
struct.pack_into('<I', valid, optional + 32, 0x10000)
struct.pack_into('<I', valid, optional + 36, 0x200)
struct.pack_into('<I', valid, optional + 56, 0x20000)
struct.pack_into('<I', valid, optional + 60, 0x200)
struct.pack_into('<H', valid, optional + 68, 10)
section = optional + 0xa0
valid[section:section + 8] = b'.text\0\0\0'
struct.pack_into('<IIII', valid, section + 8, 0x100, 0x10000, 0x200, 0x200)
valid_path.write_bytes(valid)

raw = bytearray(0x400)
raw[0:4] = b'\x18\x00\x00\x14'
struct.pack_into('<Q', raw, 16, len(raw))
raw[56:60] = b'ARMd'
raw_path.write_bytes(raw)
PY

inspector=$repo_root/scripts/mobile/inspect-pixel9-kernel-entry.py
"$inspector" --require-stock-contract "$valid" >"$valid_report"
grep -Fqx 'ARM64_IMAGE_MAGIC_PRESENT=true' "$valid_report"
grep -Fqx 'PE_COFF_HEADER_PRESENT=true' "$valid_report"
grep -Fqx 'PE_MACHINE=0xaa64' "$valid_report"
grep -Fqx 'PE_SUBSYSTEM=10' "$valid_report"
grep -Fqx 'PE_ENTRY_IN_SECTION=true' "$valid_report"
grep -Fqx 'ENTRY_CONTRACT_MATCHES_STOCK=true' "$valid_report"

"$inspector" "$raw" >"$raw_report"
grep -Fqx 'ARM64_IMAGE_MAGIC_PRESENT=true' "$raw_report"
grep -Fqx 'DOS_MZ_SIGNATURE_PRESENT=false' "$raw_report"
grep -Fqx 'PE_COFF_HEADER_PRESENT=false' "$raw_report"
grep -Fqx 'ENTRY_CONTRACT_MATCHES_STOCK=false' "$raw_report"
if "$inspector" --require-stock-contract "$raw" >/dev/null 2>&1; then
  printf 'Pixel 9 kernel entry contract: FAIL: raw ARM64 payload was accepted\n' >&2
  exit 1
fi

printf 'Pixel 9 kernel entry contract fixtures: PASS\n'
