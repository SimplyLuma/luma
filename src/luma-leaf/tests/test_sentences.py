# SPDX-License-Identifier: Apache-2.0
import pathlib
import re
import unittest

from luma_leaf import sentences

READER = pathlib.Path(__file__).resolve().parents[1] / "data/reader/reader.js"


class SentenceRule(unittest.TestCase):
    def test_titles_never_end_a_sentence(self):
        text = "“I do not believe Mrs. Long will do any such thing. She has two nieces.” Mr. Bingley came. Dr. Jones left."
        self.assertEqual(sentences.split(text), [
            "“I do not believe Mrs. Long will do any such thing.",
            "She has two nieces.”",
            "Mr. Bingley came.",
            "Dr. Jones left.",
        ])

    def test_closing_quotes_stay_with_their_sentence(self):
        self.assertEqual(sentences.split("“Is it?” she asked. “Yes!” He left."),
                         ["“Is it?” she asked.", "“Yes!”", "He left."])

    def test_lowercase_after_a_stop_does_not_split(self):
        self.assertEqual(sentences.split("It was 3 p.m. and late."), ["It was 3 p.m. and late."])

    def test_page_uses_the_same_rule(self):
        # The page and the library must cut sentences in the same places.
        source = READER.read_text()
        py_titles = "|".join(sentences.ABBREVIATIONS)
        self.assertIn(f"(?:{py_titles})", source)
        self.assertIn(r'/[.!?]+[”’")\]]*(?=\s+[“‘"(]?[A-Z0-9])/g', source)

    def test_time_figures(self):
        self.assertEqual(sentences.minutes(0, 238), 0)
        self.assertEqual(sentences.minutes(10, 238), 1)
        self.assertEqual(sentences.hours_and_minutes(121_000, 160), "12 h 36 min")
        self.assertEqual(sentences.hours_and_minutes(800, 160), "5 min")


if __name__ == "__main__":
    unittest.main()
