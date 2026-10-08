#!/usr/bin/env python3
"""Derive the no-SELinux libbinder boundary for the FP6 C1 container.

QREL 16.95.0's ProcessState::becomeContextManager() asks Binder to attach a
security SID to every transaction received by the context manager.  The Luma
Fedora kernel deliberately has no Android SELinux policy active, so the kernel
cannot translate a secid and rejects every transaction with EOPNOTSUPP.

This builder removes that flag both from the context-manager registration
ioctl and from local Binder objects.  The latter is required because stock
framework services deliberately call BBinder::setRequestingSid(true), which
otherwise makes the Fedora kernel reject transactions to those objects for
the same reason.  It does not alter the host kernel or any global device.  The
resulting library is used only inside the hardware-free C1 container, whose
boundary remains its private BinderFS instance, namespaces, and device
allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


LIBBINDER_SHA256 = (
    "2f35490b8093a9875858843946946e79866eec73dc51aff134749649b491fdf5"
)
LIBHIDLBASE_SHA256 = (
    "2a70073366c7d020609ffb1c251d5d6ade82ec46bdb81addafed435c20554fcd"
)
LIBBINDER_VNDK34_SHA256 = (
    "493d5f75a675f43f2b537ae098137669664e5b3944555798bf5d4415384c6dd1"
)

# Exact QREL 16.95.0 file offsets.  At 0x54ef0, becomeContextManager loads the
# 16-byte flat_binder_object prefix at 0x2aeb8.  Its second uint32_t is flags.
CONTEXT_MANAGER_FUNCTION_OFFSET = 0x54EBC
CONTEXT_MANAGER_LOAD_OFFSET = 0x54EF0
CONTEXT_MANAGER_OBJECT_OFFSET = 0x2AEB8
FUNCTION_PREFIX = bytes.fromhex(
    "3f2303d5ff8301d1fd7b03a9f52300f9f44f05a9fdc30091"
)
CONSTANT_LOAD = bytes.fromhex("48feea10")  # adr x8, 0x2aeb8
OBJECT_PREFIX_WITH_SID = bytes.fromhex(
    "00000000001000000000000000000000"
)
OBJECT_PREFIX_WITHOUT_SID = bytes.fromhex(
    "00000000000000000000000000000000"
)

# Exact QREL 16.95.0 BBinder::isRequestingSid() identity.  Preserve BTI at the
# entry point and replace only its body with `mov w0, wzr; ret`, so an object's
# stock setRequestingSid() call cannot ask a non-Android-SELinux kernel for a
# transaction security context.  This compatibility choice is confined to
# C1's private BinderFS registry.
IS_REQUESTING_SID_OFFSET = 0x612C4
IS_REQUESTING_SID_ORIGINAL = bytes.fromhex(
    "5f2403d50820009108fddfc8480000b40841403900010012c0035fd6"
)
IS_REQUESTING_SID_WITHOUT_SELINUX = bytes.fromhex(
    "5f2403d5e0031f2ac0035fd61f2003d51f2003d51f2003d51f2003d5"
)

HIDL_CONTEXT_MANAGER_FUNCTION_OFFSET = 0x8CFFC
HIDL_CONTEXT_MANAGER_LOAD_OFFSET = 0x8D034
HIDL_CONTEXT_MANAGER_OBJECT_OFFSET = 0x283E0
HIDL_FUNCTION_PREFIX = bytes.fromhex(
    "3f2303d5ff4301d1fd7b03a9f44f04a9fdc3009154d03bd5"
)
HIDL_CONSTANT_LOAD = bytes.fromhex("689dcd10")
HIDL_IS_REQUESTING_SID_OFFSET = 0x7FDEC
HIDL_IS_REQUESTING_SID_ORIGINAL = bytes.fromhex(
    "5f2403d50840009108fddfc8480000b40801403900010012c0035fd6"
)

VNDK34_CONTEXT_MANAGER_FUNCTION_OFFSET = 0x68FF0
VNDK34_CONTEXT_MANAGER_LOAD_OFFSET = 0x6902C
VNDK34_CONTEXT_MANAGER_OBJECT_OFFSET = 0x41AF0
VNDK34_FUNCTION_PREFIX = bytes.fromhex(
    "ff8301d1fd7b03a9f52300f9f44f05a9fdc3009155d03bd5"
)
VNDK34_CONSTANT_LOAD = bytes.fromhex("2856ec10")
VNDK34_IS_REQUESTING_SID_OFFSET = 0x7463C
VNDK34_IS_REQUESTING_SID_ORIGINAL = bytes.fromhex(
    "0820009108fddfc8a80000b4084140391f010071e0079f1ac0035fd6e0031f2ac0035fd6"
)

MOV_FALSE_RET_WITH_BTI = bytes.fromhex(
    "5f2403d5e0031f2ac0035fd61f2003d51f2003d51f2003d51f2003d5"
)
MOV_FALSE_RET_VNDK34 = bytes.fromhex(
    "e0031f2ac0035fd61f2003d51f2003d51f2003d51f2003d51f2003d51f2003d51f2003d5"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--hidlbase", required=True, type=Path)
    parser.add_argument("--vndk34", required=True, type=Path)
    args = parser.parse_args()

    if sha256(args.input) != LIBBINDER_SHA256:
        raise SystemExit("factory libbinder input hash differs")
    if sha256(args.hidlbase) != LIBHIDLBASE_SHA256:
        raise SystemExit("factory libhidlbase input hash differs")
    if sha256(args.vndk34) != LIBBINDER_VNDK34_SHA256:
        raise SystemExit("factory VNDK 34 libbinder input hash differs")
    if args.output_dir.exists():
        raise SystemExit(f"refuse to overwrite output: {args.output_dir}")

    original = args.input.read_bytes()
    if original[
        CONTEXT_MANAGER_FUNCTION_OFFSET :
        CONTEXT_MANAGER_FUNCTION_OFFSET + len(FUNCTION_PREFIX)
    ] != FUNCTION_PREFIX:
        raise SystemExit("becomeContextManager function identity differs")
    if original[
        CONTEXT_MANAGER_LOAD_OFFSET :
        CONTEXT_MANAGER_LOAD_OFFSET + len(CONSTANT_LOAD)
    ] != CONSTANT_LOAD:
        raise SystemExit("context-manager constant load differs")
    if original[
        CONTEXT_MANAGER_OBJECT_OFFSET :
        CONTEXT_MANAGER_OBJECT_OFFSET + len(OBJECT_PREFIX_WITH_SID)
    ] != OBJECT_PREFIX_WITH_SID:
        raise SystemExit("context-manager flat_binder_object differs")
    if original[
        IS_REQUESTING_SID_OFFSET :
        IS_REQUESTING_SID_OFFSET + len(IS_REQUESTING_SID_ORIGINAL)
    ] != IS_REQUESTING_SID_ORIGINAL:
        raise SystemExit("BBinder::isRequestingSid identity differs")

    blob = bytearray(original)
    start = CONTEXT_MANAGER_OBJECT_OFFSET
    end = start + len(OBJECT_PREFIX_WITH_SID)
    blob[start:end] = OBJECT_PREFIX_WITHOUT_SID
    start = IS_REQUESTING_SID_OFFSET
    end = start + len(IS_REQUESTING_SID_ORIGINAL)
    blob[start:end] = IS_REQUESTING_SID_WITHOUT_SELINUX

    observed = [
        offset
        for offset, (before, after) in enumerate(zip(original, blob))
        if before != after
    ]
    expected_changed = [
        CONTEXT_MANAGER_OBJECT_OFFSET + 5,
        *range(
            IS_REQUESTING_SID_OFFSET + 4,
            IS_REQUESTING_SID_OFFSET + len(IS_REQUESTING_SID_ORIGINAL),
        ),
    ]
    expected_changed = [
        offset
        for offset in expected_changed
        if original[offset] != blob[offset]
    ]
    if observed != expected_changed:
        raise SystemExit(
            f"unexpected binary delta: expected={expected_changed} observed={observed}"
        )

    hidl_original = args.hidlbase.read_bytes()
    if hidl_original[
        HIDL_CONTEXT_MANAGER_FUNCTION_OFFSET :
        HIDL_CONTEXT_MANAGER_FUNCTION_OFFSET + len(HIDL_FUNCTION_PREFIX)
    ] != HIDL_FUNCTION_PREFIX:
        raise SystemExit("HIDL becomeContextManager function identity differs")
    if hidl_original[
        HIDL_CONTEXT_MANAGER_LOAD_OFFSET :
        HIDL_CONTEXT_MANAGER_LOAD_OFFSET + len(HIDL_CONSTANT_LOAD)
    ] != HIDL_CONSTANT_LOAD:
        raise SystemExit("HIDL context-manager constant load differs")
    if hidl_original[
        HIDL_CONTEXT_MANAGER_OBJECT_OFFSET :
        HIDL_CONTEXT_MANAGER_OBJECT_OFFSET + len(OBJECT_PREFIX_WITH_SID)
    ] != OBJECT_PREFIX_WITH_SID:
        raise SystemExit("HIDL context-manager flat_binder_object differs")
    if hidl_original[
        HIDL_IS_REQUESTING_SID_OFFSET :
        HIDL_IS_REQUESTING_SID_OFFSET + len(HIDL_IS_REQUESTING_SID_ORIGINAL)
    ] != HIDL_IS_REQUESTING_SID_ORIGINAL:
        raise SystemExit("BHwBinder::isRequestingSid identity differs")
    hidl_blob = bytearray(hidl_original)
    hidl_blob[
        HIDL_CONTEXT_MANAGER_OBJECT_OFFSET :
        HIDL_CONTEXT_MANAGER_OBJECT_OFFSET + len(OBJECT_PREFIX_WITH_SID)
    ] = OBJECT_PREFIX_WITHOUT_SID
    hidl_blob[
        HIDL_IS_REQUESTING_SID_OFFSET :
        HIDL_IS_REQUESTING_SID_OFFSET + len(HIDL_IS_REQUESTING_SID_ORIGINAL)
    ] = MOV_FALSE_RET_WITH_BTI

    vndk34_original = args.vndk34.read_bytes()
    if vndk34_original[
        VNDK34_CONTEXT_MANAGER_FUNCTION_OFFSET :
        VNDK34_CONTEXT_MANAGER_FUNCTION_OFFSET + len(VNDK34_FUNCTION_PREFIX)
    ] != VNDK34_FUNCTION_PREFIX:
        raise SystemExit("VNDK 34 becomeContextManager function identity differs")
    if vndk34_original[
        VNDK34_CONTEXT_MANAGER_LOAD_OFFSET :
        VNDK34_CONTEXT_MANAGER_LOAD_OFFSET + len(VNDK34_CONSTANT_LOAD)
    ] != VNDK34_CONSTANT_LOAD:
        raise SystemExit("VNDK 34 context-manager constant load differs")
    if vndk34_original[
        VNDK34_CONTEXT_MANAGER_OBJECT_OFFSET :
        VNDK34_CONTEXT_MANAGER_OBJECT_OFFSET + len(OBJECT_PREFIX_WITH_SID)
    ] != OBJECT_PREFIX_WITH_SID:
        raise SystemExit("VNDK 34 context-manager flat_binder_object differs")
    if vndk34_original[
        VNDK34_IS_REQUESTING_SID_OFFSET :
        VNDK34_IS_REQUESTING_SID_OFFSET + len(VNDK34_IS_REQUESTING_SID_ORIGINAL)
    ] != VNDK34_IS_REQUESTING_SID_ORIGINAL:
        raise SystemExit("VNDK 34 BBinder::isRequestingSid identity differs")
    vndk34_blob = bytearray(vndk34_original)
    vndk34_blob[
        VNDK34_CONTEXT_MANAGER_OBJECT_OFFSET :
        VNDK34_CONTEXT_MANAGER_OBJECT_OFFSET + len(OBJECT_PREFIX_WITH_SID)
    ] = OBJECT_PREFIX_WITHOUT_SID
    vndk34_blob[
        VNDK34_IS_REQUESTING_SID_OFFSET :
        VNDK34_IS_REQUESTING_SID_OFFSET + len(VNDK34_IS_REQUESTING_SID_ORIGINAL)
    ] = MOV_FALSE_RET_VNDK34

    args.output_dir.mkdir(parents=True)
    output = args.output_dir / "libbinder.so"
    output.write_bytes(blob)
    output.chmod(0o755)
    hidl_output = args.output_dir / "libhidlbase.so"
    hidl_output.write_bytes(hidl_blob)
    hidl_output.chmod(0o755)
    vndk34_output = args.output_dir / "libbinder-vndk34.so"
    vndk34_output.write_bytes(vndk34_blob)
    vndk34_output.chmod(0o755)

    manifest = {
        "format": 1,
        "factory_release": "FP6.QREL.16.95.0/WS1J",
        "source_branch": "refs/heads/android16-release",
        "source_file": "platform/frameworks/native/libs/binder/ProcessState.cpp",
        "source_url": (
            "https://android.googlesource.com/platform/frameworks/native/+/"
            "android16-release/libs/binder/ProcessState.cpp"
        ),
        "input": str(args.input),
        "input_sha256": LIBBINDER_SHA256,
        "hidlbase_input": str(args.hidlbase),
        "hidlbase_input_sha256": LIBHIDLBASE_SHA256,
        "vndk34_input": str(args.vndk34),
        "vndk34_input_sha256": LIBBINDER_VNDK34_SHA256,
        "output": str(output),
        "output_sha256": sha256(output),
        "hidlbase_output": str(hidl_output),
        "hidlbase_output_sha256": sha256(hidl_output),
        "vndk34_output": str(vndk34_output),
        "vndk34_output_sha256": sha256(vndk34_output),
        "patches": [
            {
                "file_offset": CONTEXT_MANAGER_OBJECT_OFFSET + 4,
                "before": "00100000",
                "after": "00000000",
                "source_mapping": (
                    "ProcessState::becomeContextManager: register the private "
                    "context manager without FLAT_BINDER_FLAG_TXN_SECURITY_CTX"
                ),
            },
            {
                "file_offset": IS_REQUESTING_SID_OFFSET,
                "before": IS_REQUESTING_SID_ORIGINAL.hex(),
                "after": IS_REQUESTING_SID_WITHOUT_SELINUX.hex(),
                "source_mapping": (
                    "BBinder::isRequestingSid: return false because C1 runs "
                    "without Android SELinux and uses a private BinderFS"
                ),
            },
            {
                "file": "libhidlbase.so",
                "context_manager_object_offset": HIDL_CONTEXT_MANAGER_OBJECT_OFFSET,
                "is_requesting_sid_offset": HIDL_IS_REQUESTING_SID_OFFSET,
                "source_mapping": (
                    "hardware Binder context and local objects: do not "
                    "request Android SELinux transaction SIDs"
                ),
            },
            {
                "file": "libbinder-vndk34.so",
                "context_manager_object_offset": VNDK34_CONTEXT_MANAGER_OBJECT_OFFSET,
                "is_requesting_sid_offset": VNDK34_IS_REQUESTING_SID_OFFSET,
                "source_mapping": (
                    "vendor Binder context and local objects: do not request "
                    "Android SELinux transaction SIDs"
                ),
            },
        ],
        "changed_byte_count": len(observed),
        "selinux_enforcement": False,
        "requesting_transaction_sid": False,
        "host_kernel_modified": False,
        "global_binder_device_modified": False,
        "scope": "hardware-free C1 container only",
        "security_boundary": [
            "private BinderFS devices",
            "private process/mount/ipc namespaces",
            "container device allowlist",
        ],
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o644)

    print(f"FP6 stock Android Binder boundary: {args.output_dir}")
    print(f"libbinder_sha256={manifest['output_sha256']}")
    print(f"libhidlbase_sha256={manifest['hidlbase_output_sha256']}")
    print(f"libbinder_vndk34_sha256={manifest['vndk34_output_sha256']}")
    print(f"manifest_sha256={sha256(manifest_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
