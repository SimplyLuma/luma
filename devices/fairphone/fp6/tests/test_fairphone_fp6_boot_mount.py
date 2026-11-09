# SPDX-License-Identifier: Apache-2.0
"""Fairphone 6 boot image: /boot stays on the root filesystem, where ostree
writes the boot entries the OSTree boot resolver reads; the boot partition
(LumaBoot, the device kit) is left for luma-device-layer to mount."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts/mobile/fp6-native-init-functions.sh"
SHELL = os.environ.get("LUMA_TEST_SH", "bash").split()
MARKER = "/etc/luma-fp6-ostree"
STUBS = """
find_boot_partition() { eval "$1=/dev/loop0p1"; }
get_partition_type() { echo vfat; }
check_filesystem() { echo "fsck $1" >>"$LOG"; }
modprobe() { :; }
mount() { echo "mount $*" >>"$LOG"; }
"""


class BootPartitionMount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.marker = self.dir / "luma-fp6-ostree"
        self.log = self.dir / "log"
        text = SCRIPT.read_text()
        self.assertEqual(text.count(MARKER), 2)
        (self.dir / "init_functions.sh").write_text(text.replace(MARKER, str(self.marker)))

    def tearDown(self):
        self.tmp.cleanup()

    def mount(self, *args):
        script = f". {self.dir}/init_functions.sh\n{STUBS}\nmount_boot_partition {' '.join(args)}\n"
        result = subprocess.run([*SHELL, "-c", script], capture_output=True, text=True,
                                env={**os.environ, "LOG": str(self.log)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_luma_ostree_root_keeps_boot_on_the_root_filesystem(self):
        self.marker.touch()
        self.assertEqual(self.mount("/sysroot/boot", "rw"), [])

    def test_initramfs_boot_mount_is_unchanged(self):
        self.marker.touch()
        self.assertEqual(self.mount("/boot"), [
            "mount -t vfat -o nodev,nosuid,noexec,ro,umask=0077,nosymfollow,codepage=437,iocharset=ascii "
            "/dev/loop0p1 /boot"])

    def test_other_roots_still_get_the_boot_partition(self):
        self.assertEqual(self.mount("/sysroot/boot", "rw"), [
            "fsck /dev/loop0p1",
            "mount -t vfat -o nodev,nosuid,noexec,umask=0077,nosymfollow,codepage=437,iocharset=ascii "
            "/dev/loop0p1 /sysroot/boot"])


if __name__ == "__main__":
    unittest.main()
