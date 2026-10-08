#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Derive the bounded no-SELinux QREL init used by the FP6 container.

The FP6 Linux kernel intentionally has no active SELinux LSM. QREL's stock
Android init nevertheless treats unavailable process transitions and peer
contexts as fatal before it can launch the framework. At exact, hash-gated
call sites, preserve the no-SELinux behavior expected in an LXC Android image:

* return an empty successful service transition only on getcon(3) failure;
* treat the two setexeccon(3) calls as no-ops;
* accept the already namespace-isolated property peer when getpeercon(3) is
  unavailable; and
* make the four property SELinux checks succeed; and
* retain the host-verified read-only APEX payloads and writable linkerconfig
  bind by skipping only second-stage init's two tmpfs mounts over those paths.

The stock bootstrap/default mount-namespace setup remains enabled. The
container selects SetupMountNamespaces()'s stock single-namespace branch for
its one immutable APEX set. This still records authentic namespace file
descriptors and IDs, allowing linkerconfig to identify the current namespace,
without creating a second mutable-APEX view that C1 neither has nor needs.

These changes are scoped to this container-only binary. They do not bypass
any Linux namespace, cgroup, device, or filesystem boundary and they do not
interpose on properties or Binder.

All other bytes, including the successful SELinux path, remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


STOCK_SHA256 = "11111bc72131e1e613ce0504aa0166e13207ff0f25b5767a959eb9baddd99e60"
PATCHES = (
    # ueventd setexeccon and vendor-init subcontext setexeccon.
    (0x0DFD00, "76540894", "e0031f2a"),
    (0x10CFCC, "c39f0794", "e0031f2a"),
    # Service::ComputeContextFromExecutable() getcon failure result.
    (0x10DC9C, "970f0090960f009041630091f72e41f9", "9f7e00a99f0a00f99f2200b93c010014"),
    # Property SELinux access checks.
    (0x155AF0, "02840694", "e0031f2a"),
    (0x155D14, "79830694", "e0031f2a"),
    (0x155E94, "19830694", "e0031f2a"),
    (0x156058, "a8820694", "e0031f2a"),
    # getpeercon: return true with the caller's output string untouched.
    (0x160E68, "96570694f403002ac0000035", "f4031f2a070000141f2003d5"),
    # MountExtraFilesystems() would hide the host-mounted, AVB-verified APEX
    # payloads and the container-private linkerconfig bind beneath new empty
    # tmpfs mounts. Preserve those two existing mounts and synthesize success.
    # The optional /bootstrap-apex mount remains stock.
    (0x137D1C, "99f70694", "e0031f2a"),
    (0x137DA0, "78f70694", "e0031f2a"),
    # SetupMountNamespaces(): select the function's stock single-namespace
    # branch after recording the bootstrap namespace. Android normally chooses
    # this branch for recovery/microdroid; C1 has the same one-set APEX model.
    (0x15495C, "40060034", "32000014"),
)


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    payload = bytearray(args.source.read_bytes())
    actual = sha256(payload)
    if actual != STOCK_SHA256:
        raise RuntimeError(f"stock init hash mismatch: {actual}")
    changed = 0
    for offset, original_hex, replacement_hex in PATCHES:
        original = bytes.fromhex(original_hex)
        replacement = bytes.fromhex(replacement_hex)
        if len(original) != len(replacement):
            raise RuntimeError(f"patch length differs at 0x{offset:x}")
        observed = bytes(payload[offset : offset + len(original)])
        if observed != original:
            raise RuntimeError(
                f"stock patch window mismatch at 0x{offset:x}: {observed.hex()}"
            )
        payload[offset : offset + len(replacement)] = replacement
        changed += len(replacement)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    args.output.chmod(0o755)
    print(f"source_sha256={actual}")
    print(f"output_sha256={sha256(payload)}")
    print("patch_offsets=" + ",".join(f"0x{offset:x}" for offset, _, _ in PATCHES))
    print(f"changed_bytes={changed}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
