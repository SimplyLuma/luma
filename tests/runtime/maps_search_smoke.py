"""Headless live-map smoke: result pins and the camera use the same places.

Run with a private XDG_DATA_HOME and XDG_CACHE_HOME under Xvfb. No real store
or tile provider is touched by this test; it supplies search results directly.
"""

import os
import pathlib
import sys

if not os.environ.get("XDG_DATA_HOME") or not os.environ.get("XDG_CACHE_HOME"):
    raise SystemExit("private XDG_DATA_HOME and XDG_CACHE_HOME are required")
if os.environ.get("LUMA_MAPS_FIXTURE"):
    raise SystemExit("live-map smoke must not use the fixture renderer")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src/luma-maps"), str(ROOT / "src/luma-platform/appkit")]

from luma_maps.application import MapsApplication, MapsWindow  # noqa: E402
from luma_maps.search import SearchOutcome  # noqa: E402
from luma_maps.store import Place  # noqa: E402
from luma_appkit import ShareSheet  # noqa: E402


app = MapsApplication()
assert app.register(None)
MapsWindow._apply_tile_provider = lambda self, provider: None
window = MapsWindow(app)
places = (Place("McDonald's", "Restaurant", "Portland", 45.5232, -122.6900),
          Place("McDonald's", "Restaurant", "Portland", 45.5341, -122.6604),
          Place("McDonald's", "Restaurant", "Portland", 45.5457, -122.7061))
window._search_result(SearchOutcome(places, False))
assert window.results == places
assert abs(window.viewport.get_latitude() - 45.534) < .02
assert abs(window.viewport.get_longitude() + 122.683) < .02
assert window.viewport.get_zoom_level() < 15
assert len(window.markers.get_markers()) == 3
window._share(places[0], window.place_slot)
assert ShareSheet._open is not None
assert ShareSheet._open.document.kind == "link"
assert ShareSheet._open._choose("copy-link") == "Map link copied"
ShareSheet._open.close()
window.close()
print("live search camera, 3 pins and share panel: PASS")
