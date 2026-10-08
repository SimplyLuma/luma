# SPDX-License-Identifier: Apache-2.0
import http.server
import os
import pathlib
import tempfile
import threading
import unittest

from tests.fixtures import write_epub

ROOT = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><id>calibre-all</id><title>calibre</title>
<entry><title>By Newest</title><id>newest</id><link rel="subsection" type="application/atom+xml;profile=opds-catalog" href="/opds/newest"/></entry>
</feed>"""
NEWEST = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><id>newest</id><title>By Newest</title>
<link rel="next" type="application/atom+xml" href="/opds/newest?page=2"/>
<entry><title>A Test of Manners</title><id>urn:leaf:test-book</id><author><name>Jane Tester</name></author>
<link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="/get/epub/1"/>
<link rel="http://opds-spec.org/image/thumbnail" type="image/png" href="/get/cover/1"/></entry>
</feed>"""
PAGE_TWO = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><id>newest-2</id><title>By Newest</title>
<entry><title>A Comic</title><id>urn:uuid:comic</id><link rel="http://opds-spec.org/acquisition" type="application/x-cbz" href="/get/cbz/2"/></entry>
</feed>"""


class OPDS(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.environ["XDG_CACHE_HOME"] = self.directory.name
        epub = write_epub(pathlib.Path(self.directory.name) / "served.epub").read_bytes()
        pages = {"/opds": ROOT, "/opds/newest": NEWEST, "/opds/newest?page=2": PAGE_TWO}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in pages:
                    body, kind = pages[self.path].encode(), "application/atom+xml"
                elif self.path == "/get/epub/1":
                    body, kind = epub, "application/epub+zip"
                elif self.path == "/get/cover/1":
                    body, kind = b"\x89PNG", "image/png"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        from luma_leaf import opds, store
        self.opds = opds
        self.library = store.Library(pathlib.Path(self.directory.name) / "library.sqlite3")
        self.source = self.library.add_source("calibre", "Home library", f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.directory.cleanup()

    def test_catalogue_lists_epubs_through_navigation_and_pages(self):
        books = self.opds.browse(self.source)
        self.assertEqual([(b.title, b.authors) for b in books], [("A Test of Manners", ["Jane Tester"])])

    def test_server_books_stream_and_are_the_same_book_once_read(self):
        self.assertEqual(self.opds.refresh(self.library, self.source), 1)
        [listed] = self.library.books()
        self.assertIsNone(listed.path)
        self.assertEqual(listed.source, self.source.id)
        opened = self.opds.stream(self.library, listed.id)
        # The catalogue's id and the EPUB's own identifier agree, so the
        # streamed book is the same record, not a second one.
        self.assertEqual(opened, listed.id)
        [book] = self.library.books()
        self.assertTrue(pathlib.Path(book.path).exists())
        self.assertEqual(book.source, self.source.id, "a streamed book is still the server's")

    def test_an_unreachable_server_dims_and_never_hides(self):
        self.opds.refresh(self.library, self.source)
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(self.opds.SourceError):
            self.opds.refresh(self.library, self.source)
        self.assertFalse(self.library.sources()[0].reachable)
        self.assertEqual(len(self.library.books()), 1)


if __name__ == "__main__":
    unittest.main()
