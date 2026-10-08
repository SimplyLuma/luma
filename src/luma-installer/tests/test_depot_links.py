# SPDX-License-Identifier: Apache-2.0
"""luma-depot:// and appstream:// links (ADR-028, section 11)."""

import unittest

from luma_installer.depot_links import Link, parse, uri_for


class Links(unittest.TestCase):
    def test_depot_links(self):
        self.assertEqual(parse('luma-depot://app/org.projectluma.Canvas'), Link('app', 'org.projectluma.Canvas'))
        self.assertEqual(parse('luma-depot://install/org.gimp.GIMP'), Link('install', 'org.gimp.GIMP'))
        self.assertEqual(parse('luma-depot://collection/office'), Link('collection', 'office'))
        self.assertEqual(parse('LUMA-DEPOT://App/canvas/'), Link('app', 'canvas'))
        self.assertEqual(parse('luma-depot:///app/canvas'), Link('app', 'canvas'))
        self.assertEqual(parse('luma-depot://app/org.gimp.GIMP.desktop?from=web#top'), Link('app', 'org.gimp.GIMP'))
        self.assertEqual(parse(uri_for('install', 'canvas')), Link('install', 'canvas'))

    def test_appstream_links(self):
        self.assertEqual(parse('appstream://org.mozilla.firefox'), Link('app', 'org.mozilla.firefox'))
        self.assertEqual(parse('appstream:org.mozilla.firefox'), Link('app', 'org.mozilla.firefox'))
        self.assertEqual(parse('appstream://org.gnome.Calculator.desktop'), Link('app', 'org.gnome.Calculator'))
        self.assertEqual(parse('appstream://com.anydesk.Anydesk/'), Link('app', 'com.anydesk.Anydesk'))

    def test_anything_else_is_not_a_link(self):
        for uri in ('', 'https://simplyluma.com/apps/canvas', 'luma-depot:app/canvas',
                    'luma-depot://remove/canvas', 'luma-depot://install/', 'luma-depot://app/a/b',
                    'luma-depot://app/..', 'luma-depot://app/%2e%2e', 'luma-depot://app/-rf',
                    'luma-depot://collection/Office', 'luma-depot://app/has space',
                    'appstream://a/b', 'appstream://', 'appstream://../../etc/passwd',
                    'luma-depot://app/' + 'x' * 600, 'flatpak+https://dl.flathub.org/repo/', None, 42):
            with self.subTest(uri=uri):
                self.assertIsNone(parse(uri))
        with self.assertRaises(ValueError):
            uri_for('uninstall', 'canvas')


if __name__ == '__main__':
    unittest.main()
