# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ApplicationContractTest(unittest.TestCase):
    def test_one_adaptive_application(self) -> None:
        manifest = tomllib.loads((ROOT / "luma-app.toml").read_text(encoding="utf-8"))
        self.assertEqual(manifest["application"]["id"], "org.projectluma.Darkroom")
        self.assertTrue(manifest["responsive"]["adaptive"])
        self.assertTrue({360, 500, 1024}.issubset(manifest["responsive"]["test_widths"]))
        self.assertEqual(set(manifest["responsive"]["presentation_modes"]), {"windowed", "fullscreen-mobile"})

    def test_native_and_source_safe_boundaries(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "luma_darkroom").glob("*.py"))
        for forbidden in ("subprocess.", "os.system(", "WebKit", "webview", "waydroid"):
            self.assertNotIn(forbidden, source)
        self.assertIn("ImageCms.profileToProfile", source)
        self.assertIn("os.replace", source)
        self.assertIn("max_dimension=None", source)
        self.assertIn("LumaUI.Histogram", source)
        self.assertIn("LumaUI.CurveEditor", source)

    def test_save_and_export_are_distinct(self) -> None:
        window = (ROOT / "luma_darkroom" / "window.py").read_text(encoding="utf-8")
        self.assertIn("DocumentStore.save", window)
        self.assertIn("ExportJob", window)
        self.assertIn("Save Version", window)
        self.assertIn("Editable document saved", window)


if __name__ == "__main__":
    unittest.main()

