"""The staged/source kit resolves Newsreader rather than a silent fallback."""
import unittest
import subprocess
import sys
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Pango, PangoCairo
from luma_appkit.document_fonts import ensure_document_fonts

class DocumentFontTests(unittest.TestCase):
    def test_bundled_regular_and_italic_are_available(self):
        # Fontconfig and GTK style providers are process globals. Exercise this
        # native integration with a fresh map instead of inheriting other UI fixtures.
        if __name__ != '__main__':
            result = subprocess.run([sys.executable, __file__], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        Gtk.init()
        self.assertTrue(ensure_document_fonts())
        context = PangoCairo.FontMap.get_default().create_context()
        for description in ('Newsreader 17', 'Newsreader Italic 17'):
            font = context.load_font(Pango.FontDescription.from_string(description))
            self.assertEqual(font.describe().get_family(), 'Newsreader')
            if 'Italic' in description:
                self.assertEqual(font.describe().get_style(), Pango.Style.ITALIC)
        self.assertTrue(ensure_document_fonts())

if __name__ == '__main__': unittest.main()
