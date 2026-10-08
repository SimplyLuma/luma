# SPDX-License-Identifier: Apache-2.0
"""File cards request the metadata they read from real files."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio
from luma_appkit import FileCard, install_appkit


class FileMetadata(unittest.TestCase):
    def test_regular_file_type_and_size(self):
        install_appkit()
        with tempfile.NamedTemporaryFile(suffix='.txt') as source:
            source.write(b'fixture'); source.flush()
            card = FileCard(source.name)
            info = card._query()
            self.assertEqual(info.get_file_type(), Gio.FileType.REGULAR)
            self.assertEqual(card.size, 7)


if __name__ == '__main__':
    unittest.main()
