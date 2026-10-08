# SPDX-License-Identifier: Apache-2.0
"""Viewer crop callbacks preserve the approved v70 source geometry."""
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from luma_viewer.composition import ViewerUI
from luma_viewer.application import ViewerWindow
from luma_viewer.annotations import Viewport


class CropInteractionTests(unittest.TestCase):
    def test_crop_handle_and_move_update_framing_before_commit(self):
        viewport=Viewport(1200,800,1200,800)
        window=SimpleNamespace(cropping=True,pixbuf=object(),saving=False,
            crop_box=(120,80,960,640),crop_aspect='free',
            _viewport=lambda:viewport,_page_size=lambda:(1200,800),
            marks_area=SimpleNamespace(queue_draw=Mock()),_crop_drag=None)
        window._mark_update=lambda gesture,dx,dy:ViewerWindow._mark_update(window,gesture,dx,dy)
        ViewerWindow._mark_begin(window,None,120,80)
        self.assertEqual(window._crop_drag[0],'nw')
        ViewerWindow._mark_update(window,None,60,40)
        self.assertEqual(window.crop_box,(180,120,900,600))
        ViewerWindow._mark_end(window,None,60,40)
        self.assertIsNone(window._crop_drag)
        ViewerWindow._mark_begin(window,None,500,400)
        self.assertEqual(window._crop_drag[0],'move')
        ViewerWindow._mark_update(window,None,20,10)
        self.assertEqual(window.crop_box,(200,130,900,600))
        window.marks_area.queue_draw.assert_called()

    def test_aspect_changes_reset_to_centered_source_box(self):
        window = SimpleNamespace(_page_size=lambda: (1200, 800),
            crop_box=(96, 64, 1008, 672), _processing_changed=Mock(),
            _refresh_actions=Mock())
        # Exact vwSetAspect geometry for a 1200x800 document, independent of
        # the previous crop. Repeated aspect changes must not shrink it away.
        for aspect, expected in [('square', (240, 40, 720, 720)),
                ('4:5', (312, 40, 576, 720)),
                ('orig', (60, 40, 1080, 720)),
                ('16:9', (60, 96.25, 1080, 607.5)),
                ('square', (240, 40, 720, 720))]:
            with self.subTest(aspect=aspect):
                ViewerUI._crop_aspect(window, aspect)
                self.assertEqual(window.crop_aspect, aspect)
                for actual, required in zip(window.crop_box, expected):
                    self.assertAlmostEqual(actual, required)
        previous = window.crop_box
        ViewerUI._crop_aspect(window, 'free')
        self.assertEqual(window.crop_box, previous)
        self.assertEqual(window.crop_aspect, 'free')
        window._refresh_actions.assert_called_once()


if __name__ == '__main__':
    unittest.main()
