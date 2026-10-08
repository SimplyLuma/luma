#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))

from prairie_ui.context import InputMode, PrairieContext, PresentationMode  # noqa: E402
from prairie_ui import context as context_module  # noqa: E402


class ContextTests(unittest.TestCase):
    def test_desktop_defaults_are_windowed_and_pointer_driven(self) -> None:
        context = PrairieContext.from_environment({})
        self.assertEqual(context.presentation, PresentationMode.WINDOWED)
        self.assertEqual(context.input_mode, InputMode.POINTER)

    def test_the_two_kits_still_name_the_same_presentation(self) -> None:
        """The Python kit's member is FULLSCREEN; the C kit's is FULLSCREEN_MOBILE.

        They meet over `LUMA_PRESENTATION_MODE`, so the member names may
        differ but the value may not: `luma-context.c` gives its enum the
        nick "fullscreen-mobile", and this is the Python side of that wire.
        """
        self.assertEqual(PresentationMode.FULLSCREEN.value, "fullscreen-mobile")
        self.assertEqual(PresentationMode.WINDOWED.value, "windowed")

    def test_handheld_defaults_are_explicit_capabilities(self) -> None:
        context = PrairieContext.from_environment({"LUMA_DEVICE_CLASS": "handheld"})
        self.assertEqual(context.presentation, PresentationMode.FULLSCREEN)
        self.assertEqual(context.input_mode, InputMode.TOUCH)

    def test_capabilities_override_device_default_independently(self) -> None:
        context = PrairieContext.from_environment(
            {
                "LUMA_DEVICE_CLASS": "handheld",
                "LUMA_PRESENTATION_MODE": "windowed",
                "LUMA_INPUT_MODE": "pointer",
            }
        )
        self.assertEqual(context.presentation, PresentationMode.WINDOWED)
        self.assertEqual(context.input_mode, InputMode.POINTER)

    def test_immutable_device_marker_is_session_environment_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "luma-device-class"
            marker.write_text("handheld\n", encoding="utf-8")
            with mock.patch.object(context_module, "DEVICE_CLASS_MARKER", marker):
                context = PrairieContext.from_environment({})
        self.assertEqual(context.presentation, PresentationMode.FULLSCREEN)
        self.assertEqual(context.input_mode, InputMode.TOUCH)

    def test_explicit_device_class_wins_over_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "luma-device-class"
            marker.write_text("handheld\n", encoding="utf-8")
            with mock.patch.object(context_module, "DEVICE_CLASS_MARKER", marker):
                context = PrairieContext.from_environment({"LUMA_DEVICE_CLASS": "desktop"})
        self.assertEqual(context.presentation, PresentationMode.WINDOWED)
        self.assertEqual(context.input_mode, InputMode.POINTER)

    def test_width_is_not_a_context_input(self) -> None:
        compact = PrairieContext.from_environment({"LUMA_WINDOW_WIDTH": "360"})
        wide = PrairieContext.from_environment({"LUMA_WINDOW_WIDTH": "1440"})
        self.assertEqual(compact, wide)

    def test_invalid_explicit_capability_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            PrairieContext.from_environment({"LUMA_PRESENTATION_MODE": "phone-ish"})


if __name__ == "__main__":
    unittest.main()
