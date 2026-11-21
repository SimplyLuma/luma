# SPDX-License-Identifier: Apache-2.0
"""Barbet kernel: a Luma tag plus the family config fragment."""
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEVICE = ROOT / "devices/google/barbet/device.toml"
SOC = ROOT / "socs/qcom/sm7250/soc.toml"


class BarbetKernel(unittest.TestCase):
    def test_kernel_points_at_a_luma_tag(self) -> None:
        kernel = tomllib.loads(DEVICE.read_text())["kernel"]
        self.assertTrue(kernel.get("tag", "").startswith("luma-kernel-"),
                        "Blocked: an actual published Luma kernel tag is required")
        self.assertEqual(kernel["release"], "7.2.15")
        self.assertEqual(kernel["dtb"], "qcom/sm7250-google-barbet.dtb")
        self.assertNotIn("source_lock", kernel)
        self.assertNotIn("patches", kernel)

    def test_family_branch_is_the_kernel_repository(self) -> None:
        soc = tomllib.loads(SOC.read_text())
        self.assertEqual(soc["kernel"]["repository"],
                         "https://github.com/SimplyLuma/linux")
        self.assertEqual(soc["kernel"]["branch"], "sm7250")

    def test_family_config_lives_in_the_kernel_tree(self) -> None:
        kernel = tomllib.loads(DEVICE.read_text())["kernel"]
        self.assertEqual(kernel["config"], "luma/config/sm7250.fragment")
        self.assertFalse((ROOT / "config/kernel/sm7250.fragment").exists())

    def test_device_directory_has_only_the_five_policy_entries(self) -> None:
        entries = {p.name for p in DEVICE.parent.iterdir()
                   if p.name != "__pycache__"}
        self.assertEqual(entries, {"device.toml", "boot.toml", "firmware.manifest",
                                   "INSTALL.md", "tests"})

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
