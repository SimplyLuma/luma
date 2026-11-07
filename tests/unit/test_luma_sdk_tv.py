#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""`luma lint` for [tv]: the TV home opt-in and its installed desktop keys."""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/sdk"))

from luma_sdk.tv import tile_icon_name, tv_errors, tv_project_errors  # noqa: E402
from luma_sdk.validation import validate_manifest  # noqa: E402

APP = {"application": {"id": "org.example.App"}}


class TvTable(unittest.TestCase):
    def test_absent_or_declined_is_valid(self):
        self.assertEqual(tv_errors(dict(APP)), [])
        self.assertEqual(tv_errors({**APP, "tv": {"supported": False}}), [])

    def test_supported_needs_a_tile_and_a_known_presentation(self):
        self.assertEqual(tv_errors({**APP, "tv": {"supported": True, "tile": "data/org.example.App-tv.svg"}}), [])
        errors = tv_errors({**APP, "tv": {"supported": True, "presentation": "couch", "colour": 1}})
        self.assertIn("tv.colour is not a known key", errors)
        self.assertTrue(any("tv.tile" in error for error in errors))
        self.assertTrue(any("tv.presentation" in error for error in errors))
        self.assertEqual(tv_errors({**APP, "tv": "yes"}), ["tv must be a table"])

    def test_project_tile_and_desktop_keys_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            tile = root / "data" / f"{tile_icon_name('org.example.App')}.svg"
            tile.write_text("<svg/>")
            desktop = root / "data/org.example.App.desktop"
            desktop.write_text("[Desktop Entry]\nType=Application\nName=App\n")
            manifest = {**APP, "tv": {"supported": True, "tile": "data/org.example.App-tv.svg", "presentation": "tv"}}
            errors = tv_project_errors(manifest, root, desktop)
            self.assertEqual(len(errors), 3)
            desktop.write_text("[Desktop Entry]\nType=Application\nName=App\nX-Luma-TV=true\n"
                               "X-Luma-TV-Tile=org.example.App-tv\nX-Luma-TV-Presentation=tv\n")
            self.assertEqual(tv_project_errors(manifest, root, desktop), [])
            manifest["tv"]["tile"] = "../elsewhere.svg"
            self.assertEqual(tv_project_errors(manifest, root, desktop), ["tv.tile must stay inside the project"])


class ShippedApps(unittest.TestCase):
    def test_tide_declares_tv_and_installs_the_keys(self):
        root = REPO_ROOT / "src/luma-tide"
        manifest = tomllib.loads((root / "luma-app.toml").read_text())
        self.assertIs(manifest["tv"]["supported"], True)
        self.assertEqual([e for e in validate_manifest(manifest, root) if e.startswith("tv")], [])
        self.assertEqual(tv_project_errors(manifest, root, root / "data/org.projectluma.Tide.desktop"), [])

    def test_weather_desktop_entry_opts_in(self):
        text = (REPO_ROOT / "src/prairie-core/data/org.projectluma.Weather.desktop").read_text()
        self.assertIn("X-Luma-TV=true\n", text)
        self.assertIn("X-Luma-TV-Tile=org.projectluma.Weather-tv\n", text)
        self.assertTrue((REPO_ROOT / "src/prairie-core/data/org.projectluma.Weather-tv.svg").is_file())


if __name__ == "__main__":
    unittest.main()
