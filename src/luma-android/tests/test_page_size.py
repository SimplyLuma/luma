# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import patch

from luma_android import engine, initialize, kernel, waydroid_hardware
from luma_android.errors import RuntimeUnavailableError


class AndroidPageSize(unittest.TestCase):
    def test_running_kernel_size_and_unknown_values(self):
        for value, expected in ((4096, 4096), (16384, 16384), (65536, 65536),
                                (0, 0), (-1, 0), (True, 0), ("4096", 0)):
            with self.subTest(value=value), patch.object(kernel.os, "sysconf", return_value=value):
                self.assertEqual(kernel.runtime_page_size(), expected)
        for failure in (OSError("unavailable"), ValueError("unknown sysconf key")):
            with patch.object(kernel.os, "sysconf", side_effect=failure):
                self.assertEqual(kernel.runtime_page_size(), 0)

    def test_current_image_profile_requires_four_kib(self):
        with patch.object(kernel.os, "sysconf", return_value=4096):
            kernel.require_android_page_size()
        for value in (0, 16384, 65536):
            with self.subTest(value=value), patch.object(kernel.os, "sysconf", return_value=value):
                with self.assertRaisesRegex(RuntimeUnavailableError, "4 KiB kernel profile"):
                    kernel.require_android_page_size()

    def test_unsupported_kernel_cannot_start_or_change_presentation(self):
        runtime = engine.WaydroidEngine()
        with patch.object(kernel.os, "sysconf", return_value=16384), \
             patch.object(engine, "_activate_fp6_software_presentation") as presentation, \
             patch.object(engine, "_prepare_interactive_windowing") as windowing, \
             patch.object(runtime, "status") as status, \
             patch.object(engine.subprocess, "run") as command:
            with self.assertRaisesRegex(RuntimeUnavailableError, "16384 bytes"):
                runtime.ensure_ready()
            presentation.assert_not_called()
            windowing.assert_not_called()
            status.assert_not_called()
            command.assert_not_called()

    def test_existing_state_cannot_bypass_kernel_check(self):
        with patch.object(initialize.os, "geteuid", return_value=0), \
             patch.object(kernel.os, "sysconf", return_value=65536), \
             patch.object(initialize, "runtime_complete", return_value=True) as state, \
             patch.object(initialize.subprocess, "run") as command:
            with self.assertRaisesRegex(RuntimeUnavailableError, "65536 bytes"):
                initialize.initialize()
            state.assert_not_called()
            command.assert_not_called()

    def test_container_entrypoint_checks_before_loading_provider(self):
        with patch.object(kernel.os, "getuid", return_value=0), \
             patch.object(kernel.os, "sysconf", return_value=16384), \
             patch.object(waydroid_hardware, "verify_confinement") as confinement:
            with self.assertRaises(RuntimeUnavailableError):
                waydroid_hardware.main()
            confinement.assert_not_called()
