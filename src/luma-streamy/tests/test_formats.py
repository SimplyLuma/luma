# SPDX-License-Identifier: Apache-2.0
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from luma_streamy.graph import only_alpha, prefer_alpha  # noqa: E402


class PreferAlpha(unittest.TestCase):
    def test_alpha_formats_come_first(self):
        caps = ("video/x-raw(memory:DMABuf), format=(string)DMA_DRM, drm-format=(string){ XR24:0x0100000000000002, "
                "AR24:0x0100000000000002, XR24 }; video/x-raw, format=(string){ BGRx, BGRA, NV12 }")
        out = prefer_alpha(caps)
        self.assertIn("drm-format=(string){ AR24:0x0100000000000002, XR24:0x0100000000000002, XR24 }", out)
        self.assertIn("format=(string){ BGRA, BGRx, NV12 }", out)

    def test_single_formats_are_kept(self):
        caps = "video/x-raw, format=(string)BGRx, width=(int)640"
        self.assertEqual(prefer_alpha(caps), caps)



class OnlyAlpha(unittest.TestCase):
    def test_cut_to_alpha_formats(self):
        caps = ("video/x-raw(memory:DMABuf), format=(string)DMA_DRM, drm-format=(string){ XR24:0x0100000000000010, "
                "AR24:0x0100000000000010, AB24 }; video/x-raw, format=(string){ BGRx, BGRA, NV12 }")
        self.assertEqual(only_alpha(caps), "video/x-raw(memory:DMABuf), format=(string)DMA_DRM, drm-format=(string)"
                                           "{ AR24:0x0100000000000010, AB24 }; video/x-raw, format=(string)BGRA")
        self.assertEqual(only_alpha("video/x-raw, format=(string)BGRx"), "")

if __name__ == "__main__":
    unittest.main()
