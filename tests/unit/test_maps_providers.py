"""Provider URLs must use placeholders understood by libshumate."""

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-maps"))

from luma_maps.providers import load  # noqa: E402


class MapsProviderTests(unittest.TestCase):
    def test_shipped_raster_tile_url_contains_tile_coordinates(self):
        with tempfile.TemporaryDirectory() as config_home, \
                patch.dict("os.environ", {"XDG_CONFIG_HOME": config_home,
                                          "LUMA_MAPS_PROVIDERS": str(ROOT / "src/luma-maps/data/providers.toml")}):
            provider = load().tile("osm-mapnik")
        self.assertIsNotNone(provider)
        template = provider.url_template
        self.assertEqual(urlsplit(template).fragment, "")
        self.assertEqual(template.format(z=12, x=970, y=1565),
                         "https://tile.openstreetmap.org/12/970/1565.png")


if __name__ == "__main__":
    unittest.main()
