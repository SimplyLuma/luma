"""The conform fixture must stay deterministic and independent of the user's Maps data."""

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-maps"))

from luma_maps.fixture import MapsFixture, place_matches  # noqa: E402
from luma_maps.model import MapState, fixture_state, route_progress  # noqa: E402


class MapsFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MapsFixture(ROOT / "tests/fixtures/maps-v70.json")

    def test_v70_scene_and_order(self):
        self.assertEqual([p.id for p in self.fixture.places],
                         ["studio", "loft", "home", "duende", "door", "books", "park"])
        self.assertEqual([p.id for p in self.fixture.saved], ["studio", "loft", "home"])
        self.assertEqual([p.id for p in self.fixture.recents], ["duende", "door", "books"])
        self.assertEqual(len(self.fixture.route_points), 8)
        self.assertEqual(len(self.fixture.steps), 6)
        self.assertEqual(self.fixture.opening["view"], {"x": 1100, "y": 740, "zoom": 0.78})

    def test_search_uses_v70_name_and_category(self):
        self.assertEqual([p.id for p in self.fixture.find("COFFEE")], ["door"])
        self.assertEqual([p.id for p in self.fixture.find("park")], ["park"])
        self.assertEqual(self.fixture.find("no such place"), ())
        self.assertFalse(place_matches(self.fixture.places[0], ""))

    def test_fixture_changes_stay_in_memory(self):
        place = self.fixture.by_id["duende"]
        self.fixture.save(place)
        self.assertTrue(self.fixture.is_saved(place))
        self.fixture.unsave(place)
        self.assertFalse(self.fixture.is_saved(place))
        self.fixture.clear_recents()
        self.assertEqual(self.fixture.recents, [])
        fresh = MapsFixture(ROOT / "tests/fixtures/maps-v70.json")
        self.assertEqual([p.id for p in fresh.recents], ["duende", "door", "books"])

    def test_state_transitions_and_capture_modes(self):
        state = MapState().select("loft").show_directions().set_mode("transit")
        self.assertEqual(state, fixture_state("directions:loft:transit", set(self.fixture.by_id)))
        self.assertEqual(state.go().end().place, "loft")
        self.assertFalse(state.back_to_place().directions)
        self.assertEqual(fixture_state("layer:transit", set()).layer, "transit")
        with self.assertRaises(ValueError):
            fixture_state("place:unknown", set(self.fixture.by_id))
        with self.assertRaises(ValueError):
            MapState().go()

    def test_route_progress_reaches_each_turn_and_destination(self):
        points = self.fixture.route_points
        self.assertEqual((route_progress(points, 0).x, route_progress(points, 0).y), points[0])
        first = route_progress(points, 80)
        self.assertEqual((first.x, first.y), points[1])
        self.assertEqual(first.segment, 0)
        final = route_progress(points, 100000)
        self.assertEqual((final.x, final.y, final.remaining), (*points[-1], 0))
        with self.assertRaises(ValueError):
            route_progress(((0, 0),), 0)


if __name__ == "__main__":
    unittest.main()
