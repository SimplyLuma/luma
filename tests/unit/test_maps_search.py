"""The live map's fractional camera zoom must be valid in Photon requests."""

import io
import pathlib
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-maps"))

from luma_maps.providers import SearchProvider  # noqa: E402
from luma_maps.search import PlaceSearch  # noqa: E402


class PhotonRequestTests(unittest.TestCase):
    def test_fractional_viewport_zoom_is_sent_as_an_integer(self):
        provider = SearchProvider("https://photon.example/api", "", "", 350, 1, False)
        search = PlaceSearch(provider, "LumaMaps/test")
        requests = []

        def respond(request, timeout):
            requests.append(request)
            return io.BytesIO(b'{"features": []}')

        with patch("luma_maps.search.urllib.request.urlopen", side_effect=respond):
            outcome = search._fetch("710", (45.5152, -122.6784, 12.4))

        self.assertFalse(outcome.error)
        self.assertTrue(outcome.reached_network)
        self.assertEqual(len(requests), 1)
        query = parse_qs(urlsplit(requests[0].full_url).query)
        self.assertEqual(query["zoom"], ["12"])
        self.assertEqual(query["q"], ["710"])
        self.assertEqual(query["lat"], ["45.5152"])
        self.assertEqual(query["lon"], ["-122.6784"])


if __name__ == "__main__":
    unittest.main()
