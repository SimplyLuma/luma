#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Brand shim's fallback entry metadata during image composition.

Only the Fedora shim CSV label and comment change. Signed executables, vendor
directories, loader arguments, and firmware variables are never modified.
See rhboot/shim README.fallback for the UCS-2LE CSV interface.
"""
from pathlib import Path
import argparse
import codecs
import csv
import io


def branded(data: bytes, filename: str) -> bytes:
    if not data.startswith(codecs.BOM_UTF16_LE):
        raise ValueError(f'{filename}: expected shim UCS-2LE metadata with BOM')
    text = data[2:].decode('utf-16-le')
    if '\x00' in text or any(ord(c) > 0xffff for c in text):
        raise ValueError(f'{filename}: invalid UCS-2 metadata')
    rows = list(csv.reader(io.StringIO(text)))
    expected = {'BOOTX64.CSV': 'shimx64.efi', 'BOOTIA32.CSV': 'shimia32.efi',
                'BOOTAA64.CSV': 'shimaa64.efi'}
    if filename not in expected or len(rows) != 1 or len(rows[0]) < 4:
        raise ValueError(f'{filename}: unexpected shim fallback entry')
    row = rows[0]
    if row[0] != expected[filename] or row[1] not in ('Fedora', 'Luma'):
        raise ValueError(f'{filename}: not a Fedora/Luma shim entry')
    if row[1] == 'Luma' and row[3] == 'This is the boot entry for Luma':
        return data
    row[1], row[3] = 'Luma', 'This is the boot entry for Luma'
    output = io.StringIO()
    csv.writer(output, lineterminator='\r\n' if '\r\n' in text else '\n').writerow(row)
    return codecs.BOM_UTF16_LE + output.getvalue().encode('utf-16-le')


def prepare(root: Path) -> list[Path]:
    # Fedora 44 bootupd consumes versioned /usr/lib/efi; older image inputs
    # carry /usr/lib/bootupd/updates/EFI. Do not traverse any other vendor.
    root = root.absolute()
    candidates = set((root / 'usr/lib/efi').glob('shim/*/EFI/fedora/BOOT*.CSV'))
    legacy = root / 'usr/lib/bootupd/updates/EFI/fedora'
    candidates.update(legacy.glob('BOOT*.CSV'))
    if not candidates:
        raise ValueError('no Fedora shim fallback metadata in the composed image')
    updates = []
    # Validate every entry before writing any of them.
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if root.is_symlink() or any((root / Path(*relative.parts[:index])).is_symlink()
                                   for index in range(1, len(relative.parts) + 1)):
            raise ValueError(f'{path}: symlink in fallback metadata path')
        if not path.is_file():
            raise ValueError(f'{path}: expected a regular fallback metadata file')
        updates.append((path, branded(path.read_bytes(), path.name)))
    for path, data in updates:
        path.write_bytes(data)
    return [path for path, _ in updates]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='uninstalled image root, never a mounted ESP')
    args = parser.parse_args()
    try:
        for path in prepare(args.root):
            print(f'Luma shim fallback entry: {path}')
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f'error: {error}\n')
