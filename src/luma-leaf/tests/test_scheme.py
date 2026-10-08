# SPDX-License-Identifier: Apache-2.0
"""A book's XHTML reaches WebKit in a form it shows whole."""
import unittest

from luma_leaf.scheme import section_document

HEAD = b'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
XHTML = "application/xhtml+xml"


class Sections(unittest.TestCase):
    def test_well_formed_sections_are_served_byte_for_byte(self):
        data = HEAD + b"<p>Mr.&#160;Bennet &amp; Co.</p></body></html>"
        self.assertEqual(section_document(data, XHTML), (data, XHTML))

    def test_html_entities_and_bare_ampersands_are_repaired(self):
        data, media = section_document(HEAD + b"<p>Mr.&nbsp;Tester wrote to AT&T &eacute;t&eacute;.</p></body></html>", XHTML)
        self.assertEqual(media, XHTML)
        self.assertIn(b"Mr.&#160;Tester wrote to AT&amp;T &#233;t&#233;.", data)

    def test_a_declared_xhtml_dtd_keeps_its_entities(self):
        data = (b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>&nbsp;</p></body></html>')
        self.assertEqual(section_document(data, XHTML), (data, XHTML))

    def test_markup_xml_cannot_read_is_served_as_html(self):
        data = HEAD + b"<p>an unclosed paragraph<br></body></html>"
        self.assertEqual(section_document(data, XHTML), (data, "text/html"))

    def test_other_files_are_untouched(self):
        self.assertEqual(section_document(b"p { color: red }", "text/css"), (b"p { color: red }", "text/css"))


if __name__ == "__main__":
    unittest.main()
