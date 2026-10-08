# SPDX-License-Identifier: Apache-2.0
"""The conform fixture stays in memory and carries Studio's book data."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from luma_leaf.fixture import FixtureLibrary
from luma_leaf.epub import Epub
from luma_leaf.position import Locator, Stats


FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/leaf-v70.json"
if not FIXTURE.exists():
    FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/leaf-v70.json"


class FixtureTests(unittest.TestCase):
    def test_v70_books_and_chapters_are_present(self):
        library = FixtureLibrary(FIXTURE)
        self.assertEqual(len(library.books()), 10)
        self.assertEqual(library.book("totc").title, "A Tale of Two Cities")
        self.assertEqual(library.sample("totc")["p"], 0.12)
        self.assertEqual(len(library.document["chapters"]["totc"]), 2)
        book = library.book("totc")
        epub = Epub(book.path)
        self.assertEqual(epub.metadata.title, book.title)
        self.assertEqual(len(epub.toc()), 15)
        self.assertIn("It was the best of times", epub.text(1).text)
        self.assertIsNotNone(Locator().place(book.path, Stats.from_json(book.stats), book.position))
        self.assertEqual([book.title for book in library.books() if book.shelf == "reading"],
                         ["A Tale of Two Cities", "Moby-Dick", "Alice’s Adventures in Wonderland"])

    def test_changes_cannot_reach_a_real_store(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = directory
            try:
                library = FixtureLibrary(FIXTURE)
                library.set_shelf("totc", "finished")
                library.set_pref("page", {"theme": "night"})
                self.assertEqual(library.book("totc").shelf, "finished")
                self.assertFalse(list(Path(directory).rglob("*")))
                fresh = FixtureLibrary(FIXTURE)
                self.assertEqual(fresh.book("totc").shelf, "reading")
            finally:
                if old is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old


if __name__ == "__main__":
    unittest.main()
