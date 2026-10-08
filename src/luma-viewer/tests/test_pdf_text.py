# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
import unittest
from luma_viewer.pdf_text import text_regions


class PDFTextTests(unittest.TestCase):
    def test_unicode_columns_and_empty_lines_keep_character_offsets(self):
        text='éA\n\nB'
        boxes=[SimpleNamespace(x1=x,y1=y,x2=x+6,y2=y+12)
               for x,y in ((10,20),(16,20),(0,0),(0,0),(200,40))]
        runs=text_regions(text,boxes)
        self.assertEqual([(r.text,r.x,r.y) for r in runs],[('éA',10,20),('B',200,40)])

    def test_empty_scanned_page_and_missing_rectangles_are_safe(self):
        self.assertEqual(text_regions('',[]),[])
        self.assertEqual(text_regions('Text',[]),[])
