#!/usr/bin/env python3
"""Build the audited no-SELinux manager boundary for the FP6 Android container.

The phone's Fedora kernel does not run SELinux, while the exact QREL 16.95.0
Android service managers require Android SELinux service labels and request
Binder transaction SIDs.  Per-process preload shims are explicitly excluded
from the container baseline.  This tool therefore derives three hash-locked
manager binaries from the exact factory inputs and changes only the source-
mapped access-control and SID-request instructions.

This is not a claim of policy enforcement.  The resulting registries permit
all clients inside their private BinderFS instance.  Their security boundary
is the container's private Binder devices, namespaces, and device allowlist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


SERVICE_MANAGER_SHA256 = (
    "626eaac3336fcf91e3510aaf920d7fa7882733ba1648ab9a432b0532a1570c70"
)
HW_SERVICE_MANAGER_SHA256 = (
    "c9b8ced988965bdb53dbe2e98b3f3d6b30da09c5376606c9353377c820e5ba1f"
)
VND_SERVICE_MANAGER_SHA256 = (
    "f44e086d89c6b7dfb6b9bd4c8886735ab51850e61f8e52b3947e4fb690755978"
)

# AArch64 instructions, little endian.
BTI_C = bytes.fromhex("5f2403d5")
MOV_W0_1 = bytes.fromhex("20008052")
MOV_W1_0 = bytes.fromhex("01008052")
MOV_X0_0 = bytes.fromhex("e0031faa")
RET = bytes.fromhex("c0035fd6")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def edit(blob: bytearray, offset: int, expected: bytes, replacement: bytes) -> None:
    actual = bytes(blob[offset : offset + len(expected)])
    if actual != expected:
        raise SystemExit(
            f"input bytes differ at 0x{offset:x}: "
            f"expected={expected.hex()} actual={actual.hex()}"
        )
    if len(expected) != len(replacement):
        raise SystemExit(f"patch size differs at 0x{offset:x}")
    blob[offset : offset + len(replacement)] = replacement


def derive(
    source: Path,
    destination: Path,
    expected_sha: str,
    patches: list[dict[str, object]],
) -> dict[str, object]:
    if sha256(source) != expected_sha:
        raise SystemExit(f"factory input hash differs: {source}")
    blob = bytearray(source.read_bytes())
    for patch in patches:
        edit(
            blob,
            int(patch["offset"]),
            bytes.fromhex(str(patch["before"])),
            bytes.fromhex(str(patch["after"])),
        )
    destination.write_bytes(blob)
    destination.chmod(0o755)

    changed = {index for patch in patches for index in range(
        int(patch["offset"]),
        int(patch["offset"]) + len(bytes.fromhex(str(patch["before"]))),
    )}
    original = source.read_bytes()
    observed = {index for index, pair in enumerate(zip(original, blob)) if pair[0] != pair[1]}
    if not observed or not observed.issubset(changed):
        raise SystemExit(f"unexpected binary delta for {source.name}")

    return {
        "input": str(source),
        "input_sha256": expected_sha,
        "output": str(destination),
        "output_sha256": sha256(destination),
        "changed_byte_count": len(observed),
        "allowed_patch_byte_count": len(changed),
        "patches": patches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    if args.output_dir.exists():
        raise SystemExit(f"refuse to overwrite output: {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    sm_patches: list[dict[str, object]] = [
        {
            "offset": 0xC44C,
            "before": "ab320094",
            "after": "0b000014",
            "source_mapping": "Access::Access: skip SELinux setup after vtable/member initialization",
        },
        {
            "offset": 0xC9EC,
            "before": "e80302aae00301aa",
            "after": (MOV_W0_1 + RET).hex(),
            "source_mapping": "Access::canFind: allow within private BinderFS registry",
        },
        {
            "offset": 0xC724,
            "before": "79320094",
            "after": MOV_X0_0.hex(),
            "source_mapping": (
                "Access::getCallingContext/getPidcon: do not abort when the "
                "private no-SELinux registry receives a transaction without "
                "a security SID; retain the stock empty-context fallback"
            ),
        },
        {
            "offset": 0xCBF0,
            "before": "e80302aae00301aa",
            "after": (MOV_W0_1 + RET).hex(),
            "source_mapping": "Access::canAdd: allow within private BinderFS registry",
        },
        {
            "offset": 0xCC08,
            "before": "3f2303d5ff4301d1fd7b03a9",
            "after": (BTI_C + MOV_W0_1 + RET).hex(),
            "source_mapping": "Access::canList: allow within private BinderFS registry",
        },
        {
            "offset": 0x1822C,
            "before": "21008052",
            "after": MOV_W1_0.hex(),
            "source_mapping": "main: manager->setRequestingSid(false)",
        },
    ]
    hwsm_patches: list[dict[str, object]] = [
        {
            "offset": 0xC2C0,
            "before": "21008052",
            "after": MOV_W1_0.hex(),
            "source_mapping": "main: setRequestingSid(manager, false)",
        },
        {
            "offset": 0xCDEC,
            "before": "bf2b0094",
            "after": "11000014",
            "source_mapping": "AccessControl::AccessControl: skip Android SELinux setup",
        },
        {
            "offset": 0xCE3C,
            "before": "bd2b0014",
            "after": RET.hex(),
            "source_mapping": "AccessControl::AccessControl: return instead of SELinux callback tail-call",
        },
        {
            "offset": 0xD964,
            "before": "4d290094",
            "after": MOV_X0_0.hex(),
            "source_mapping": (
                "AccessControl::getCallingContext/getPidcon: do not abort "
                "while serving a private hwbinder transaction without an "
                "Android SELinux SID; retain the stock empty-context fallback"
            ),
        },
        {
            "offset": 0xCEE0,
            "before": "3f2303d5ff4304d1fd7b0ba9",
            "after": (BTI_C + MOV_W0_1 + RET).hex(),
            "source_mapping": "AccessControl::canAdd: allow within private hwbinder registry",
        },
        {
            "offset": 0xD228,
            "before": "3f2303d5ff4304d1fd7b0ba9",
            "after": (BTI_C + MOV_W0_1 + RET).hex(),
            "source_mapping": "AccessControl::canGet: allow within private hwbinder registry",
        },
        {
            "offset": 0xD470,
            "before": "3f2303d5ff0301d1fd7b02a9",
            "after": (BTI_C + MOV_W0_1 + RET).hex(),
            "source_mapping": "AccessControl::canList: allow within private hwbinder registry",
        },
    ]
    vndsm_patches: list[dict[str, object]] = [
        {
            "offset": 0x6098,
            "before": "e41a0094",
            "after": "0b000014",
            "source_mapping": "Access::Access: skip SELinux setup after vtable/member initialization",
        },
        {
            "offset": 0x6508,
            "before": "5f2403d5e3ffffb063e03291",
            "after": (BTI_C + MOV_W0_1 + RET).hex(),
            "source_mapping": "Access::canAdd: allow within private vndbinder registry",
        },
        {
            "offset": 0x6700,
            "before": "5f2403d5e3ffffb0630c2491",
            "after": (BTI_C + MOV_W0_1 + RET).hex(),
            "source_mapping": "Access::canFind: allow within private vndbinder registry",
        },
        {
            "offset": 0x6710,
            "before": "3f2303d5ff4301d1fd7b03a9",
            "after": (BTI_C + MOV_W0_1 + RET).hex(),
            "source_mapping": "Access::checkPermission: allow within private vndbinder registry",
        },
        {
            "offset": 0xC1C4,
            "before": "21008052",
            "after": MOV_W1_0.hex(),
            "source_mapping": "main: manager->setRequestingSid(false)",
        },
    ]

    records = [
        derive(
            args.input_dir / "servicemanager.qrel1695",
            args.output_dir / "servicemanager",
            SERVICE_MANAGER_SHA256,
            sm_patches,
        ),
        derive(
            args.input_dir / "hwservicemanager.qrel1695",
            args.output_dir / "hwservicemanager",
            HW_SERVICE_MANAGER_SHA256,
            hwsm_patches,
        ),
        derive(
            args.input_dir / "vndservicemanager.qrel1695",
            args.output_dir / "vndservicemanager",
            VND_SERVICE_MANAGER_SHA256,
            vndsm_patches,
        ),
    ]
    manifest = {
        "format": 1,
        "factory_release": "FP6.QREL.16.95.0/WS1J",
        "source_branch": "refs/heads/android16-release",
        "source_files": [
            "platform/frameworks/native/cmds/servicemanager/main.cpp",
            "platform/frameworks/native/cmds/servicemanager/Access.cpp",
            "platform/system/hwservicemanager/service.cpp",
            "platform/system/hwservicemanager/AccessControl.cpp",
            "platform/frameworks/native/cmds/servicemanager/vndservicemanager.cpp",
        ],
        "selinux_enforcement": False,
        "requesting_transaction_sid": False,
        "preload_shim": False,
        "security_boundary": [
            "private binderfs devices",
            "private process/mount/ipc namespaces",
            "container device allowlist",
        ],
        "binaries": records,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o644)
    print(f"FP6 stock Android manager boundary: {args.output_dir}")
    for record in records:
        print(f"{Path(str(record['output'])).name}_sha256={record['output_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
