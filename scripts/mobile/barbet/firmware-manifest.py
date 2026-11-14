#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate devices/google/barbet/firmware.manifest from a firmware store.

The store is the operator's copy of the barbet firmware tree; this tool records
names and SHA-256 only. It refuses symlinks, absolute paths and `..`, so the
manifest cannot point outside the store.
"""
import argparse
import hashlib
from pathlib import Path


def generate(store: Path) -> str:
    rows = []
    for path in sorted(store.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"symlink not allowed: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(store).as_posix()
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise SystemExit(f"unsafe path: {relative}")
        rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    text = f"H\t{len(rows)}\t-\t-\t-\n"
    text += "".join(f"F\t644\t{digest}\t{relative}\t-\n" for relative, digest in rows)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.store.is_dir():
        raise SystemExit(f"not a directory: {args.store}")
    args.output.write_text(generate(args.store))


if __name__ == "__main__":
    main()
