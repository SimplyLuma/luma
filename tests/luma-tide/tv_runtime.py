# SPDX-License-Identifier: Apache-2.0
"""Tide's TV presentation on a display: remote navigation, playback keys, the pill, Search and idle.

Uses the v70 fixture and a stand-in window; nothing touches the library, audio or the network.

    PYTHONPATH=src/luma-platform/appkit:src/luma-tide python3 tests/luma-tide/tv_runtime.py
"""
import os
import unittest
from dataclasses import replace
from pathlib import Path

from luma_tide.presentation import Player, Playlist

REPO = Path(__file__).resolve().parents[2]


def _display():
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk
        return Gtk.init_check()
    except (ImportError, ValueError):
        return False


@unittest.skipUnless(_display(), 'needs a display')
class TvNavigationTests(unittest.TestCase):
    """Drives the TV presentation with remote keys, over the v70 fixture and a stand-in window."""

    def setUp(self):
        from gi.repository import Gtk
        from luma_tide.fixture import FixtureLibrary
        self.Gtk = Gtk
        os.environ.setdefault('LUMA_TIDE_ARTWORK_ROOT', str(REPO / 'tests/fixtures'))
        source = FixtureLibrary(REPO / 'tests/fixtures/tide-v70.json')
        library = source.load()
        playlist = Playlist('p1', 'Sunday morning', tuple(s.id for s in library.albums[0].songs[:3]))
        library = replace(library, playlists=(playlist,), played=(library.albums[1].id,))
        self.calls = []
        source.play = lambda album, song=None, shuffle=False: self.calls.append(('play', album.id, song and song.id))
        source.play_ids = lambda ids, start=0, shuffle=False: self.calls.append(('ids', tuple(ids), start))
        source.toggle = lambda: self.calls.append(('toggle',))
        source.step = lambda delta: self.calls.append(('step', delta))
        self.window = Gtk.Window(default_width=1920, default_height=1080)
        self.window.source, self.window.library, self.window.player = source, library, Player()
        from luma_tide.ui_tv import TideTV
        self.tv = TideTV(self.window)
        self.window.set_child(self.tv.stage)
        self.window.present()
        self.settle()

    def tearDown(self):
        self.tv.close()
        self.window.destroy()

    def settle(self, seconds=0.25):
        import time
        from gi.repository import GLib
        context = GLib.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)

    def key(self, name, settle=True):
        from gi.repository import Gdk
        self.tv.focus._key_pressed(None, Gdk.keyval_from_name(name), 0, Gdk.ModifierType(0))
        if settle:
            self.settle()

    def test_moving_along_the_bar_opens_each_place(self):
        self.assertEqual(self.tv.view['kind'], 'listen')
        for name, place in (('Right', 'albums'), ('Right', 'playlists'), ('Right', 'sources'), ('Right', 'search'),
                            ('Left', 'sources'), ('Left', 'playlists')):
            self.key(name)
            self.assertEqual(self.tv.view['kind'], place)
        self.assertEqual(self.tv.bar.open, 2)

    def test_album_page_plays_from_a_song_and_back_steps_out(self):
        self.key('Right')                      # Albums
        self.key('Down')                       # the first cover
        self.key('Return')                     # its page
        self.settle(1.0)                       # its rows are laid out a frame or two later
        self.assertEqual(self.tv.view['kind'], 'album')
        self.assertIs(self.tv.focus.focused, self.tv._preferred)   # Play first
        self.key('Down')                       # the first song
        self.key('Return')
        self.assertEqual(self.calls[-1][0], 'ids')
        self.assertEqual(self.calls[-1][2], 0)
        self.assertEqual(self.tv.view['kind'], 'now')
        self.key('Escape')
        self.assertEqual(self.tv.view['kind'], 'album')
        self.key('Escape')
        self.assertEqual(self.tv.view['kind'], 'albums')
        self.key('Escape')                     # to the bar
        self.assertEqual(self.tv.focus.group_of(self.tv.focus.focused), 'places')
        self.assertFalse(self.tv.back())        # then the TV home (the window takes Back)

    def test_space_plays_and_pauses_from_anywhere(self):
        library = self.window.library
        self.window.player = Player(song_id=library.albums[0].songs[0].id, playing=True)
        self.key('space')
        self.assertEqual(self.calls[-1], ('toggle',))

    def test_the_pill_rides_in_the_bar_while_playing_and_opens_now_playing(self):
        library = self.window.library
        previous = self.window.player
        self.window.player = Player(song_id=library.albums[0].songs[0].id, playing=True,
                                    queue=tuple(s.id for s in library.albums[0].songs))
        self.tv.player_changed(self.window.player, previous)
        self.settle()
        self.assertTrue(self.tv.pill.get_visible())
        for _ in range(5):
            self.key('Right')
        self.assertIs(self.tv.focus.focused, self.tv.pill)
        self.key('Return')
        self.assertEqual(self.tv.view['kind'], 'now')
        self.assertFalse(self.tv.pill.get_visible())          # Now playing shows the song itself

    def test_search_finds_albums_artists_and_songs(self):
        self.key('Right'), self.key('Right'), self.key('Right'), self.key('Right')
        album = self.window.library.albums[0]
        self.tv._field.set_value(album.title)
        self.settle()
        self.assertTrue(self.tv._results)
        self.assertTrue(any(getattr(w, 'tide_album', None) is album for w in self.tv._results))

    def test_sources_browse_and_their_menu_confirms_a_removal(self):
        from luma_tide.ui_tv import SourceRow
        self.key('Right'), self.key('Right'), self.key('Right')        # Sources
        self.assertEqual(self.tv.view['kind'], 'sources')
        self.key('Down')
        row = self.tv.focus.focused
        self.assertIsInstance(row, SourceRow)
        source = row.source
        self.assertTrue(self.tv.focus.open_menu())                    # what holding OK opens
        self.settle()
        labels = [c.label for c in self.tv._source_menu(source).groups[0].commands]
        self.assertIn('Refresh', labels)
        self.assertEqual(labels[-1], 'Remove…')
        self.key('Escape')                                             # the menu closes, the row keeps the focus
        self.assertIs(self.tv.focus.focused, row)
        self.tv._confirm_remove(source)
        self.settle()
        self.assertEqual(self.tv.focus.focused.get_parent().__gtype__.name, 'LumaUITvMenu')
        from gi.repository import Gdk
        self.key('Return')                                             # Cancel has the focus: nothing is removed
        self.tv.focus._key_released(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0))
        self.settle()
        self.assertIn(source.id, [s.id for s in self.window.library.sources])
        if source.icon != 'download':
            self.assertIs(self.tv.focus.focused, row)
            self.key('Return')                                         # OK (released) browses the source's albums
            self.tv.focus._key_released(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0))
            self.settle()
            self.assertEqual(self.tv.view['kind'], 'source')
            self.key('Escape')
            self.assertEqual(self.tv.view['kind'], 'sources')

    def test_add_a_server_is_a_form_of_tv_fields(self):
        self.key('Right'), self.key('Right'), self.key('Right')
        self.tv.open_server()
        self.settle()
        self.assertEqual(self.tv.view['kind'], 'server')
        self.assertEqual(len(self.tv._form), 3)
        self.assertFalse(self.tv._form[2].text.get_visibility())     # the password is never shown
        self.key('Escape')
        self.assertEqual(self.tv.view['kind'], 'sources')

    def test_idle_shows_the_record_alone_and_any_key_wakes(self):
        library = self.window.library
        previous = self.window.player
        self.window.player = Player(song_id=library.albums[0].songs[0].id, playing=True)
        self.tv.player_changed(self.window.player, previous)
        self.window.remove_controller(self.tv.idle_hook._motion)   # no stray pointer on a test display
        self.tv.idle_hook.go_idle()
        self.settle()
        self.assertTrue(self.tv.idle)
        self.assertEqual(self.tv.view['kind'], 'now')
        self.assertEqual(self.tv._calm_title.get_label(), library.albums[0].songs[0].title)
        self.key('Down')
        self.assertFalse(self.tv.idle)


if __name__ == '__main__':
    unittest.main()
