# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import call, patch

from luma_android.engine import (
    HOST_GESTURE_STRIP_LOGICAL_PX,
    CommandResult,
    WaydroidEngine,
    host_status_bar_px,
)


class StatusBarInsetHeight(unittest.TestCase):
    def test_touch_presentations_use_the_scaled_top_bar(self):
        self.assertEqual(host_status_bar_px("handheld", "3.000000"), 102)
        self.assertEqual(host_status_bar_px("tablet", "2"), 68)

    def test_gesture_strip_scales_like_the_top_bar(self):
        self.assertEqual(
            host_status_bar_px("handheld", "3.000000", HOST_GESTURE_STRIP_LOGICAL_PX), 84)

    def test_desktop_keeps_no_inset(self):
        self.assertEqual(host_status_bar_px("desktop", "3.000000"), 0)
        self.assertEqual(
            host_status_bar_px("desktop", "3.000000", HOST_GESTURE_STRIP_LOGICAL_PX), 0)

    def test_unknown_scale_falls_back_to_logical_height(self):
        for scale in ("", "nan", "0", "-2"):
            self.assertEqual(host_status_bar_px("handheld", scale), 34)


class StatusBarInsetProperty(unittest.TestCase):
    def run_sync(self, device_class, scale, status, nav):
        engine = WaydroidEngine("waydroid")
        replies = {
            "waydroid.display_scale": scale,
            "persist.luma.status_bar_px": status,
            "persist.luma.nav_bar_px": nav,
        }

        def fake_run(*arguments, **_):
            if arguments[:2] == ("prop", "get"):
                return CommandResult(replies[arguments[2]] + "\n", "", 0)
            return CommandResult("", "", 0)

        with patch.object(engine, "_run", side_effect=fake_run) as run:
            engine.sync_host_bar_insets(device_class)
        return [c for c in run.call_args_list if c.args[:2] == ("prop", "set")]

    def test_handheld_publishes_physical_bar_heights(self):
        self.assertEqual(self.run_sync("handheld", "3.000000", "", ""), [
            call("prop", "set", "persist.luma.status_bar_px", "102", timeout=30),
            call("prop", "set", "persist.luma.nav_bar_px", "84", timeout=30),
        ])

    def test_desktop_publishes_zero(self):
        self.assertEqual(self.run_sync("desktop", "1.000000", "102", "84"), [
            call("prop", "set", "persist.luma.status_bar_px", "0", timeout=30),
            call("prop", "set", "persist.luma.nav_bar_px", "0", timeout=30),
        ])

    def test_unchanged_heights_are_not_rewritten(self):
        self.assertEqual(self.run_sync("handheld", "3.000000", "102", "84"), [])

    def test_only_the_changed_height_is_written(self):
        self.assertEqual(self.run_sync("tablet", "2", "68", ""), [
            call("prop", "set", "persist.luma.nav_bar_px", "56", timeout=30),
        ])


if __name__ == "__main__":
    unittest.main()
