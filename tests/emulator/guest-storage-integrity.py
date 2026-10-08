#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded VM storage regression probe. Create, cold boot, then verify.

Usage: guest-storage-integrity.py create|verify /var/tmp/UNIQUE_TEST_DIRECTORY
Writes 256 MiB of incompressible test data with a 1 MiB working buffer. Never
overwrites or removes an existing test directory. Remove test data explicitly
after retaining the result. This is test tooling, not an image startup task.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("operation", choices=("create", "verify"))
parser.add_argument("directory", type=Path)
args = parser.parse_args()
root = args.directory
chunk_size = 1024 * 1024

if args.operation == "create":
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    hashes = []
    with (root / "payload.bin").open("xb") as output:
        for _ in range(256):
            chunk = os.urandom(chunk_size)
            hashes.append(hashlib.sha256(chunk).hexdigest())
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    with (root / "manifest.json").open("x") as manifest:
        json.dump(hashes, manifest)
        manifest.flush()
        os.fsync(manifest.fileno())
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    print("Created and flushed 256 MiB; verify again after a cold boot.")

hashes = json.loads((root / "manifest.json").read_text())
if len(hashes) != 256:
    raise SystemExit("FAIL: incomplete test manifest")
with (root / "payload.bin").open("rb") as source:
    for index, expected in enumerate(hashes):
        chunk = source.read(chunk_size)
        if len(chunk) != chunk_size or hashlib.sha256(chunk).hexdigest() != expected:
            raise SystemExit(f"FAIL: storage block {index} changed")
    if source.read(1):
        raise SystemExit("FAIL: unexpected trailing data")
print("PASS: all 256 MiB match the recorded checksums.")
