# SPDX-License-Identifier: Apache-2.0
"""A tile is never blank, and never the same anonymous glyph as its neighbour."""

import unittest

from luma_depot.providers import monogram


class Monogram(unittest.TestCase):
    def test_two_words_give_their_initials(self):
        self.assertEqual(monogram("Google Chrome"), "GC")
        self.assertEqual(monogram("Tor Browser"), "TB")
        self.assertEqual(monogram("Android Studio"), "AS")

    def test_an_acronym_is_already_the_short_form(self):
        """Cutting VLC down to VL makes it unreadable."""
        self.assertEqual(monogram("VLC"), "VLC")
        self.assertEqual(monogram("GIMP"), "GIMP")

    def test_a_long_capitalised_name_is_not_treated_as_an_acronym(self):
        self.assertEqual(monogram("REAPER"), "R")

    def test_a_name_that_capitalises_inside_itself_shows_both_parts(self):
        self.assertEqual(monogram("qBittorrent"), "QB")
        self.assertEqual(monogram("NordVPN"), "NV")
        self.assertEqual(monogram("LibreOffice"), "LO")
        self.assertEqual(monogram("1Password"), "1P")

    def test_one_plain_word_gives_one_letter(self):
        self.assertEqual(monogram("Firefox"), "F")
        self.assertEqual(monogram("Blender"), "B")
        self.assertEqual(monogram("Claude"), "C")

    def test_punctuation_and_emptiness_do_not_raise(self):
        self.assertEqual(monogram(""), "?")
        self.assertEqual(monogram("   "), "?")
        self.assertEqual(monogram("!!!"), "?")
        self.assertEqual(monogram("Foo-Bar"), "FB")

    def test_every_catalogued_application_gets_something_to_draw(self):
        import json, pathlib as pl
        root = pl.Path(__file__).resolve()
        catalog = next(
            candidate for candidate in (
                root.parents[1] / "data/depot-catalog.json",
                root.parents[2] / "luma-installer/data/depot-catalog.json",
            ) if candidate.is_file()
        )
        names = [a["name"] for a in json.loads(catalog.read_text())["applications"]]
        self.assertTrue(names)
        for name in names:
            self.assertTrue(monogram(name).strip(), name)


if __name__ == "__main__":
    unittest.main()
