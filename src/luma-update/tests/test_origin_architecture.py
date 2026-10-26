# SPDX-License-Identifier: Apache-2.0
"""An origin label must not override the running machine's architecture."""
import unittest

import fakes
from luma_update.engine import Engine


class OriginArchitecture(unittest.TestCase):
    def test_signed_desktop_graph_is_not_selected_by_arm_device(self):
        rig = fakes.Rig()
        self.addCleanup(rig.close)
        rig.publish(fakes.graph_doc([fakes.release("1.0.0", 1),
                                    fakes.release("1.0.1", 2)]))
        engine = Engine(rig.paths, rig.settings, rig.backend, rig.probes, rig.http,
                        clock=lambda: rig.now, arch="aarch64",
                        boot_id=lambda: "test-boot", greenboot_installed=True)
        self.assertIsNone(engine.check())
        self.assertEqual(engine.status().last_error_class, "architecture")
        self.assertFalse(engine.download(user_initiated=True))
        self.assertEqual(rig.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
