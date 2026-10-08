# SPDX-License-Identifier: GPL-3.0-only
"""First-frame/resize regression coverage without opening a desktop window."""
import unittest
from collections import deque
from types import SimpleNamespace
from gpu_page import GpuPage
from page_input import PageInput
from gi.repository import Gdk


class PagePresentationTest(unittest.TestCase):
    def page(self, width=900, height=700, scale=1):
        attached = []
        page = SimpleNamespace(cursors={}, rejected_sizes=0, snapshot_count=0,
            texture=None, displayed_frame=None, displayed_size=None,
            presentation_samples=deque(), received_samples=deque(),
            expected_buffer_size=(round(width*scale), round(height*scale)),
            get_native=lambda: SimpleNamespace(get_surface=lambda: None),
            get_scale_factor=lambda: scale, get_width=lambda: width,
            get_height=lambda: height, set_cursor_from_name=lambda _: None,
            queue_draw=lambda: None, on_frame=attached.append)
        return page, attached

    def test_initial_wrong_size_still_attaches_without_displaying(self):
        page, attached = self.page()
        frame = dict(id=1, target_id='new-tab', width=640, height=480)
        GpuPage.set_frame(page, object(), frame)
        self.assertIsNone(page.texture)
        self.assertEqual(attached, [frame])
        self.assertEqual(page.rejected_sizes, 1)

    def test_matching_physical_size_displays_after_initial_mismatch(self):
        page, _ = self.page(scale=2)
        GpuPage.set_frame(page, object(), dict(id=1, target_id='tab', width=900, height=600))
        self.assertIsNone(page.texture)
        texture = object()
        GpuPage.set_frame(page, texture, dict(id=2, target_id='tab', width=1800, height=1400))
        self.assertIs(page.texture, texture)

    def test_fractional_request_survives_later_surface_scale_change(self):
        page, _ = self.page(width=899, height=727, scale=1.25)
        page.get_scale_factor = lambda: 1
        page.get_native = lambda: SimpleNamespace(get_surface=lambda: SimpleNamespace(get_scale=lambda: 1.0))
        page.expected_buffer_size = (899,727)
        texture = object()
        GpuPage.set_frame(page, texture, dict(id=1, target_id='tab', width=1124, height=909))
        self.assertIs(page.texture, texture)
        self.assertEqual(page.rejected_sizes, 0)

    def test_resize_retains_frame_without_stretching_or_false_presentations(self):
        page, attached = self.page()
        texture = object()
        accepted = dict(id=1, target_id='tab', width=900, height=700)
        GpuPage.set_frame(page, texture, accepted)
        draws = []
        snapshot = SimpleNamespace(push_clip=lambda _: None, pop=lambda: None, append_texture=lambda tex, rect:
            draws.append((tex, rect.get_width(), rect.get_height())))
        for width in (850, 700, 500, 800, 1000):
            page.get_width = lambda: width
            page.expected_buffer_size = (width, 700)
            GpuPage.set_frame(page, object(),
                dict(id=2, target_id='tab', width=890, height=700))
            GpuPage.do_snapshot(page, snapshot)
            self.assertIs(page.texture, texture)
            self.assertEqual(draws[-1], (texture, 900, 700))
            self.assertEqual(page.presentation_samples[-1][1], 1)
        replacement = object()
        GpuPage.set_frame(page, replacement,
            dict(id=3, target_id='tab', width=1000, height=700))
        GpuPage.do_snapshot(page, snapshot)
        self.assertEqual(draws[-1], (replacement, 1000, 700))
        self.assertEqual(page.presentation_samples[-1][1], 3)

    def test_target_switch_does_not_retain_other_tabs_content(self):
        page, _ = self.page()
        GpuPage.set_frame(page, object(), dict(id=1, target_id='old', width=900, height=700))
        GpuPage.set_frame(page, object(), dict(id=2, target_id='new', width=640, height=480))
        self.assertIsNone(page.texture)
        self.assertIsNone(page.displayed_frame)

    def test_storage_padding_is_not_displayed_as_page_gaps(self):
        page, _ = self.page(width=900, height=700)
        texture = object()
        frame = dict(id=1, target_id='tab', width=1024, height=768,
                     content_x=8, content_y=4, content_width=900, content_height=700)
        GpuPage.set_frame(page, texture, frame)
        self.assertIs(page.texture, texture)
        self.assertEqual(page.displayed_size, (900, 700))
        draws = []
        snapshot = SimpleNamespace(push_clip=lambda r: draws.append(('clip', r.get_width(), r.get_height())),
            pop=lambda: None, append_texture=lambda t, r: draws.append((r.get_x(), r.get_y(), r.get_width(), r.get_height())))
        GpuPage.do_snapshot(page, snapshot)
        self.assertEqual(draws, [('clip', 900, 700), (-8, -4, 1024, 768)])

    def test_native_shortcuts_do_not_also_reach_chromium(self):
        sent = []
        adapter = SimpleNamespace(dispatch=lambda method, payload: sent.append((method, payload)))
        control = Gdk.ModifierType.CONTROL_MASK
        for name in ('w', 'n'):
            key = Gdk.keyval_from_name(name)
            PageInput._key_event(adapter, True, key, 0, control)
            PageInput._key_event(adapter, False, key, 0, control)
            self.assertFalse(PageInput._unfiltered_key(adapter, None, key, 0, control))
        self.assertEqual(sent, [])
        PageInput._key_event(adapter, True, Gdk.KEY_c, 0, control)
        self.assertEqual(sent[0][1]['key'], 'c')

    def test_clear_releases_retained_frame(self):
        page, _ = self.page()
        GpuPage.set_frame(page, object(), dict(id=1, target_id='old', width=900, height=700))
        GpuPage.clear(page)
        self.assertIsNone(page.texture)
        self.assertIsNone(page.displayed_frame)
        self.assertIsNone(page.displayed_size)


if __name__ == '__main__':
    unittest.main()
