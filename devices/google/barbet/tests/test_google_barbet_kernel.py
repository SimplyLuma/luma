# SPDX-License-Identifier: Apache-2.0
"""Barbet kernel: a Luma tag plus the family config fragment."""
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEVICE = ROOT / "devices/google/barbet/device.toml"
SOC = ROOT / "socs/qcom/sm7250/soc.toml"
FRAGMENT = ROOT / "config/kernel/sm7250.fragment"


class BarbetKernel(unittest.TestCase):
    def test_kernel_points_at_a_luma_tag(self) -> None:
        kernel = tomllib.loads(DEVICE.read_text())["kernel"]
        self.assertTrue(kernel["tag"].startswith("luma-kernel-"))
        self.assertEqual(kernel["release"], "7.2.15-sm7250")
        self.assertEqual(kernel["dtb"], "qcom/sm7250-google-barbet.dtb")
        self.assertNotIn("source_lock", kernel)
        self.assertNotIn("patches", kernel)

    def test_family_branch_is_the_kernel_repository(self) -> None:
        soc = tomllib.loads(SOC.read_text())
        self.assertEqual(soc["kernel"]["repository"],
                         "https://github.com/SimplyLuma/linux")
        self.assertEqual(soc["kernel"]["branch"], "sm7250")

    def test_fragment_names_the_board_drivers(self) -> None:
        fragment = FRAGMENT.read_text()
        for symbol in (
            "CONFIG_PINCTRL_SM7250=y",
            "CONFIG_SM_GCC_7250=y",
            "CONFIG_SND_SOC_RT5514=y",
            "CONFIG_SND_SOC_TAS2562=y",
            "CONFIG_TOUCHSCREEN_STFTS_SPI=y",
            "CONFIG_BATTERY_BARBET=y",
            "CONFIG_CHARGER_QCOM_PM7250B=y",
            "CONFIG_INPUT_DRV2624=y",
            "CONFIG_ATH10K_SNOC=y",
        ):
            self.assertIn(symbol, fragment)

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
