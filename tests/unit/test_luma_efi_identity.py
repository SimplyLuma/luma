"""Exercise shim UTF-16 CSV records consumed as UEFI load-option labels."""
import codecs
import csv
import importlib.util
import io
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('efi_identity', ROOT / 'scripts/boot/prepare-efi-identity.py')
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


def fallback_load_option(data):
    """Encode the CSV description in the native EFI_LOAD_OPTION byte layout.

    The description is CHAR16 NUL terminated, following attributes and the
    file-path-list length. This fixture does not write actual firmware.
    """
    row = next(csv.reader(io.StringIO(data.decode('utf-16'))))
    device_path = b'\x7f\xff\x04\x00'  # End device-path node.
    return struct.pack('<IH', 1, len(device_path)) + row[1].encode('utf-16-le') + b'\0\0' + device_path


def description(option):
    offset = 6
    while option[offset:offset + 2] != b'\0\0':
        offset += 2
    return option[6:offset].decode('utf-16-le')


class EfiIdentity(unittest.TestCase):
    def test_real_utf16_fedora_recovery_label_becomes_luma(self):
        old = codecs.BOM_UTF16_LE + 'shimx64.efi,Fedora,,This is the boot entry for Fedora\n'.encode('utf-16-le')
        self.assertEqual(description(fallback_load_option(old)), 'Fedora')
        with self.assertRaises(AssertionError):
            self.assertEqual(description(fallback_load_option(old)), 'Luma')
        updated = identity.branded(old, 'BOOTX64.CSV')
        self.assertEqual(description(fallback_load_option(updated)), 'Luma')
        self.assertEqual(updated.decode('utf-16'), 'shimx64.efi,Luma,,This is the boot entry for Luma\n')
        self.assertEqual(identity.branded(updated, 'BOOTX64.CSV'), updated)

    def test_only_owned_csv_changes_binaries_and_other_vendors_preserved(self):
        for layout in ('usr/lib/efi/shim/16.1-7/EFI', 'usr/lib/bootupd/updates/EFI'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                vendor = root / layout / 'fedora'
                vendor.mkdir(parents=True)
                shim = vendor / 'shimx64.efi'
                shim.write_bytes(b'signed PE fixture\0\x01\xff')
                other = root / layout / 'Microsoft/BOOTX64.CSV'
                other.parent.mkdir()
                other.write_bytes(b'foreign vendor metadata')
                records = {}
                for name, loader in (('BOOTX64.CSV', 'shimx64.efi'), ('BOOTIA32.CSV', 'shimia32.efi')):
                    records[name] = codecs.BOM_UTF16_LE + f'{loader},Fedora,keep arguments,This is the boot entry for Fedora,extra\r\n'.encode('utf-16-le')
                    (vendor / name).write_bytes(records[name])
                self.assertEqual(len(identity.prepare(root)), 2)
                self.assertEqual(shim.read_bytes(), b'signed PE fixture\0\x01\xff')
                self.assertEqual(other.read_bytes(), b'foreign vendor metadata')
                for name, old in records.items():
                    row = next(csv.reader(io.StringIO((vendor / name).read_bytes().decode('utf-16'))))
                    original = next(csv.reader(io.StringIO(old.decode('utf-16'))))
                    self.assertEqual(row[0], original[0])
                    self.assertEqual(row[2], original[2])
                    self.assertEqual(row[4:], original[4:])
                    self.assertEqual(row[1], 'Luma')

    def test_reject_invalid_or_foreign_metadata(self):
        for data in (b'ASCII,not UCS2', codecs.BOM_UTF16_LE + 'evil.efi,Fedora,,x\n'.encode('utf-16-le'),
                     codecs.BOM_UTF16_LE + 'shimx64.efi,Foreign,,x\n'.encode('utf-16-le')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                identity.branded(data, 'BOOTX64.CSV')
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            identity.prepare(Path(directory))

    def test_parent_symlink_cannot_modify_an_external_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            root.mkdir()
            external = Path(directory) / 'external'
            vendor = external / 'lib/efi/shim/16.1-7/EFI/fedora'
            vendor.mkdir(parents=True)
            data = codecs.BOM_UTF16_LE + 'shimx64.efi,Fedora,,unchanged\n'.encode('utf-16-le')
            record = vendor / 'BOOTX64.CSV'
            record.write_bytes(data)
            (root / 'usr').symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                identity.prepare(root)
            self.assertEqual(record.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
