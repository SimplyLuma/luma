"""Mapped private-fixture regression for compact Maps sidebar access."""
import os
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

_private = tempfile.TemporaryDirectory(prefix="maps-compact-")
for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    os.environ[key] = str(Path(_private.name) / key)
os.environ.update(GSETTINGS_BACKEND="memory", LUMA_FORM_FACTOR="desktop")
assert os.environ.get("LUMA_MAPS_FIXTURE"), "Provide the private Maps fixture path"
# The bundled v70 data has no guides. Add one explicitly private guide so
# this gate really reaches guide navigation without changing the reference.
source = Path(os.environ['LUMA_MAPS_FIXTURE'])
data = json.loads(source.read_text())
place = data['places'][0]
assets = source.with_suffix('')
assert (assets / place['image']).is_file(), 'Actual Maps fixture artwork missing'
data['guides'] = [{'id': 'private-native-guide', 'title': 'Private native guide',
                  'by': 'Test', 'initials': 'TE', 'image': place['image'],
                  'places': [place['id']], 'note': 'Private guide navigation fixture'}]
fixture = Path(_private.name) / 'maps.json'
fixture.write_text(json.dumps(data))
fixture.with_suffix('').symlink_to(assets)
os.environ['LUMA_MAPS_FIXTURE'] = str(fixture)
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib
from luma_maps.application import MapsApplication, MapsWindow
from luma_maps.guides import GuideCard


def settle(seconds=.5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


def descendants(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


app = MapsApplication()
app.set_application_id("org.projectluma.Maps.CompactSidebarTest")
assert app.register(None)
with patch("urllib.request.urlopen", side_effect=AssertionError("No network")):
    window = MapsWindow(app)
    window.set_default_size(720, 874)
    window.present()
    settle(1)
    assert not window.sidebar_toggle.get_mapped()
    window._search_query("coffee")
    assert window.commands.invoke('maps.sidebar')
    settle()
    assert window.sidebar.get_mapped()
    row = next(n for n in descendants(window.sidebar) if isinstance(n, Gtk.ListBoxRow)
               and getattr(n, "place", None))
    assert row.get_mapped()
    place = row.place
    window.sidebar.list.emit("row-activated", row)
    settle()
    assert window.selected == place
    assert not window.sidebar.get_mapped()
    window._back_to_list()
    window._search_query("")
    assert window.commands.invoke('maps.sidebar')
    settle()
    card = next(n for n in descendants(window.sidebar) if isinstance(n, GuideCard))
    assert card.get_mapped()
    card.emit("clicked")
    settle()
    assert window.sidebar.get_mapped()
    assert any(isinstance(n, Gtk.Label) and n.get_mapped() and
               n.get_label() == card.guide.note for n in descendants(window.sidebar))
    window.sidebar_toggle.toggle()
    for width in (1180, 402, 720):
        window.set_default_size(width, 874)
        settle()
        assert window.get_surface().get_width() == width, (width, window.get_surface().get_width(), window.get_width())
        assert not window.sidebar_toggle.get_mapped()
        if width == 1180:
            assert window.sidebar.get_mapped()
    window.close()
    settle()
app.quit()
print("PASS compact search selection, guide content and sidebar width transitions", flush=True)
