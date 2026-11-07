"""Phone chrome follows the same device marker that mobile composition writes."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from luma_appkit.lumaui import mobile_form_factor


class MobileIdentity(unittest.TestCase):
    def test_device_identity_and_override(self):
        cases = [
            ({"LUMA_DEVICE_CLASS": "handheld"}, True),
            ({"LUMA_DEVICE_CLASS": " HandHeld "}, True),
            ({"LUMA_DEVICE_CLASS": "desktop"}, False),
            ({"LUMA_DEVICE_CLASS": "tablet"}, False),
            ({"LUMA_DEVICE_CLASS": "handheld", "LUMA_FORM_FACTOR": "desktop"}, False),
            ({"LUMA_DEVICE_CLASS": "desktop", "LUMA_FORM_FACTOR": "phone"}, True),
            ({"LUMA_DEVICE_CLASS": "desktop", "LUMA_FORM_FACTOR": " Handset "}, True),
        ]
        for env, expected in cases:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.assertEqual(mobile_form_factor(), expected)

    def test_image_marker_and_legacy_chassis(self):
        for marker, chassis, expected in [
            ("handheld\n", "", True),
            ("desktop\n", "CHASSIS=handset\n", False),
            ("tablet\n", "CHASSIS=handset\n", False),
            (None, 'CHASSIS="handset"\n', True),
            ("unknown", "CHASSIS=desktop\n", False),
            (None, None, False),
        ]:
            def read(path, **kwargs):
                value = marker if str(path) == "/etc/luma-device-class" else chassis
                if value is None:
                    raise FileNotFoundError(path)
                return value
            with self.subTest(marker=marker, chassis=chassis), \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(Path, "read_text", read):
                self.assertEqual(mobile_form_factor(), expected)

    def test_tv_posture_is_never_a_phone(self):
        for env in ({"LUMA_POSTURE": "tv", "LUMA_DEVICE_CLASS": "handheld"},
                    {"LUMA_POSTURE": "tv", "LUMA_FORM_FACTOR": "phone"}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                self.assertFalse(mobile_form_factor())
        with patch.dict(os.environ, {"LUMA_POSTURE": "desktop", "LUMA_DEVICE_CLASS": "handheld"}, clear=True):
            self.assertTrue(mobile_form_factor())


if __name__ == "__main__":
    unittest.main()
