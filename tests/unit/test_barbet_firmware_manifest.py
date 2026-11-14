# SPDX-License-Identifier: Apache-2.0
"""Barbet firmware manifest generator: names/hashes only, no unsafe paths."""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts/mobile/barbet/firmware-manifest.py"


class BarbetFirmwareManifest(unittest.TestCase):
    def test_counts_and_hashes_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "firmware"
            (store / "qcom" / "sm7250").mkdir(parents=True)
            (store / "qcom" / "sm7250" / "adsp.mdt").write_bytes(b"adsp")
            (store / "qcom" / "a650_sqe.fw").write_bytes(b"gpu")
            (store / "rt5514p_dsp_fw1.bin").write_bytes(b"mic")
            output = Path(tmp) / "firmware.manifest"
            subprocess.run([sys.executable, str(TOOL), str(store),
                            "--output", str(output)], check=True)
            lines = output.read_text().splitlines()
            self.assertEqual(lines[0].split("\t")[0:2], ["H", "3"])
            rows = [line.split("\t") for line in lines[1:]]
            self.assertEqual([r[3] for r in rows], [
                "qcom/a650_sqe.fw",
                "qcom/sm7250/adsp.mdt",
                "rt5514p_dsp_fw1.bin",
            ])
            for row in rows:
                self.assertEqual(row[0], "F")
                self.assertEqual(row[1], "644")
                relative = row[3]
                self.assertEqual(row[2], hashlib.sha256(
                    (store / relative).read_bytes()).hexdigest())

    def test_refuses_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "firmware"
            store.mkdir()
            (store / "real.bin").write_bytes(b"x")
            (store / "link.bin").symlink_to(store / "real.bin")
            output = Path(tmp) / "firmware.manifest"
            result = subprocess.run(
                [sys.executable, str(TOOL), str(store), "--output", str(output)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
