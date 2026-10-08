"""Real metadata presentation must not substitute languages or trust icons paths."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from luma_depot.native_metadata import _read, text


class MetadataTests(unittest.TestCase):
    def test_locale_then_untranslated(self):
        node = ET.fromstring('<component><summary xml:lang="bg">Wrong</summary>'
                             '<summary>English</summary><summary xml:lang="fr">Français</summary></component>')
        with patch('luma_depot.native_metadata.GLib.get_language_names', return_value=['en_US','en','C']):
            self.assertEqual(text(node, 'summary'), 'English')
        with patch('luma_depot.native_metadata.GLib.get_language_names', return_value=['fr']):
            self.assertEqual(text(node, 'summary'), 'Français')

    def test_fixed_identity_and_cached_icon_only(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            (base/'icons/128x128').mkdir(parents=True)
            (base/'icons/128x128/real.png').write_bytes(b'fixture')
            source=base/'appstream.xml'
            source.write_text('<components><component><id>org.mozilla.firefox</id>'
                '<summary>Browser</summary><icon type="cached">../../secret.png</icon>'
                '<icon type="cached">real.png</icon></component>'
                '<component><id>evil.Other</id><summary>Excluded</summary></component></components>')
            result=_read(str(source), source.stat().st_mtime_ns)
            self.assertEqual(set(result), {'org.mozilla.firefox'})
            self.assertEqual(result['org.mozilla.firefox']['icon_name'],str(base/'icons/128x128/real.png'))


if __name__ == '__main__': unittest.main()
