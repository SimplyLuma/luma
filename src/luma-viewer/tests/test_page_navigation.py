# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
from luma_viewer.application import ViewerWindow


class NavigationTests(unittest.TestCase):
    def test_select_later_page_scrolls_in_document_coordinates(self):
        adjustment=Mock()
        pages=Mock();pages.get_margin_top.return_value=76
        areas=[Mock(),Mock(),Mock()]
        areas[2].compute_bounds.return_value=(True,SimpleNamespace(origin=SimpleNamespace(y=1636)))
        window=SimpleNamespace(page_count=3,saving=False,page=0,selected=1,facts=None,
            document=object(),pdf_scroll=Mock(),pdf_pages=pages,pdf_areas=areas,
            pdf_thumbnails=[SimpleNamespace(face=Mock()) for _ in range(3)],
            _cancel_drag=Mock(),_refresh_pager=Mock(),_refresh_status=Mock(),_refresh_actions=Mock())
        window.pdf_scroll.get_vadjustment.return_value=adjustment
        ViewerWindow._step_page(window,2)
        self.assertEqual(window.page,2);self.assertIs(window.marks_area,areas[2])
        areas[2].compute_bounds.assert_called_once_with(pages)
        adjustment.set_value.assert_called_once_with(1652)
        for thumb in window.pdf_thumbnails:thumb.face.queue_draw.assert_called_once()


if __name__=='__main__':unittest.main()
