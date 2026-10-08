# SPDX-License-Identifier: Apache-2.0
import unittest
from unittest.mock import patch
import subprocess
from luma_viewer.ocr import recognize, TextRegion, detection_kind

class OcrTests(unittest.TestCase):
    def test_missing_optional_engine_is_honest(self):
        with patch('luma_viewer.ocr.shutil.which',return_value=None):self.assertEqual(recognize(b'image'),[])
    def test_pixels_are_piped_and_bad_or_low_confidence_words_rejected(self):
        data='left\ttop\twidth\theight\tconf\ttext\n10\t20\t30\t10\t92\tTotal\n0\t0\t10\t10\t12\tGuess\nx\t0\t10\t10\t92\tBad\n'
        with patch('luma_viewer.ocr.shutil.which',return_value='/usr/bin/tesseract'),patch('luma_viewer.ocr.subprocess.run',return_value=subprocess.CompletedProcess([],0,data.encode(),b'')) as run:
            self.assertEqual(recognize(b'private pixels'),[TextRegion('Total',10,20,30,10)])
            args,kw=run.call_args
            self.assertEqual(args[0],['/usr/bin/tesseract','stdin','stdout','tsv']);self.assertEqual(kw['input'],b'private pixels')

    def test_detects_full_lines_without_treating_order_ids_as_phones(self):
        for value,kind in (('(510) 555-0183','tel'),('118 Alameda Ave, Studio 4','addr'),
                ('Sep 18, 2026 10:14 AM','date'),('$62.00','money'),('ORDER #2048',None),('2048',None)):
            self.assertEqual(detection_kind(value),kind)

    def test_words_on_a_line_are_selected_and_detected_together(self):
        data='page_num\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n1\t1\t1\t1\t10\t20\t30\t10\t92\t(510)\n1\t1\t1\t1\t50\t20\t50\t10\t90\t555-0183\n'
        with patch('luma_viewer.ocr.shutil.which',return_value='/usr/bin/tesseract'),patch('luma_viewer.ocr.subprocess.run',return_value=subprocess.CompletedProcess([],0,data.encode(),b'')):
            runs=recognize(b'image')
        self.assertEqual(runs,[TextRegion('(510) 555-0183',10,20,90,10)])
        self.assertEqual(detection_kind(runs[0].text),'tel')
