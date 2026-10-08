# SPDX-License-Identifier: Apache-2.0
import unittest
from pathlib import Path

from luma_leaf.fixture import FixtureLibrary
from luma_leaf.library_data import counts, matches, on_shelf, ordered


FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/leaf-v70.json"
if not FIXTURE.exists():
    FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/leaf-v70.json"


class LibraryDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.books = FixtureLibrary(FIXTURE).books()

    def test_v70_shelf_counts(self):
        self.assertEqual(counts(self.books), {"reading": 3, "want": 5, "finished": 2, "all": 10, "hl": 0})
        self.assertTrue(on_shelf(next(b for b in self.books if b.id == "gatsby"), "want"))

    def test_search_all_words_and_sort(self):
        self.assertEqual([b.id for b in self.books if matches(b, "Charles cities")], ["totc"])
        self.assertEqual([b.id for b in self.books if matches(b, "not present")], [])
        self.assertEqual(ordered(self.books, "title")[0].id, "alice")
        self.assertEqual(ordered(self.books, "author")[0].id, "pp")


if __name__ == "__main__":
    unittest.main()
