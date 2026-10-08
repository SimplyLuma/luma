"""Filer's native conform fixture stays isolated and matches the v70 tree."""

import importlib.util
import configparser
import hashlib
import json
import stat
import struct
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("filer_fixture", ROOT / "tools/filer-fixture.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FilerFixtureTest(unittest.TestCase):
    def test_launch_and_empty_drive_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "fixture"
            data = json.loads((ROOT / "tests/fixtures/filer-v70.json").read_text())
            launch = MODULE.materialize(data, root, ROOT / "tests/fixtures/filer-images", date(2026, 9, 26))
            self.assertEqual(launch.name, "Launch")
            self.assertEqual(len(list(launch.iterdir())), 14)
            self.assertEqual((launch / "launch-plan.md").stat().st_size, 14_000)
            self.assertIn(b"press all day", (launch / "launch-plan.md").read_bytes())
            self.assertEqual((launch / "hero-writing.webp").stat().st_size, 412_000)
            press_photo = launch / "Press kit" / "press-photo-1.webp"
            self.assertEqual(press_photo.stat().st_size, 2_400_000)
            self.assertTrue(press_photo.read_bytes().startswith(b"RIFF"))
            webp = (launch / "hero-writing.webp").read_bytes()
            self.assertTrue(webp.startswith(b"RIFF"))
            self.assertEqual(struct.unpack_from("<I", webp, 4)[0], len(webp) - 8)
            self.assertEqual(list((root / "Fable SSD/Luma/Apps").iterdir()), [])
            self.assertEqual(len(list((root / "Recent").iterdir())), 5)
            self.assertEqual(stat.S_IMODE(launch.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((launch / "launch-plan.md").stat().st_mode), 0o444)
            self.assertEqual(stat.S_IMODE((root / "Profile").stat().st_mode), 0o700)
            self.assertEqual((root / "Profile/config/filer-grid.tsv").read_text().splitlines()[0],
                             "Press kit\t12 items")
            self.assertEqual((root / "Profile/config/filer-search.tsv").read_text().splitlines(),
                             data["search_order"])
            info = configparser.ConfigParser()
            info.read(root / "Profile/config/filer-info.ini")
            self.assertEqual(info["Launch"]["contains"], "14 items")
            self.assertEqual(info["Press kit"]["modified"], "Today, 9:40 AM")
            self.assertEqual(info["press-release.write"]["kind"], "Write document")
            self.assertEqual(info["hero-writing.webp"]["dimensions"], "2000 × 1125")
            uri = (launch / "hero-writing.webp").as_uri()
            thumb_name = hashlib.md5(uri.encode(), usedforsecurity=False).hexdigest() + ".png"
            cached = (root / "Profile/cache/thumbnails/large" / thumb_name).read_bytes()
            self.assertIn(b"Thumb::URI\0" + uri.encode(), cached)
            self.assertEqual(cached.count(b"Thumb::URI\0"), 1)
            self.assertEqual(cached.count(b"Thumb::MTime\0"), 1)
    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = {"files": [], "tree": {"Home": [["../escape", "folder"]]}}
            with self.assertRaises(ValueError):
                MODULE.materialize(fixture, Path(temp) / "fixture", Path(temp))


if __name__ == "__main__":
    unittest.main()
