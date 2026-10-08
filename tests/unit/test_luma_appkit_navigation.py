"""An application's trail: where back, forward and the breadcrumb go."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-platform/appkit"))

from luma_appkit.navigation import NavigationTrail, Place  # noqa: E402

ALBUMS = Place("albums", "Albums")
ALBUM = Place("album", "Abbey Road", ("Abbey Road", "The Beatles"))
ARTIST = Place("songs", "The Beatles", ("artist", "The Beatles"))


class Trail(unittest.TestCase):
    def setUp(self) -> None:
        self.moves: list[tuple[str, str]] = []
        self.trail = NavigationTrail(ALBUMS, lambda place, direction: self.moves.append((place.title, direction)))

    def test_opening_and_going_back_returns_the_way_you_came(self) -> None:
        self.trail.open(ALBUM)
        self.trail.open(ARTIST)
        self.assertEqual([p.title for p in self.trail.places], ["Albums", "Abbey Road", "The Beatles"])
        self.assertTrue(self.trail.back())
        self.assertEqual(self.trail.current, ALBUM)
        self.assertTrue(self.trail.forward())
        self.assertEqual(self.trail.current, ARTIST)
        self.assertEqual([d for _t, d in self.moves], ["forward", "forward", "back", "forward"])

    def test_nowhere_to_go_is_reported_so_the_input_passes_through(self) -> None:
        self.assertFalse(self.trail.back())
        self.assertFalse(self.trail.forward())
        self.assertFalse(self.trail.can_go_back)
        self.assertEqual(self.moves, [])

    def test_a_crumb_returns_there_and_leaves_the_rest_for_forward(self) -> None:
        self.trail.open(ALBUM)
        self.trail.open(ARTIST)
        self.assertTrue(self.trail.go_to(0))
        self.assertEqual(self.trail.places, (ALBUMS,))
        self.assertFalse(self.trail.go_to(0))
        self.trail.forward()
        self.trail.forward()
        self.assertEqual(self.trail.current, ARTIST)

    def test_a_top_level_place_starts_again_and_opening_clears_forward(self) -> None:
        self.trail.open(ALBUM)
        self.trail.back()
        self.trail.open(ARTIST)
        self.assertFalse(self.trail.can_go_forward)
        self.trail.start(Place("songs", "Songs"))
        self.assertEqual(len(self.trail.places), 1)
        self.assertFalse(self.trail.can_go_back)
        count = len(self.moves)
        self.trail.start(Place("songs", "Songs"))
        self.trail.open(Place("songs", "Songs"))
        self.assertEqual(len(self.moves), count, "choosing where you already are is not a step")

    def test_tide_carries_an_identical_copy_for_older_platforms(self) -> None:
        kit = ROOT / "src/luma-platform/appkit/luma_appkit/navigation.py"
        tide = ROOT / "src/luma-tide/luma_tide/_navigation.py"
        self.assertEqual(tide.read_text(), kit.read_text())


if __name__ == "__main__":
    unittest.main()
