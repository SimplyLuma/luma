# SPDX-License-Identifier: Apache-2.0
"""Look thumbnails are derived from the selected source photo."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from luma_darkroom.look_preview import render_look_preview


class LookPreviewTests(unittest.TestCase):
    def test_selected_source_and_look_change_the_preview(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "photo.png"
            Image.new("RGB", (600, 400), (150, 90, 45)).save(source)
            as_shot = Image.open(BytesIO(render_look_preview(source, {})))
            tri_x = Image.open(BytesIO(render_look_preview(source, {"sat": -100})))
            self.assertEqual(as_shot.size, (240, 160))
            self.assertNotEqual(as_shot.getpixel((20, 20)), tri_x.getpixel((20, 20)))
            gray = tri_x.getpixel((20, 20))
            self.assertEqual(gray[0], gray[1])
            self.assertEqual(gray[1], gray[2])


if __name__ == "__main__":
    unittest.main()
