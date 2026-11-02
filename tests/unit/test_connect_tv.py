#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Luma Connect in TV posture: the device noun, the phone actions a TV keeps, the TV home opt-in."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/appkit"))
sys.path.insert(0, str(REPO_ROOT / "src/luma-continuity"))

from luma_appkit.context import AppContext, InputMode, PresentationMode  # noqa: E402

try:
    from luma_appkit.lumaui import device_noun  # noqa: E402
    from luma_continuity import application  # noqa: E402
except (ImportError, ValueError) as error:  # no GTK 4 / libadwaita / LumaUI typelib here
    application = None
    MISSING = str(error)


def context(device: str, posture: str = "") -> AppContext:
    return AppContext(PresentationMode.WINDOWED, InputMode.POINTER, device, posture)


@unittest.skipIf(application is None, "needs GTK 4, libadwaita and LumaUI")
class DeviceNoun(unittest.TestCase):
    def test_each_posture(self):
        self.assertEqual(device_noun(context=context("desktop", "tv")), "this TV")
        self.assertEqual(device_noun(context=context("desktop")), "this computer")
        self.assertEqual(device_noun(context=context("tablet")), "this tablet")
        self.assertEqual(device_noun(context=context("handheld"), capital=True), "This phone")

    def test_postures_limit_which_move(self):
        self.assertEqual(device_noun(context=context("handheld"), postures=("tv",)), "this computer")
        self.assertEqual(device_noun(context=context("tablet", "tv"), postures=("tv",)), "this TV")


@unittest.skipIf(application is None, "needs GTK 4, libadwaita and LumaUI")
class ConnectCopy(unittest.TestCase):
    def test_off_a_tv_copy_is_unchanged(self):
        for device in ("desktop", "tablet", "handheld"):
            with mock.patch.object(application, "device_noun",
                                   lambda capital=False, postures=None, d=device: device_noun(
                                       capital=capital, postures=postures, context=context(d))):
                for text in (application.OPTIONAL, "This computer can", "This Computer"):
                    self.assertIs(application.tv_words(text), text)

    def test_on_a_tv_this_computer_reads_this_tv(self):
        with mock.patch.object(application, "device_noun",
                               lambda capital=False, postures=None: device_noun(
                                   capital=capital, postures=postures, context=context("desktop", "tv"))):
            self.assertEqual(application.tv_words(application.OPTIONAL),
                             "Luma Connect is optional. Everything on this TV keeps working without it.")
            self.assertEqual(application.tv_words("This computer can"), "This TV can")
            self.assertEqual(application.tv_words("This Computer"), "This TV")


@unittest.skipIf(application is None, "needs GTK 4, libadwaita and LumaUI")
class TvActions(unittest.TestCase):
    def test_a_tv_keeps_ring_screen_webcam_and_calls(self):
        with mock.patch.object(application, "runs_on_tv", lambda app_id: app_id is None):
            kept = [a for a in ("Ring", "Send Text", "Send File", "Hotspot", "Messages", "Webcam", "Screen", "Calls",
                                "Power Mode") if application.tv_keeps(a)]
        self.assertEqual(kept, ["Ring", "Webcam", "Screen", "Calls"])

    def test_pairing_offers_what_a_tv_can_use(self):
        offered_in = set(application.TV_PHONE_TO_DESKTOP)
        offered_out = set(application.TV_DESKTOP_TO_PHONE)
        self.assertLessEqual(offered_in, {c for c, _ in application.PHONE_TO_DESKTOP_LABELS})
        self.assertLessEqual(offered_out, {c for c, _ in application.DESKTOP_TO_PHONE_LABELS})
        self.assertNotIn("files.write", offered_in | offered_out)
        self.assertNotIn("hotspot.request", offered_out)


class TvHome(unittest.TestCase):
    def test_connect_desktop_entry_opts_in(self):
        data = REPO_ROOT / "src/luma-continuity/data"
        text = (data / "org.projectluma.Connect.desktop").read_text()
        self.assertIn("X-Luma-TV=true\n", text)
        self.assertIn("X-Luma-TV-Tile=org.projectluma.Connect-tv\n", text)
        self.assertIn("X-Luma-TV-Presentation=tv\n", text)
        self.assertTrue((data / "icons/hicolor/scalable/apps/org.projectluma.Connect-tv.svg").is_file())
        spec = (REPO_ROOT / "packaging/rpm/luma-continuity.spec").read_text()
        self.assertEqual(spec.count("org.projectluma.Connect-tv.svg"), 3)


if __name__ == "__main__":
    unittest.main()
