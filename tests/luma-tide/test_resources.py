import tempfile
import unittest
from pathlib import Path

from luma_tide.resources import stylesheet_path


class StylesheetResourceTests(unittest.TestCase):
    def test_source_checkout_uses_its_own_stylesheet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            style = root / "data/tide.css"
            style.parent.mkdir()
            style.write_text("/* source */")
            self.assertEqual(stylesheet_path(root / "luma_tide/resources.py"), style)

    def test_staged_install_uses_its_prefix_not_the_live_install(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "usr"
            style = prefix / "share/luma-tide/tide.css"
            style.parent.mkdir(parents=True)
            style.write_text("/* staged */")
            module = prefix / "lib/python3.14/site-packages/luma_tide/resources.py"
            self.assertEqual(stylesheet_path(module), style)
