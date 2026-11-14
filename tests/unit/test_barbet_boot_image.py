# SPDX-License-Identifier: Apache-2.0
"""Barbet boot-image builder: policy, failure paths and no side effects."""
import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "scripts/mobile/build-barbet-boot-image.sh"


def tool(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class BarbetBootImage(unittest.TestCase):
    def test_header_and_avb_policy(self) -> None:
        text = BUILD.read_text()
        for needle in (
            "--header_version 2", "--pagesize 4096",
            "--kernel_offset 0x00008000", "--ramdisk_offset 0x10000000",
            "--dtb_offset 0x18000000", "partition_size=100663296",
            "rollback_index=1783209600", "--algorithm NONE",
            "firmware_store=/var/lib/luma/device-firmware",
        ):
            self.assertIn(needle, text)

    def test_shared_resolver_required(self) -> None:
        text = BUILD.read_text()
        self.assertIn('"$shared/luma-ostree-boot.sh"', text)
        self.assertIn("50-luma-ostree-default.sh", text)
        self.assertIn("barbet-initramfs", text)

    def test_wrong_input_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            kernel = tmp_path / "kernel"
            kernel.write_text("this is not an arm64 Image\n")
            dtb = tmp_path / "board.dtb"
            dtb.write_text("dtb")
            alpha = tmp_path / "alpha.bin"
            alpha.write_text("alpha")
            out = tmp_path / "boot.img"
            result = subprocess.run(
                [str(BUILD), "--kernel", str(kernel), "--dtb", str(dtb),
                 "--alpha-boot", str(alpha), "--output", str(out)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())

    def test_oversize_preserves_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            kernel = tmp_path / "kernel"
            data = bytearray(64)
            struct.pack_into("<QQ", data, 8, 0, 64)
            kernel.write_bytes(data)
            dtb = tmp_path / "board.dtb"
            dtb.write_text("dtb")
            alpha = tmp_path / "alpha.bin"
            alpha.write_text("alpha")
            out = tmp_path / "boot.img"
            out.write_text("previous set")
            tools = tmp_path / "tools"
            tools.mkdir()
            tool(tools / "lz4", "printf '\\004\\042\\115\\030'\nfor last; do :; done\ncat \"$last\"\n")
            tool(tools / "unpack", "out=''\nwhile [ $# -gt 0 ]; do [ \"$1\" = --out ] && out=$2; shift; done\nmkdir -p \"$out/alpha\"\nprintf x > \"$out/alpha/ramdisk\"\n")
            tool(tools / "mkbootimg", "out=''\nwhile [ $# -gt 0 ]; do [ \"$1\" = --output ] && out=$2; shift; done\ntruncate -s 100663297 \"$out\"\n")
            tool(tools / "avbtool", "exit 0\n")
            env = {
                **os.environ,
                "TMPDIR": str(tmp_path),
                "LZ4": str(tools / "lz4"),
                "UNPACK_BOOTIMG": str(tools / "unpack"),
                "MKBOOTIMG": str(tools / "mkbootimg"),
                "AVBTOOL": str(tools / "avbtool"),
            }
            result = subprocess.run(
                [str(BUILD), "--kernel", str(kernel), "--dtb", str(dtb),
                 "--alpha-boot", str(alpha), "--output", str(out)],
                env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(out.read_text(), "previous set")

    def test_no_flash_side_effects(self) -> None:
        text = BUILD.read_text()
        self.assertNotRegex(text, r"\bfastboot\b")
        self.assertNotRegex(text, r"\bdd\b.*of=/dev/")


if __name__ == "__main__":
    unittest.main()
