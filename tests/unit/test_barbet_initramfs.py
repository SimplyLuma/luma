# SPDX-License-Identifier: Apache-2.0
"""Barbet boot initramfs overlay: hashes, scripts and safety boundaries."""
import hashlib
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREE = ROOT / "config/mobile/barbet-initramfs"
MANIFEST = ROOT / "config/mobile/barbet-initramfs.manifest"


def rows():
    out = []
    for line in MANIFEST.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        kind, mode, value, path, origin = line.split("\t")
        out.append((kind, mode, value, path, origin))
    return out


class BarbetInitramfs(unittest.TestCase):
    def test_manifest_covers_every_file(self) -> None:
        listed = {r[3]: r for r in rows()}
        actual = {p.relative_to(TREE).as_posix() for p in TREE.rglob("*") if p.is_file()}
        self.assertEqual(set(listed), actual)
        for rel, (kind, mode, value, _, origin) in listed.items():
            self.assertEqual(kind, "F", rel)
            self.assertEqual(origin, "branch", rel)
            path = TREE / rel
            self.assertEqual(mode, oct(path.stat().st_mode & 0o777)[2:].zfill(4), rel)
            self.assertEqual(value, hashlib.sha256(path.read_bytes()).hexdigest(), rel)

    def test_no_firmware_payload(self) -> None:
        self.assertEqual(
            [p for p in TREE.rglob("*") if p.suffix in {".bin", ".mbn", ".mdt", ".elf", ".fw"}],
            [],
        )

    def test_scripts_parse(self) -> None:
        for path in TREE.rglob("*"):
            if path.is_file() and (path.suffix == ".sh" or path.name == "50-barbet-usb"):
                subprocess.run(["sh", "-n", str(path)], check=True)

    def test_hook_does_not_write_identity_storage(self) -> None:
        hook = (TREE / "hooks-cleanup/60-barbet-platform.sh").read_text()
        self.assertNotRegex(hook, re.compile(r"modemst|fsg|fsc|persist|vbmeta|dtbo", re.I))

    def test_hook_masks_the_fp6_platform_units(self) -> None:
        hook = (TREE / "hooks-cleanup/60-barbet-platform.sh").read_text()
        self.assertIn("luma-fp6-*.service", hook)
        self.assertIn('ln -sf /dev/null "/sysroot/etc/systemd/system/${unit##*/}"', hook)

    def test_wifi_starts_the_modem_after_ath10k_binds(self) -> None:
        script = (TREE / "luma-barbet/etc/luma/barbet-wifi-start.sh").read_text()
        bind = script.index("/sys/bus/platform/devices/18800000.wifi/driver")
        start = script.index("/sys/class/remoteproc/remoteproc1/state")
        self.assertLess(bind, start, "the modem must start after ath10k binds")
        self.assertIn("tqftpserv", script)
        self.assertIn("wlan0", script)

    def test_audio_uses_the_firmware_store(self) -> None:
        service = (TREE / "luma-barbet/etc/systemd/system/luma-barbet-audio.service").read_text()
        self.assertIn("/var/lib/luma/device-firmware-barbet/rt5514p_dsp_fw1.bin", service)
        script = (TREE / "luma-barbet/etc/luma/barbet-audio-start.sh").read_text()
        self.assertIn("device-firmware-barbet", script)
        self.assertNotIn("modemst", script)


if __name__ == "__main__":
    unittest.main()
