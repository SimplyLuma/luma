# SPDX-License-Identifier: Apache-2.0
"""Barbet install set: hashes, commands and fail-closed output."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "scripts/mobile/build-barbet-install.sh"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(userdata: Path, digest: str, boot: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(BUILD), "--userdata", str(userdata), "--userdata-sha256", digest,
         "--boot-image", str(boot), "--output", str(out)],
        capture_output=True, text=True,
    )


class BarbetInstallSet(unittest.TestCase):
    def test_sha256sums_cover_every_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            userdata = tmp_path / "userdata.img"
            userdata.write_bytes(b"luma userdata fixture")
            boot = tmp_path / "boot.img"
            boot.write_bytes(b"\0" * 100663296)
            out = tmp_path / "set"
            result = run(userdata, sha(userdata), boot, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((out / "boot.img").is_file())
            self.assertTrue((out / "userdata.img").is_file())
            check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=out,
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_install_commands_only_touch_userdata_and_boot_b(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            userdata = tmp_path / "userdata.img"
            userdata.write_bytes(b"luma userdata fixture")
            boot = tmp_path / "boot.img"
            boot.write_bytes(b"\0" * 100663296)
            out = tmp_path / "set"
            self.assertEqual(run(userdata, sha(userdata), boot, out).returncode, 0)
            text = (out / "INSTALL.txt").read_text()
            for command in ("fastboot flash userdata userdata.img",
                            "fastboot flash boot_b boot.img",
                            "fastboot --set-active=b", "fastboot reboot"):
                self.assertIn(command, text)
            for forbidden in ("flash boot_a", "flash dtbo", "flash vbmeta",
                              "flash modemst", "flash fsg", "flash fsc",
                              "flash persist", "flash super"):
                self.assertNotIn(forbidden, text)

    def test_wrong_device_or_userdata_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            userdata = tmp_path / "userdata.img"
            userdata.write_bytes(b"luma userdata fixture")
            boot = tmp_path / "boot.img"
            boot.write_bytes(b"\0" * 100663296)
            out = tmp_path / "set"
            result = run(userdata, "0" * 64, boot, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())

    def test_oversize_boot_preserves_existing_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            userdata = tmp_path / "userdata.img"
            userdata.write_bytes(b"luma userdata fixture")
            boot = tmp_path / "boot.img"
            boot.write_bytes(b"\0" * 100663297)
            out = tmp_path / "set"
            out.mkdir()
            (out / "sentinel").write_text("keep")
            result = run(userdata, sha(userdata), boot, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((out / "sentinel").read_text(), "keep")

    def test_no_flash_side_effects(self) -> None:
        code, in_heredoc = [], False
        for line in BUILD.read_text().splitlines():
            if "<<'EOF'" in line:
                in_heredoc = True
                continue
            if in_heredoc:
                in_heredoc = line != "EOF"
                continue
            code.append(line)
        text = "\n".join(code)
        self.assertNotRegex(text, r"\bfastboot\b")
        self.assertNotRegex(text, r"\bdd\b.*of=/dev/")


if __name__ == "__main__":
    unittest.main()
