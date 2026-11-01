#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""LumaUI's TV posture contract: one posture, independent of presentation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/appkit"))

from luma_appkit import context as context_module  # noqa: E402
from luma_appkit.context import (  # noqa: E402
    AppContext, InputMode, PresentationMode, context_from_session, system_posture,
)


class SystemPosture(unittest.TestCase):
    def test_override_and_explicit_environment(self):
        self.assertEqual(system_posture({"LUMA_POSTURE": "tv"}), "tv")
        self.assertEqual(system_posture({"LUMA_POSTURE": " TV "}), "tv")
        self.assertEqual(system_posture({"LUMA_POSTURE": "desktop"}), "")
        # An explicit environment never reads the system.
        self.assertEqual(system_posture({}), "")

    def test_device_class_tv_is_tv_when_automatic(self):
        def read(path, **_kwargs):
            if str(path) == "/etc/luma-device-class":
                return "tv\n"
            raise FileNotFoundError(path)
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(Path, "read_text", read):
            self.assertEqual(system_posture(), "tv")


class ContextPosture(unittest.TestCase):
    def test_environment_context_carries_posture_without_changing_presentation(self):
        for device in ("desktop", "tablet", "handheld"):
            with self.subTest(device=device):
                plain = AppContext.from_environment({"LUMA_DEVICE_CLASS": device})
                tv = AppContext.from_environment({"LUMA_DEVICE_CLASS": device, "LUMA_POSTURE": "tv"})
                self.assertEqual(plain.posture, "")
                self.assertFalse(plain.tv)
                self.assertTrue(tv.tv)
                self.assertEqual((tv.presentation, tv.input_mode, tv.device_class),
                                 (plain.presentation, plain.input_mode, plain.device_class))

    def test_positional_construction_is_unchanged(self):
        context = AppContext(PresentationMode.WINDOWED, InputMode.POINTER, "desktop")
        self.assertEqual(context.posture, "")

    def test_session_posture_projection(self):
        base = AppContext(PresentationMode.WINDOWED, InputMode.POINTER, "desktop")
        self.assertEqual(context_from_session(base, "desktop", {}), base)
        self.assertEqual(context_from_session(base, "desktop", {}, "desktop"), base)
        tv = context_from_session(base, "desktop", {}, "tv")
        self.assertTrue(tv.tv)
        self.assertEqual(tv.presentation, PresentationMode.WINDOWED)
        # A service without the property keeps what the app already knew.
        self.assertTrue(context_from_session(tv, "desktop", {}, None).tv)
        self.assertFalse(context_from_session(tv, "mobile", {}, "mobile").tv)

    def test_module_names_the_tv_posture(self):
        self.assertEqual(context_module.TV_POSTURE, "tv")


if __name__ == "__main__":
    unittest.main()
