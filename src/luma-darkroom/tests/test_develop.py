# SPDX-License-Identifier: Apache-2.0
"""Physical exposure, precision and monotonic tonal behavior, not UI snapshots."""
import unittest
import numpy as np
from luma_darkroom.develop import CameraFrame, LinearImage, develop, develop_linear, srgb_decode
from luma_darkroom.model import Adjustment


def adjustments(**values):
    return [Adjustment(id=key, kind=key, value=value) for key, value in values.items()]


def neutral(values):
    return LinearImage(np.repeat(np.array(values, dtype=np.float32)[None, :, None], 3, axis=-1), np.eye(3, dtype=np.float32))


class DevelopmentTests(unittest.TestCase):
    def test_exposure_is_in_photographic_stops(self):
        source = neutral([.018, .18, .5, 1.0, 2.0])
        for ev in (-2, -1, 0, 1, 2):
            result = develop_linear(source, adjustments(exposure=ev))
            np.testing.assert_allclose(result, source.pixels * 2 ** ev, rtol=1e-6)
        # Middle gray +1 EV is about 162 sRGB, not 236 (doubling code 118).
        self.assertAlmostEqual(develop(source, adjustments(exposure=1)).getpixel((1, 0))[0], 162, delta=1)

    def test_bright_detail_survives_until_output_and_can_be_recovered(self):
        source = neutral([.4, .5, .6])
        self.assertEqual([develop(source, adjustments(exposure=2)).getpixel((x, 0))[0] for x in range(3)], [255]*3)
        recovered = develop(source, adjustments(exposure=2, highlights=-100))
        codes = [recovered.getpixel((x, 0))[0] for x in range(3)]
        self.assertEqual(codes, sorted(set(codes)))
        self.assertLess(max(codes), 255)
        # A second render always starts from the immutable source.
        np.testing.assert_allclose(source.pixels[0, :, 0], [.4, .5, .6])

    def test_all_tonal_controls_are_monotonic(self):
        source = neutral(np.linspace(0, 4, 4096))
        for kind in ('contrast', 'shadows', 'highlights', 'whites', 'blacks'):
            for value in (-100, -50, 50, 100):
                with self.subTest(kind=kind, value=value):
                    values = develop_linear(source, adjustments(**{kind: value}))[0, :, 0]
                    self.assertTrue(np.isfinite(values).all())
                    self.assertTrue((np.diff(values) >= -1e-6).all())

    def test_highlights_and_shadows_target_different_ranges(self):
        source = neutral([0, .02, .18, .7, 2])
        highlights = develop_linear(source, adjustments(highlights=-100))[0, :, 0]
        shadows = develop_linear(source, adjustments(shadows=100))[0, :, 0]
        np.testing.assert_allclose(highlights[:3], [0, .02, .18], atol=1e-6)
        self.assertLess(highlights[3], .7)
        self.assertGreater(shadows[1], .02)
        self.assertEqual(shadows[0], 0)
        np.testing.assert_allclose(shadows[3:], [.7, 2])
        self.assertGreater(develop_linear(source, adjustments(blacks=100))[0, 0, 0], 0)

    def test_16_bit_samples_and_resizing_keep_sub_8bit_detail(self):
        samples = np.repeat(np.arange(4096, dtype=np.uint16)[None, :, None], 3, axis=-1)
        frame = CameraFrame(samples, np.eye(3, dtype=np.float32), 1)
        full, small = frame.preview(), frame.preview(1024)
        self.assertEqual(len(np.unique(full.pixels[..., 0])), 4096)
        self.assertEqual(len(np.unique(small.pixels[..., 0])), 1024)
        self.assertFalse(frame.samples.flags.writeable)

    def test_camera_matrix_does_not_clip_before_development(self):
        matrix = np.array([[2, -1, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
        source = LinearImage(np.array([[[1, .25, .25]]], dtype=np.float32), matrix)
        result = develop_linear(source, adjustments(exposure=-1))
        np.testing.assert_allclose(result, [[[.875, .125, .125]]])
        warmed = develop_linear(source, adjustments(temperature=100))
        self.assertGreater(warmed[0, 0, 0], 2)

    def test_raw_tone_curve_runs_before_8bit_quantization(self):
        source = neutral(srgb_decode([.001, .0015]))
        self.assertEqual([develop(source, []).getpixel((x, 0))[0] for x in range(2)], [0, 0])
        curve = adjustments(**{'tone-curve': [[0, 0], [.01, .5], [1, 1]]})
        self.assertEqual([develop(source, curve).getpixel((x, 0))[0] for x in range(2)], [13, 19])

    def test_zero_edit_srgb_round_trip_and_alpha(self):
        from PIL import Image
        image = Image.new('RGBA', (256, 1))
        image.putdata([(v, 255-v, v//2, v) for v in range(256)])
        result = develop(LinearImage.from_display(image), [])
        self.assertEqual(result.tobytes(), image.tobytes())

if __name__ == '__main__': unittest.main()
