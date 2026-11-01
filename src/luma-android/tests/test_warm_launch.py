# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
from unittest.mock import patch

from luma_android import engine as engine_module
from luma_android.engine import WaydroidEngine


class WarmLaunch(unittest.TestCase):
    def setUp(self):
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        for target in (patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime.name}),
                       patch("luma_android.engine._activate_fp6_software_presentation"),
                       patch("luma_android.engine._prepare_interactive_windowing"),
                       patch("luma_android.engine.require_android_page_size"),
                       patch("luma_android.engine.device_class", return_value="handheld"),
                       patch("luma_android.engine.surface_treatment", return_value="dark")):
            target.start()
            self.addCleanup(target.stop)

    def test_stamped_session_skips_the_readiness_pass(self):
        engine = WaydroidEngine("waydroid")
        config = engine_module.load_runtime_config()
        with patch("luma_android.engine._session_identity", return_value="10:5/20:6"):
            engine_module._write_ready_stamp(engine_module._ready_key(config.idle_freeze, True))
            with patch.object(engine, "status") as status, patch.object(engine, "_run") as run:
                self.assertFalse(engine.ensure_ready(multi_window=True, detached=True))
        status.assert_not_called()
        run.assert_not_called()

    def test_restarted_session_or_changed_policy_runs_the_readiness_pass(self):
        engine = WaydroidEngine("waydroid")
        config = engine_module.load_runtime_config()
        with patch("luma_android.engine._session_identity", return_value="10:5/20:6"):
            engine_module._write_ready_stamp(engine_module._ready_key(config.idle_freeze, True))
        for identity, multi_window in (("11:7/20:6", True), ("10:5/20:6", False)):
            with patch("luma_android.engine._session_identity", return_value=identity), \
                 patch.object(engine, "status",
                              side_effect=RuntimeError("readiness pass")) as status:
                with self.assertRaisesRegex(RuntimeError, "readiness pass"):
                    engine.ensure_ready(multi_window=multi_window, detached=True)
            status.assert_called_once()

    def test_no_running_session_writes_no_stamp(self):
        with patch("luma_android.engine._session_identity", return_value=None):
            self.assertIsNone(engine_module._ready_key(False, True))
            engine_module._write_ready_stamp(None)
        self.assertIsNone(engine_module._ready_stamp())


if __name__ == "__main__":
    unittest.main()
