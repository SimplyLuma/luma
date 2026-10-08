# SPDX-License-Identifier: Apache-2.0
"""Actual responsive GTK/WebKit previews; untrusted EPUB scripts stay disabled."""
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
import zipfile
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk
from luma_viewer.application import ViewerApplication, ViewerWindow
from luma_viewer.book_preview import open_book


def until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        if predicate():
            return True
        time.sleep(.01)
    return False


def labels(widget):
    result = [widget.get_label()] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child:
        result.extend(labels(child))
        child = child.get_next_sibling()
    return result


def book(path):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr('book.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Preview Test</dc:title></metadata><manifest><item id="one" href="one.xhtml" media-type="application/xhtml+xml"/><item id="two" href="two.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
        for name, text in (('one', 'First section'), ('two', 'Second section')):
            archive.writestr(name + '.xhtml', f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{text}</title><script>document.title="UNSAFE SCRIPT RAN";</script></head><body><h1>{text}</h1><p>A readable EPUB paragraph.</p><a href="file:///etc/passwd">External file</a></body></html>')


class Previews(unittest.TestCase):
    def test_real_book_model_sections_rotation_and_cleanup(self):
        app = ViewerApplication()
        app.set_application_id('org.projectluma.Viewer.DocumentTest')
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        with tempfile.TemporaryDirectory(prefix='viewer-preview-') as directory:
            epub = Path(directory) / 'sample.epub'
            book(epub)
            model = Path(directory) / 'sample.obj'
            model.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 2 3\nf 1 2 4\nf 1 3 4\nf 2 3 4\n')
            hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (epub, model)}
            for width in (360, 500, 1024, 1440):
                with self.subTest(width=width):
                    window = ViewerWindow(app, opening=str(epub))
                    errors = []
                    original_failed = window._render_failed
                    def failed(token, reason):
                        errors.append(reason)
                        return original_failed(token, reason)
                    window._render_failed = failed
                    try:
                        window.set_default_size(width, 740)
                        window.present()
                        self.assertTrue(until(lambda: window.loaded or errors),
                            f'EPUB never rendered: kind={window.facts.kind} token={window.load_token} '
                            f'preview={getattr(window, "_document_preview", None)} labels={labels(window.stage)}')
                        self.assertFalse(errors, errors)
                        preview = window._document_preview
                        self.assertTrue(until(lambda: preview.web.get_title() == 'First section'))
                        self.assertEqual(window.page_count, 2)
                        self.assertTrue(until(lambda: preview.web.get_height() > 0 and window.corner_slot.get_height() > 0))
                        book_ok, book_bounds = preview.web.compute_bounds(window.document_overlay)
                        pill_ok, pill_bounds = window.corner_slot.compute_bounds(window.document_overlay)
                        self.assertTrue(book_ok and pill_ok)
                        self.assertGreaterEqual(book_bounds.origin.y, pill_bounds.origin.y + pill_bounds.size.height, "The book heading must stay below the document controls")
                        self.assertFalse(window.surround.get_visible(), "Book navigation must retain the normal semantic foreground")
                        window._step_page(1)
                        self.assertTrue(until(lambda: preview.web.get_title() == 'Second section'))
                        self.assertEqual(window.page, 1)
                        window._step_page(-1)
                        self.assertTrue(until(lambda: preview.web.get_title() == 'First section'))
                        self.assertFalse(preview.web.get_settings().get_enable_javascript())
                        preview.web.load_uri('file:///etc/passwd')
                        self.assertTrue(until(lambda: not preview.web.is_loading()))
                        self.assertTrue(preview.web.get_uri().startswith('leaf://reader/book/preview/'))
                        window.open_path(str(model))
                        self.assertTrue(until(lambda: window.loaded and window.facts.kind == '3D model'))
                        self.assertTrue(preview.closed)
                        self.assertIsNone(preview.book.zip.fp)
                        mesh = window._document_preview
                        self.assertEqual(len(mesh.faces), 4)
                        self.assertFalse(window.surround.get_visible())
                        old = mesh.faces
                        mesh._begin(None, 0, 0)
                        mesh._drag(None, 60, 30)
                        self.assertNotEqual(mesh.faces, old)
                        mesh.set_zoom(2)
                        self.assertEqual(mesh.zoom, 2)
                        self.assertTrue(until(lambda: mesh.area.get_width() > 0))
                        self.assertGreater(mesh.area.get_height(), 0)
                    finally:
                        window.close()
                        until(lambda: not window.get_visible(), 2)
            for path, digest in hashes.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_book_directory_limits_precede_resource_decompression(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bomb.epub'
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('huge.xhtml', b'0' * (16 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, 'preview limit'):
                open_book(path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
