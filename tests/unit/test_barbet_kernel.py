# SPDX-License-Identifier: Apache-2.0
"""Barbet kernel builder: the lock, config and failure behaviour must agree."""
import hashlib
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "scripts/kernel/build-barbet.sh"
LOCK = ROOT / "config/kernel/barbet-7.3.json"
CONFIG = ROOT / "config/kernel/barbet.config"
SOC = ROOT / "socs/qcom/sm7250/soc.toml"


def makefile() -> str:
    return "VERSION=7\nPATCHLEVEL=3\nSUBLEVEL=0\nEXTRAVERSION=-rc7\n"


def fake_make(path: Path, fail_build: bool) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *kernelrelease*) echo 7.3.0-rc7 ;;\n"
        "  *olddefconfig*) exit 0 ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n"
    )
    path.chmod(0o755)


class BarbetKernel(unittest.TestCase):
    def test_lock_pins_sources_and_patch_order(self) -> None:
        lock = json.loads(LOCK.read_text())
        self.assertEqual(lock["schema"], "org.projectluma.kernel-source/v1")
        self.assertEqual(lock["release"], "7.3.0-rc7")
        self.assertEqual(lock["device_tree"], "qcom/sm7250-google-barbet.dtb")
        self.assertEqual(
            lock["sources"]["linux"]["sha256"],
            "f789fc0d7b3713b6be23284f2a9c35bf41fb86785b24cfcc506c20d8ff712523",
        )
        self.assertEqual(
            lock["sources"]["overlay"]["sha256"],
            "5b151b749dc11c746b20268554a2cc5e72ba5f1a5a0f86fa66a6aca8c24ccc0f",
        )
        self.assertEqual(
            [p["path"] for p in lock["patches"]][:2],
            ["patches/01-bindings.patch", "patches/02-clk.patch"],
        )
        self.assertEqual(len(lock["patches"]), 21)
        self.assertTrue(all(p["source"] == "overlay" for p in lock["patches"][:14]))
        self.assertEqual([p["source"] for p in lock["patches"][14:]],
                         ["repository"] * 7)
        self.assertEqual([p["path"] for p in lock["patches"][14:]], [
            "patches/linux-barbet/7.3/0001-arm64-dts-qcom-add-google-barbet.patch",
            "patches/linux-barbet/7.3/0002-Input-add-ST-FTS-SPI-touchscreen.patch",
            "patches/linux-barbet/7.3/0003-remoteproc-add-the-sm7250-adsp-resource.patch",
            "patches/linux-barbet/7.3/0004-net-qrtr-and-barbet-modem.patch",
            "patches/linux-barbet/7.3/0005-ASoC-qcom-barbet-speaker.patch",
            "patches/linux-barbet/7.3/0006-ASoC-qcom-barbet-microphone.patch",
            "patches/linux-barbet/7.3/0007-drm-panel-add-the-barbet-sofef01-panel-mode.patch",
        ])

    def test_config_hash_matches(self) -> None:
        lock = json.loads(LOCK.read_text())
        self.assertEqual(
            hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
            lock["base_config"]["sha256"],
        )

    def test_offsets_match_soc(self) -> None:
        soc = tomllib.loads(SOC.read_text())
        self.assertEqual(
            soc["boot"]["offsets"],
            {
                "base": 0x00000000,
                "kernel": 0x00008000,
                "ramdisk": 0x10000000,
                "second": 0x00000000,
                "tags": 0x00000100,
                "dtb": 0x18000000,
            },
        )

    def test_failed_build_does_not_publish_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "Makefile").write_text(makefile())
            out = Path(tmp) / "out"
            fake = Path(tmp) / "make"
            fake_make(fake, fail_build=True)
            env = {**os.environ, "MAKE": str(fake)}
            result = subprocess.run(
                [str(BUILD), "--source", str(src), str(out)],
                env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((out / "Image").exists())
            self.assertFalse((out / "qcom/sm7250-google-barbet.dtb").exists())

    def test_stale_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "Makefile").write_text(makefile())
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "receipt.json").write_text('{"lock_sha256": "' + "0" * 64 + '"}\n')
            fake = Path(tmp) / "make"
            fake_make(fake, fail_build=False)
            env = {**os.environ, "MAKE": str(fake)}
            result = subprocess.run(
                [str(BUILD), "--source", str(src), str(out)],
                env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stale", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
