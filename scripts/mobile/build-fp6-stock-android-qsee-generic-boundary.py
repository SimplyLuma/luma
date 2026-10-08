#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Derive the bounded FP6 QSEE module that selects its generic TZMEM path.

The accepted BinderFS/IMS kernel inherited an experimental DT property for
dedicated QSEE heaps.  Its ordinary QSEE module consequently enters the
dedicated path and fails before registering a TEE device because Linux cannot
attach the already-reserved TA heap a second time.  The same module contains
the previously accepted generic SHM-Bridge allocator.  This derivation changes
only the one conditional branch selecting those paths; every other byte is
preserved and the input/output identities are recorded.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import sys


EXPECTED_INPUT_SHA256 = "3ab2993101346a2e24ad34e94599f1e67ece023100005b0a643793303fe7ff03"
# .text VMA 0xc0, file offset 0x258: tbz w0,#0,0x14c.  Replace it with an
# unconditional branch to the exact existing generic-allocation continuation.
PATCH_OFFSET = 0x258
EXPECTED_INSTRUCTION = bytes.fromhex("60040036")
GENERIC_BRANCH = bytes.fromhex("23000014")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} INPUT_QSEECOMTEE_KO OUTPUT_DIR")
    source = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    if not source.is_file() or source.is_symlink():
        raise RuntimeError(f"input is absent or linked: {source}")
    if output_dir.exists():
        raise RuntimeError(f"refuse to overwrite output: {output_dir}")

    original = source.read_bytes()
    actual = sha256(original)
    if actual != EXPECTED_INPUT_SHA256:
        raise RuntimeError(f"input hash mismatch: {actual}")
    if original[:4] != b"\x7fELF" or original[4] != 2 or original[5] != 1:
        raise RuntimeError("input is not ELF64 little-endian")
    if original.count(EXPECTED_INSTRUCTION) != 1:
        raise RuntimeError("dedicated-heap selector instruction is not unique")
    if original[PATCH_OFFSET : PATCH_OFFSET + 4] != EXPECTED_INSTRUCTION:
        raise RuntimeError("dedicated-heap selector offset differs")

    derived = bytearray(original)
    derived[PATCH_OFFSET : PATCH_OFFSET + 4] = GENERIC_BRANCH
    if sum(a != b for a, b in zip(original, derived, strict=True)) != 3:
        raise RuntimeError("unexpected byte-difference count")

    output_dir.mkdir(parents=True, mode=0o700)
    output = output_dir / "qseecomtee.ko"
    output.write_bytes(derived)
    output.chmod(stat.S_IRUSR | stat.S_IWUSR)
    manifest = {
        "version": 1,
        "scope": "RAM-only FP6 stock-Android KeyMint construction gate",
        "input": str(source),
        "input_sha256": actual,
        "output_sha256": sha256(bytes(derived)),
        "patch_offset": PATCH_OFFSET,
        "before_instruction_le": EXPECTED_INSTRUCTION.hex(),
        "after_instruction_le": GENERIC_BRANCH.hex(),
        "semantic_change": (
            "select existing generic TZMEM/SHM-Bridge allocation path instead "
            "of experimental dedicated-heaps path"
        ),
        "partition_written": False,
        "phone_accessed": False,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"QSEECOMTEE_SHA256={manifest['output_sha256']}")
    print(f"MANIFEST_SHA256={sha256(manifest_path.read_bytes())}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
