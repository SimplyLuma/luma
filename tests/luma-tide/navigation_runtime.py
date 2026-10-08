#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The v70 trail supports album back/forward and artist-release navigation.

v70's artist tile opens a release directly; search uses the Songs view rather
than a nested grouped-search page. Exercise those real port contracts without
inventing the retired artist page or breadcrumb strip.
"""
import tempfile,time
from pathlib import Path
import gi
gi.require_version('Gtk','4.0');gi.require_version('Adw','1')
from gi.repository import Adw,Gio,GLib,Gtk
from luma_appkit import install_appkit
from luma_tide.application import TideWindow
from luma_tide.model import LibraryStore,MediaMetadata
from luma_tide.playback import PlaybackController
from test_playback import FakeEngine
from lumaui_runtime import count, key, named, test_application, window_id, xdo

def settle(test):
    until=time.monotonic()+10
    while time.monotonic()<until:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        if test():return
        time.sleep(.01)
    raise AssertionError('Tide did not settle')

with test_application('org.projectluma.TideNavigationTest') as (app, root):
    source=app.store.add_source('Test music','local-folder',Path(root),local=True)
    for n in (1,2):
        app.store.upsert_copy(source.id,MediaMetadata(uri=(Path(root)/f'{n}.flac').as_uri(),content_digest=str(n)*64,
            title=f'Song {n}',artist='Artist',album='Record',duration_ns=60_000_000_000))
    window=TideWindow(app);window.set_title('TideNavigationRuntime');window.set_size_request(1180,740);window.present()
    settle(lambda:window.get_width()>0 and count(window)==2)
    xdo('windowsize', '--sync', window_id(window), '1180', '740')
    settle(lambda: window.get_width() >= 1100 and not window.phone)
    assert window.view=='albums' and window.trail.current.title=='Albums'
    assert not window.trail.can_go_back
    tile=named(window,'td-album-grid').get_child_at_index(0).get_child()
    tile.emit('clicked')
    assert window.view=='album' and window.trail.current.title=='Record'
    assert window.trail.can_go_back
    assert window.go_back() and window.view=='albums' and window.trail.current.title=='Albums'
    assert window.go_forward() and window.trail.current.title=='Record'
    key(window,None,'alt+Left')
    settle(lambda:window.view=='albums')
    assert not window.go_back(),'nothing behind the top level'
    key(window,None,'alt+Right')
    settle(lambda:window.view=='album' and window.trail.current.title=='Record')
    window.show_view('artists')
    artist=named(window,'td-artist-Artist')
    assert artist is not None and window.library.artists[0][0]=='Artist'
    artist.emit('clicked')
    assert window.view=='album' and window.trail.current.title=='Record'
    assert window.go_back() and window.view=='artists'
    artist=named(window,'td-artist-Artist');artist.emit('clicked')
    window.open_search();window.search_item.entry.set_text('Song 2')
    settle(lambda:window.query=='Song 2' and len(window._rows)==1)
    assert window.view=='songs' and window.trail.current.title=='Results'
    assert window._rows[0].song.title=='Song 2'
    window.search_item.entry.set_text('')
    settle(lambda:window.query=='' and len(window._rows)==2)
    assert window.view=='songs' and window.trail.current.title=='Songs'
    window.close_search()
    assert window.view=='album'
    assert window.go_back() and window.view=='artists'
    assert named(window,'td-artist-Artist') is not None
print('PASS: album tile, back/forward and Alt keys, artist release, search trail and browse restoration')
