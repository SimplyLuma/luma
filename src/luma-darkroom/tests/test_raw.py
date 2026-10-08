# SPDX-License-Identifier: Apache-2.0
"""Exercise real RAW decoding with a small, generated CFA DNG, no thumbnail."""
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from PIL import Image, TiffImagePlugin
from luma_darkroom.engine import RasterEngine, ExportJob
from luma_darkroom.model import Document, ExportPreset
from luma_darkroom.raw import RawDecodeError, decode_raw, raw_thumbnail


def make_dng(path):
    width, height = 128, 96
    samples = [int(5000 + x * 220 + y * 130) for y in range(height) for x in range(width)]
    photo = Image.frombytes('I;16', (width, height), struct.pack('<' + 'H' * len(samples), *samples))
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    values = {262: 32803, 271: 'Luma', 272: 'DNG regression fixture',
              33421: (2, 2), 33422: bytes((0, 1, 1, 2)),
              50706: bytes((1, 4, 0, 0)), 50707: bytes((1, 1, 0, 0)),
              50708: 'Darkroom DNG fixture', 50714: 0, 50717: 65535,
              50721: tuple(TiffImagePlugin.IFDRational(v, 100) for v in (64, 33, 3, 21, 71, 8, 15, 6, 79)),
              50728: tuple(TiffImagePlugin.IFDRational(1) for _ in range(3)), 50778: 21}
    for tag, value in values.items(): tags[tag] = value
    tags.tagtype[50721] = 10
    photo.save(path, format='TIFF', tiffinfo=tags)


class RawTests(unittest.TestCase):
    def test_real_dng_preview_and_full_resolution_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'sensor.dng'
            make_dng(source)
            before = hashlib.sha256(source.read_bytes()).digest()
            document = Document.new(source.as_uri(), raw=True)
            preview = RasterEngine(document).render(max_dimension=48)
            self.assertEqual(preview.image.size, (48, 36))
            self.assertEqual((document.source.width, document.source.height), (128, 96))
            self.assertGreater(len(preview.image.getcolors(maxcolors=48*36) or []), 20)
            destination = Path(temporary) / 'export.png'
            job = ExportJob(document, destination, ExportPreset(format='PNG'))
            job.start()
            self.assertTrue(job.wait(15))
            self.assertIsNone(job.error)
            with Image.open(destination) as exported: self.assertEqual(exported.size, (128, 96))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), before)

    def test_highlights_recover_detail_that_exposure_only_clips_on_display(self):
        from luma_darkroom.editing import Editor
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'sensor.dng'
            make_dng(source)
            document = Document.new(source.as_uri(), raw=True)
            editor = Editor(document)
            editor.set_adjustment('exposure', 3)
            blown = RasterEngine(document).render().image.crop((40, 30, 100, 70)).convert('RGB')
            editor.set_adjustment('highlights', -50)
            recovered = RasterEngine(document).render().image.crop((40, 30, 100, 70)).convert('RGB')
            self.assertLessEqual(len(blown.getcolors(4096) or []), 2)
            self.assertGreater(len(recovered.getcolors(4096) or []), 2, 'Highlight detail was clipped before the highlights control')

    def test_no_thumbnail_does_not_trigger_sensor_decode(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sensor.dng"
            make_dng(source)
            with patch("luma_darkroom.raw.decode_raw", side_effect=AssertionError("Gallery decoded RAW")):
                with self.assertRaises(RawDecodeError): raw_thumbnail(source)
            self.assertEqual(decode_raw(source).size, (128, 96))

    def test_damaged_raw_is_reported_without_a_decoder_install_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'broken.nef'
            source.write_bytes(b'not a camera file')
            with self.assertRaises(RawDecodeError) as raised: decode_raw(source)
            self.assertNotIn('decoder', str(raised.exception))
            self.assertIn('copy', str(raised.exception))


if __name__ == '__main__': unittest.main()
