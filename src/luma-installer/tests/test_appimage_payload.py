import struct
import tempfile
import unittest
from pathlib import Path

from luma_installer import appimage_payload
from luma_installer.errors import InstallerError


def runtime(size=4096, decoy_at=1000):
    """A 64-bit ELF whose section headers end at `size`, with hsqs inside its code."""
    header = bytearray(size)
    header[:4] = b"\x7fELF"
    header[4], header[5] = 2, 1
    header[8:11] = b"AI\x02"
    struct.pack_into("<Q", header, 0x28, size - 64 * 4)
    struct.pack_into("<HH", header, 0x3A, 64, 4)
    header[decoy_at:decoy_at + 4] = b"hsqs"
    return bytes(header)


def squashfs(bytes_used=4096):
    block = bytearray(bytes_used)
    block[:4] = b"hsqs"
    struct.pack_into("<I", block, 12, 131072)
    struct.pack_into("<HH", block, 20, 1, 17)
    struct.pack_into("<H", block, 28, 4)
    struct.pack_into("<Q", block, 40, bytes_used)
    return bytes(block)


class AppImagePayload(unittest.TestCase):
    def write(self, content):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "app.AppImage"
        path.write_bytes(content)
        return path

    def test_payload_starts_where_the_runtime_ends_not_at_the_first_hsqs(self):
        self.assertEqual(appimage_payload.locate(self.write(runtime() + squashfs())), ("squashfs", 4096))

    def test_padded_runtime_finds_the_first_valid_superblock(self):
        self.assertEqual(appimage_payload.locate(self.write(runtime() + bytes(512) + squashfs())),
                         ("squashfs", 4608))

    def test_dwarfs_payload_is_recognised(self):
        self.assertEqual(appimage_payload.locate(self.write(runtime() + b"DWARFS" + bytes(4096))),
                         ("dwarfs", 4096))

    def test_truncated_download_is_explained(self):
        with self.assertRaisesRegex(InstallerError, "download it again"):
            appimage_payload.locate(self.write(runtime()[:2048]))


if __name__ == "__main__":
    unittest.main()
