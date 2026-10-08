"""Search framing moves the camera and keeps multiple matching pins on screen."""

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-maps"))

from luma_maps.camera import camera_for_places, fixture_view_for_places, nearby_cluster  # noqa: E402
from luma_maps.store import Place  # noqa: E402


class MapCameraTests(unittest.TestCase):
    def test_single_result_moves_to_the_result(self):
        place = Place("Kansas City", "City", "", 39.0997, -94.5786)
        self.assertEqual(camera_for_places((place,), 760, 560), (39.0997, -94.5786, 15.0))

    def test_multiple_nearby_results_fit_at_lower_zoom(self):
        places = (Place("McDonald's", "Restaurant", "", 39.08, -94.60),
                  Place("McDonald's", "Restaurant", "", 39.12, -94.56),
                  Place("McDonald's", "Restaurant", "", 39.10, -94.62))
        latitude, longitude, zoom = camera_for_places(places, 760, 560)
        self.assertAlmostEqual(latitude, 39.10, delta=.01)
        self.assertAlmostEqual(longitude, -94.59, delta=.01)
        self.assertLess(zoom, 15)
        self.assertGreater(zoom, 10)

    def test_missing_coordinates_never_move_the_live_map(self):
        self.assertIsNone(camera_for_places((Place("No position", "Place", "", None, None),), 760, 560))

    def test_distant_names_do_not_force_world_zoom(self):
        places = (Place("Paris", "City", "France", 48.8566, 2.3522),
                  Place("Paris", "City", "Texas", 33.6609, -95.5555))
        self.assertEqual(nearby_cluster(places), places[:1])
        self.assertEqual(camera_for_places(nearby_cluster(places), 760, 560)[2], 15)

    def test_fixture_result_fit_is_independent_of_gtk_size(self):
        class Point:
            def __init__(self, x, y):
                self.map_x, self.map_y = x, y

        x, y, zoom = fixture_view_for_places((Point(100, 200), Point(500, 400)), 760, 560)
        self.assertEqual((x, y), (300, 300))
        self.assertGreater(zoom, .4)


if __name__ == "__main__":
    unittest.main()
