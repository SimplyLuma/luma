#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Native fixture import menu: every image is ready when the menu opens."""
import os
from pathlib import Path
import tempfile
import time
import json
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--width',type=int,default=1180)
parser.parse_args()

ROOT=Path(__file__).resolve().parents[2]
fixture=ROOT/'tests/fixtures/photos-v70.json'
document=json.loads(fixture.read_text())
photos={str(photo['id']):photo for photo in document['photos']}
assert all((fixture.parent/photos[identifier]['image']).is_file()
           for identifier in document['import']['photos']),\
    'Stage the manifest-verified Studio assets before this focused check'

with tempfile.TemporaryDirectory(prefix='photos-import-runtime-') as temporary:
    for kind in ('CACHE','DATA','CONFIG','STATE'):
        os.environ[f'XDG_{kind}_HOME']=str(Path(temporary)/kind.lower())
    os.environ['GSETTINGS_BACKEND']='memory'
    os.environ['LUMA_PHOTOS_FIXTURE']=str(fixture)
    from prairie_apps import photos as p

    app=p.PhotosApplication();app.set_default();assert app.register(None)
    window=p.PhotosWindow(app)
    fixture_bytes=fixture.read_bytes()
    deadline=time.monotonic()+10
    while window.state is None and time.monotonic()<deadline:
        p.GLib.MainContext.default().iteration(False)
        time.sleep(.002)
    assert window.state is not None and window.state.total==22
    assert not window.get_visible()
    window.request_import()
    assert isinstance(window.menu,p.FloatingMenu)

    def descend(widget):
        yield widget
        child=widget.get_first_child()
        while child is not None:
            yield from descend(child)
            child=child.get_next_sibling()

    pictures=[widget for widget in descend(window.menu) if isinstance(widget,p.Gtk.Picture)]
    assert len(pictures)==4 and all(widget.get_paintable() is not None for widget in pictures),\
        'Import menu opened before its four fixture thumbnails were decoded'
    labels=[widget for widget in descend(window.menu) if isinstance(widget,p.Gtk.Label)]
    assert any(widget.get_label()=='+34' for widget in labels) and not any(widget.get_label()=='34' for widget in labels),\
        'Overflow tile must display the signed remaining count'
    more=next(widget for widget in labels if widget.get_label()=='+34')
    assert more.get_pango_context().get_font_description().get_weight()==600,\
        'Overflow tile must use the spec 600 weight'
    assert fixture.read_bytes()==fixture_bytes,'Import preview must not change the fixture'
    window.close()
print('photos-import-runtime: four decoded thumbnails and +34/600 ready at open')
