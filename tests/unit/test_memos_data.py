# SPDX-License-Identifier: Apache-2.0
"""The v70 fixture's pure list, search, transcript and waveform data."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps.memos_data import filter_memos, format_duration, load_fixture  # noqa: E402


class MemosDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selected, cls.memos, cls.deleted = load_fixture(ROOT / "tests/fixtures/memos-v70.json")

    def test_opening_data_is_exactly_the_studio_sample(self):
        self.assertEqual(self.selected, "dry")
        self.assertEqual([memo.id for memo in self.memos], ["dry", "lip", "theo", "playlist", "hum"])
        self.assertEqual(self.deleted, ())
        self.assertEqual(self.memos[0].marks, (64, 171))
        self.assertEqual(len(self.memos[0].wave), 160)
        self.assertEqual(self.memos[-1].transcript, ())

    def test_search_looks_inside_speech_and_returns_the_excerpt(self):
        self.assertEqual([memo.id for memo in filter_memos(self.memos, "all", "notification lip")], ["dry"])
        before, match, after = self.memos[0].search_excerpt("notification lip")
        self.assertEqual(match, "notification lip")
        self.assertTrue(before.startswith("…"))
        self.assertTrue(after.endswith("…"))

    def test_favourites_and_word_times(self):
        self.assertEqual([memo.id for memo in filter_memos(self.memos, "fav")], ["dry"])
        words = self.memos[0].words
        self.assertEqual(words[0].text, "Okay,")
        self.assertLess(words[0].at, words[-1].at)
        self.assertLess(words[-1].at, self.memos[0].duration)
        self.assertEqual(format_duration(252), "4:12")


if __name__ == "__main__":
    unittest.main()
