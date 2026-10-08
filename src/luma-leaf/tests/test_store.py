# SPDX-License-Identifier: Apache-2.0
import os
import pathlib
import tempfile
import unittest

from tests.fixtures import write_epub


class Library(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.environ["XDG_CACHE_HOME"] = self.directory.name
        from luma_leaf import importer, store
        self.store = store
        self.importer = importer
        self.library = store.Library(pathlib.Path(self.directory.name) / "library.sqlite3")
        books = pathlib.Path(self.directory.name) / "Books"
        books.mkdir()
        write_epub(books / "manners.epub")
        self.books = books

    def tearDown(self):
        self.directory.cleanup()

    def test_scan_imports_once_and_keeps_missing_books(self):
        self.assertEqual(self.importer.scan(self.library, self.books), (1, 0))
        self.assertEqual(self.importer.scan(self.library, self.books), (1, 0))
        [book] = self.library.books()
        self.assertEqual(book.shelf, "new")
        self.assertTrue(pathlib.Path(book.cover).exists())
        (self.books / "manners.epub").unlink()
        self.assertEqual(self.importer.scan(self.library, self.books), (0, 1))
        [book] = self.library.books()
        # Never hidden, never deleted: the record and its highlights stay.
        self.assertTrue(book.missing)

    def test_opening_moves_new_to_reading_but_never_finishes(self):
        self.importer.scan(self.library, self.books)
        [book] = self.library.books()
        self.library.open_book(book.id)
        self.assertEqual(self.library.book(book.id).shelf, "reading")
        self.library.set_shelf(book.id, "finished")
        self.library.open_book(book.id)
        self.assertEqual(self.library.book(book.id).shelf, "finished")

    def test_an_older_reading_never_moves_you_back(self):
        self.importer.scan(self.library, self.books)
        [book] = self.library.books()
        self.assertTrue(self.library.set_position(book.id, "epubcfi(/6/4!/4/2/1:0)", device="phone", at=200.0))
        self.assertFalse(self.library.set_position(book.id, "epubcfi(/6/2!/4/2/1:0)", device="laptop", at=100.0))
        self.assertEqual(self.library.book(book.id).position, "epubcfi(/6/4!/4/2/1:0)")

    def test_highlights_notes_and_bookmarks(self):
        self.importer.scan(self.library, self.books)
        [book] = self.library.books()
        highlight = self.library.add_highlight(book.id, "epubcfi(/6/2!/4/2/4,/1:0,/1:40)", "g", "It is a truth.", 0)
        self.library.update_highlight(highlight.id, note="The opening line")
        self.assertEqual(self.library.highlights(book.id)[0].note, "The opening line")
        with self.assertRaises(ValueError):
            self.library.add_highlight(book.id, "x", "purple", "", 0)
        self.library.remove_highlight(highlight.id)
        self.assertEqual(self.library.highlights(book.id), [])
        mark = self.library.add_bookmark(book.id, "epubcfi(/6/2!/4/2/4/1:0)", "It is a truth.", 0)
        self.assertEqual([b.id for b in self.library.bookmarks(book.id)], [mark.id])

    def test_sorting_ignores_articles_and_uses_surnames(self):
        self.importer.scan(self.library, self.books)
        [book] = self.library.books()
        self.assertEqual(book.sort_title, "test of manners")
        self.assertEqual(book.surname, "tester")


if __name__ == "__main__":
    unittest.main()
