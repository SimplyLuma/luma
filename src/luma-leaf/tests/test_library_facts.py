# SPDX-License-Identifier: Apache-2.0
"""What the shelf says about a book: its name, its author, and whether it is being read."""
import os
import pathlib
import tempfile
import unittest

from luma_leaf import store
from tests.fixtures import write_epub


class Names(unittest.TestCase):
    def test_authors_are_shown_in_natural_order(self):
        self.assertEqual(store.display_name("Isaacson, Walter"), "Walter Isaacson")
        self.assertEqual(store.display_name("Le Guin, Ursula K."), "Ursula K. Le Guin")
        self.assertEqual(store.display_name("Tolkien, J. R. R."), "J. R. R. Tolkien")
        # Already natural, or a comma that is part of the name: kept as written.
        self.assertEqual(store.display_name("Herman Melville"), "Herman Melville")
        self.assertEqual(store.display_name("Martin Luther King, Jr."), "Martin Luther King, Jr.")
        self.assertEqual(store.display_name("Dumas, Alexandre, père"), "Dumas, Alexandre, père")

    def test_the_file_as_form_still_sorts_by_family_name(self):
        self.assertEqual(store.surname("Isaacson, Walter"), "isaacson")
        self.assertEqual(store.surname("Walter Isaacson"), "isaacson")
        self.assertEqual(store.surname("Martin Luther King, Jr."), "king")

    def test_the_shelf_shows_the_main_title(self):
        self.assertEqual(store.main_title("Moby Dick; Or, The Whale"), "Moby Dick")
        self.assertEqual(store.main_title("Frankenstein; or, the modern prometheus"), "Frankenstein")
        self.assertEqual(store.main_title("Twelfth Night, or What You Will"), "Twelfth Night")
        self.assertEqual(store.main_title("Steve Jobs"), "Steve Jobs")
        self.assertEqual(store.main_title("A Tale of Two Cities"), "A Tale of Two Cities")


class Shelf(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.environ["XDG_CACHE_HOME"] = self.directory.name
        from luma_leaf import importer
        from luma_leaf.position import Locator
        self.library = store.Library(pathlib.Path(self.directory.name) / "library.sqlite3")
        books = pathlib.Path(self.directory.name) / "Books"
        books.mkdir()
        write_epub(books / "whale.epub", title="Moby Dick; Or, The Whale")
        importer.scan(self.library, books)
        [self.book] = self.library.books()
        self.locator = Locator()

    def tearDown(self):
        self.directory.cleanup()

    def facts(self):
        from luma_leaf.library_view import BookFacts
        return BookFacts(self.library.book(self.book.id), self.library, self.locator)

    def test_the_stored_title_is_kept_whole(self):
        book = self.library.book(self.book.id)
        self.assertEqual(book.title, "Moby Dick; Or, The Whale")
        self.assertEqual(book.short_title, "Moby Dick")

    def test_a_book_on_reading_now_is_never_new(self):
        # Opened, but no position was ever saved: the record Nick's library
        # held for Moby Dick, counted under Reading now and labelled New.
        self.library.open_book(self.book.id)
        facts = self.facts()
        self.assertEqual(facts.book.shelf, "reading")
        self.assertIsNone(facts.book.position)
        self.assertTrue(facts.in_progress)
        self.assertEqual(facts.status(), "Not started")
        self.assertEqual(facts.short_status(), "0%")

    def test_a_new_book_is_new(self):
        facts = self.facts()
        self.assertFalse(facts.in_progress)
        self.assertEqual(facts.short_status(), "New")


if __name__ == "__main__":
    unittest.main()
