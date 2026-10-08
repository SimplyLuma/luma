# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from luma_darkroom.editing import Editor
from luma_darkroom.engine import ExportJob, ImagingUnavailable, RasterEngine, load_pillow
from luma_darkroom.model import Crop, Document, ExportPreset, Mask


class RasterEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.Image, *_ = load_pillow()
        except ImagingUnavailable as error:
            raise unittest.SkipTest(str(error)) from error

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.png"
        image = self.Image.new("RGB", (64, 48))
        image.putdata([(x * 4, y * 5, min(255, (x + y) * 3)) for y in range(48) for x in range(64)])
        image.save(self.source)
        self.document = Document.new(self.source.as_uri(), width=64, height=48)
        self.editor = Editor(self.document)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepared_preview_reuses_source_without_changing_pixels_or_input(self):
        engine = RasterEngine(self.document)
        prepared = engine.open_source(48)
        original = prepared[0].tobytes()
        for exposure in (.25, .5, 1.0):
            self.editor.set_adjustment("exposure", exposure)
            self.editor.set_crop(Crop(left=.1, right=.9))
            expected = engine.render(max_dimension=48)
            with patch.object(engine, "open_source", side_effect=AssertionError("Preview decoded again")):
                actual = engine.render(prepared=prepared)
            self.assertEqual(actual.image.tobytes(), expected.image.tobytes())
            self.assertEqual(actual.histogram, expected.histogram)
            self.assertEqual(prepared[0].tobytes(), original)

    def test_local_detail_does_not_shift_a_flat_field(self):
        image = self.Image.new("RGBA", (180, 120), (80, 130, 170, 190))
        engine = RasterEngine(self.document)
        for kind in ("texture", "clarity"):
            for amount in (-100, 50, 100):
                self.editor.set_adjustment(kind, amount)
                self.assertEqual(engine._apply_adjustments(image, self.document.raw_development).tobytes(), image.tobytes())

    def test_sky_gradient_uses_vertical_coverage(self):
        mask = Mask("sky", "Sky", "linear-gradient", geometry={"axis": "y", "start": .45, "end": 0})
        coverage = RasterEngine(self.document)._mask_image(mask, (4, 4))
        self.assertEqual(coverage.getpixel((0, 0)), 255)
        self.assertEqual(coverage.getpixel((3, 0)), 255)
        self.assertEqual(coverage.getpixel((0, 3)), 0)

    def test_16_bit_delivery_is_not_silently_saved_as_8_bit(self):
        destination = self.root / "out.tiff"
        job = ExportJob(self.document, destination, ExportPreset(format="TIFF", bit_depth=16))
        job.start(); self.assertTrue(job.wait(5))
        self.assertIn("16-bit delivery", job.error)
        self.assertFalse(destination.exists())

    def test_camera_orientation_and_full_source_dimensions(self) -> None:
        source = self.root / "portrait.jpg"
        image = self.Image.new("RGB", (120, 80), (70, 140, 190))
        exif = image.getexif()
        exif[274] = 6
        image.save(source, exif=exif)
        original = source.read_bytes()
        document = Document.new(source.as_uri())
        preview = RasterEngine(document).render(max_dimension=60)
        self.assertEqual(preview.image.size, (40, 60))
        self.assertEqual((document.source.width, document.source.height), (80, 120))
        self.assertEqual(source.read_bytes(), original)
        destination = self.root / "export.jpg"
        job = ExportJob(document, destination, ExportPreset(format="JPEG", metadata="all"))
        job.start()
        self.assertTrue(job.wait(10))
        self.assertIsNone(job.error)
        with self.Image.open(destination) as exported:
            self.assertEqual(exported.size, (80, 120))
            self.assertEqual(exported.getexif().get(274, 1), 1)


    def _render_pixels(self, pixels, **adjustments):
        source = self.root / "pixels.png"
        image = self.Image.new("RGB", (len(pixels), 1))
        image.putdata(pixels)
        image.save(source)
        document = Document.new(source.as_uri(), width=len(pixels), height=1)
        editor = Editor(document)
        for key, value in adjustments.items():
            editor.set_adjustment(key, value)
        rendered = RasterEngine(document).render().image
        return [rendered.getpixel((x, 0))[:3] for x in range(len(pixels))]

    def test_cooling_keeps_a_clipped_sun_white(self) -> None:
        # A clamp per channel turned a clipped sun cyan: red scaled down,
        # blue pinned at full scale.
        sun, grey = (255, 255, 255), (128, 128, 128)
        cooled = self._render_pixels([sun, grey], temperature=-60)
        self.assertEqual(cooled[0], sun)
        self.assertLess(cooled[1][0], 128)
        self.assertGreater(cooled[1][2], 128)
        warmed = self._render_pixels([sun, grey], temperature=60)
        self.assertEqual(warmed[0], sun)
        self.assertGreater(warmed[1][0], 128)

    def test_near_white_fades_into_the_highlight_hold(self) -> None:
        values = [self._render_pixels([(level, level, level)], temperature=-60)[0] for level in (236, 244, 250, 255)]
        spreads = [pixel[2] - pixel[0] for pixel in values]
        self.assertEqual(spreads, sorted(spreads, reverse=True))
        self.assertEqual(spreads[-1], 0)

    def test_an_overshooting_channel_rolls_toward_white_without_darkening(self) -> None:
        warm = (255, 200, 100)
        red, green, blue = self._render_pixels([warm], temperature=40)[0]
        self.assertEqual(red, 255)
        # A plain clamp leaves (255, 200, 80); the lost red brightens the rest.
        self.assertGreater(green, 200)
        self.assertGreater(blue, 80)
        self.assertGreater(green, blue)

    def test_framing_a_crop_renders_the_whole_photo(self) -> None:
        crop = Crop(**{field: getattr(self.document.crop, field) for field in self.document.crop.__dataclass_fields__})
        crop.left, crop.top, crop.right, crop.bottom = .25, .25, .75, .75
        self.editor.set_crop(crop)
        engine = RasterEngine(self.document)
        self.assertEqual(engine.render().image.size, (32, 24))
        self.assertEqual(engine.render(crop=False).image.size, (64, 48))

    def test_adjustments_change_pixels_and_histogram(self) -> None:
        engine = RasterEngine(self.document)
        before = engine.render()
        self.editor.set_adjustment("exposure", 1.0)
        self.editor.set_adjustment("saturation", -35)
        after = engine.render()
        self.assertNotEqual(before.image.tobytes(), after.image.tobytes())
        self.assertEqual(len(after.histogram), 4)
        self.assertTrue(all(len(channel) == 256 for channel in after.histogram))
        self.assertNotEqual(before.histogram, after.histogram)

    def test_crop_is_rendered_but_original_is_untouched(self) -> None:
        original = self.source.read_bytes()
        self.editor.set_crop(Crop(left=0.25, top=0.25, right=0.75, bottom=0.75))
        result = RasterEngine(self.document).render()
        self.assertEqual(result.image.size, (32, 24))
        self.assertEqual(self.source.read_bytes(), original)

    def test_adjustment_layer_with_radial_mask(self) -> None:
        layer = self.editor.add_layer("adjustment", "Spotlight")
        self.editor.set_adjustment("exposure", 1.5, layer_id=layer.id)
        self.editor.add_mask(layer.id, "radial-gradient", geometry={"x": 0.5, "y": 0.5, "radius": 0.35})
        result = RasterEngine(self.document).render()
        center = result.image.getpixel((32, 24))
        corner = result.image.getpixel((0, 0))
        self.assertGreater(sum(center[:3]), sum(corner[:3]))

    def test_brush_mask_is_real_coverage(self) -> None:
        layer = self.editor.add_layer("adjustment", "Local lift")
        self.editor.set_adjustment("exposure", 1.0, layer_id=layer.id)
        self.editor.add_mask(layer.id, "brush", geometry={"points": [[0.5, 0.5]], "size": 0.25})
        result = RasterEngine(self.document).render().image
        baseline = RasterEngine(Document.new(self.source.as_uri())).render().image
        self.assertGreater(sum(result.getpixel((32, 24))[:3]), sum(baseline.getpixel((32, 24))[:3]))
        self.assertEqual(result.getpixel((0, 0)), baseline.getpixel((0, 0)))

    def test_export_is_atomic_and_full_resolution(self) -> None:
        destination = self.root / "out.png"
        job = ExportJob(self.document, destination, ExportPreset(format="PNG"))
        job.start()
        self.assertTrue(job.wait(5))
        self.assertIsNone(job.error)
        self.assertEqual(job.result, destination)
        with self.Image.open(destination) as exported:
            self.assertEqual(exported.size, (64, 48))
        self.assertEqual(list(self.root.glob("*.darkroom-export.tmp")), [])

    def test_copyright_export_strips_location(self) -> None:
        source = self.Image.open(self.source)
        exif = self.Image.Exif()
        exif[315] = "Luma Photographer"
        exif[33432] = "Copyright 2026"
        exif[34853] = {1: "N", 2: (1.0, 2.0, 3.0)}
        source.save(self.source, exif=exif)
        source.close()
        destination = self.root / "private.jpg"
        job = ExportJob(self.document, destination, ExportPreset(format="JPEG", metadata="copyright"))
        job.start()
        self.assertTrue(job.wait(5))
        self.assertIsNone(job.error)
        with self.Image.open(destination) as exported:
            exported_exif = exported.getexif()
            self.assertEqual(exported_exif.get(315), "Luma Photographer")
            self.assertNotIn(34853, exported_exif)


if __name__ == "__main__":
    unittest.main()
