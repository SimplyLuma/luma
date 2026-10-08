# SPDX-License-Identifier: Apache-2.0
import os
import pathlib
import tempfile
import unittest

from luma_leaf import cfi, position
from luma_leaf.epub import Epub, EpubError
from tests.fixtures import write_epub


class BookRecord(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = write_epub(pathlib.Path(self.directory.name) / "manners.epub")
        self.book = Epub(str(self.path))

    def tearDown(self):
        self.directory.cleanup()

    def test_metadata_cover_and_identity(self):
        self.assertEqual(self.book.metadata.title, "A Test of Manners")
        self.assertEqual(self.book.metadata.authors, ["Jane Tester"])
        self.assertEqual(self.book.cover()[1], "image/png")
        # The same book has the same id on every device.
        other = write_epub(pathlib.Path(self.directory.name) / "copy.epub")
        self.assertEqual(Epub(str(other)).book_id(), self.book.book_id())

    def test_contents_labels(self):
        stats = position.count(self.book)
        self.assertEqual([(c[0], c[4]) for c in stats.chapters],
                         [("Chapter 1", ""), ("Chapter 2", ""), ("Chapter 3", "The Visit")])
        self.assertEqual(position.chapter_label("Book the First—Recalled to Life"), ("Book the First", "Recalled to Life"))

    def test_a_position_past_the_last_section_is_no_place(self):
        # It used to raise IndexError and take the whole library view with it.
        stats = position.count(self.book)
        self.assertIsNone(position.Locator().place(str(self.path), stats, "epubcfi(/6/80!/4/2/1:0)"))

    def test_every_figure_comes_from_one_position(self):
        stats = position.count(self.book)
        locator = position.Locator()
        # /4 body, /4 second section, /4 its paragraph, /1 text, offset 0
        start_of_two = cfi.make(0, "/4/4[c2]/4/1:0")
        place = locator.place(str(self.path), stats, start_of_two)
        self.assertEqual(place.chapter_label, "Chapter 2")
        self.assertEqual(place.chapter_count, 3)
        self.assertGreater(place.fraction, 0.4)
        self.assertLess(place.fraction, 0.75)
        self.assertEqual(place.chapter_words_left, len("Mr. Bennet was among the earliest of those who waited on Mr. Bingley. "
                                                      "He had always intended to visit him though.".split()))
        later = locator.place(str(self.path), stats, cfi.make(1, "/4/4/1:0"))
        self.assertEqual(later.chapter_label, "Chapter 3")
        self.assertGreater(later.fraction, place.fraction)

    def test_range_cfis_resolve_to_their_start(self):
        stats = position.count(self.book)
        point, end = cfi.parse("epubcfi(/6/2!/4/4[c2]/4,/1:0,/1:20)")
        self.assertEqual(point.spine, 0)
        self.assertIsNotNone(end)
        self.assertEqual(cfi.character_offset(self.book, point),
                         cfi.character_offset(self.book, cfi.parse(cfi.make(0, "/4/4[c2]/4/1:0"))[0]))
        del stats

    def test_not_an_epub(self):
        bad = pathlib.Path(self.directory.name) / "bad.epub"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(EpubError):
            Epub(str(bad))


if __name__ == "__main__":
    unittest.main()
