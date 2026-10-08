#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Empty Leaf paints without WebKit, speech connection or an MPRIS reader."""
import os
from pathlib import Path
import sys
import tempfile
import time

work = tempfile.TemporaryDirectory(prefix='leaf-library-startup-')
for name in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME'):
    path = Path(work.name) / name
    path.mkdir()
    os.environ[name] = str(path)
os.environ.pop('LUMA_LEAF_FIXTURE', None)
os.environ['LUMA_LEAF_PREVIEW'] = '1'
os.environ['GSETTINGS_BACKEND'] = 'memory'
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib
from luma_leaf.application import LeafApplication

app = LeafApplication()
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
started = time.monotonic()
failure = []

def check():
    try:
        if time.monotonic() - started > 20:
            raise AssertionError('library did not map and finish local initial scan')
        if app.window is None or not app.window.get_mapped() or not app.window.library_view._has_rendered:
            return GLib.SOURCE_CONTINUE
        assert app.window._reader is None, 'library constructed WebKit reader'
        assert 'luma_leaf.reader_view' not in sys.modules, 'library loaded WebKit reader module'
        assert app._speaker is None, 'library opened speech service'
        assert app.mpris is None, 'empty library published reader MPRIS'
        view = app.window.library_view
        assert view.empty.get_mapped() and not view.shelf_scroll.get_visible()
        assert view.empty.heading.get_label() == 'No books yet'
        assert not app.window.sidebar_toggle.get_visible()
        assert view.foot.get_margin_start() == 0 and view.foot.get_margin_end() == 0
        # These commands are actual import/connection callbacks, not dead labels.
        commands = app.window._commands(app)
        assert commands.get('leaf.add-books').execute == app.add_books
        assert commands.get('leaf.add-library').execute == app.add_library
        for group in commands.groups:
            for command in group.commands:
                assert command.icon, command.id
        print(f'PASS actual empty library mapped in {time.monotonic()-started:.3f}s; reader/speech deferred', flush=True)
    except BaseException as error:
        failure.append(error)
    app.quit()
    return GLib.SOURCE_REMOVE

GLib.timeout_add(25, check)
status = app.run([])
if failure:
    raise failure[0]
raise SystemExit(status)
