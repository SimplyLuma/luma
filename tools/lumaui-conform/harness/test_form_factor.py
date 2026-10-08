#!/usr/bin/env python3
"""Focused conform launch capability regression (python3 -m unittest discover)."""
import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("remote_capture", HERE / "remote_capture.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class FormFactorCaptureTest(unittest.TestCase):
    def test_phone_variant_overrides_inherited_and_state_desktop(self):
        env = {"LUMA_FORM_FACTOR": "desktop"}
        capture.set_form_factor(env, {"phone": True, "size": "390x740"})
        self.assertEqual(env["LUMA_FORM_FACTOR"], "phone")

    def test_narrow_desktop_keeps_desktop_capability(self):
        env = {"LUMA_FORM_FACTOR": "phone"}
        capture.set_form_factor(env, {"phone": False, "size": "390x740"})
        self.assertEqual(env["LUMA_FORM_FACTOR"], "desktop")
        capture.set_form_factor(env, {"size": "390x740"})  # old plans default to desktop
        self.assertEqual(env["LUMA_FORM_FACTOR"], "desktop")

    def test_plan_carries_spec_phone_boolean_to_launch(self):
        # This is the transport boundary between gtk_capture.sh and remote_capture.py.
        plan_writer = (HERE.parent / "gtk_capture.sh").read_text()
        self.assertIn("phone: m.phone === true", plan_writer)
        self.assertIn("set_form_factor(env, plan)", (HERE / "remote_capture.py").read_text())

    def test_malformed_plan_rejected(self):
        with self.assertRaisesRegex(ValueError, "phone must be a boolean"):
            capture.set_form_factor({}, {"phone": "false"})


if __name__ == "__main__":
    unittest.main()
