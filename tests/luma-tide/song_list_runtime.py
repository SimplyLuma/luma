# SPDX-License-Identifier: Apache-2.0
"""A server-sized list preserves every song without allocating every row."""
import unittest

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gtk

from luma_tide.presentation import Album, Player, SONG_ROW_HEIGHT, Song
from luma_tide.ui_parts import SongList, SongRow


class SongListTests(unittest.TestCase):
    @unittest.skipUnless(Gtk.init_check() and Gdk.Display.get_default(), 'GTK display required')
    def test_reused_row_activates_its_new_subject_without_writing_love_while_binding(self):
        played, loved = [], []
        old = Song('old', 'Old title', 'first', 100, 1)
        new = Song('new', 'New title', 'second', 170, 8, True)
        first = Album('first', 'First album', 'First artist', 2000, (old,))
        second = Album('second', 'Second album', 'Second artist', 2001, (new,))
        row = SongRow(first, old, Player(song_id=old.id),
                      lambda album, song: played.append((album.id, song.id)), loved.append,
                      show_album=True)
        row.set_subject(second, new, Player(song_id=new.id))
        self.assertEqual(loved, [])
        self.assertEqual(row.get_name(), 'td-song-new')
        self.assertEqual(row.play.get_child().get_label(), 'New title')
        self.assertEqual(row.credit.get_label(), 'Second album · Second artist')
        self.assertEqual(row.time.get_label(), '2:50')
        self.assertTrue(row.playing)
        row.play.emit('clicked')
        row.index.get_child_by_name('hover').emit('clicked')
        self.assertEqual(played, [('second', 'new'), ('second', 'new')])
        row.love.set_active(False)
        self.assertEqual(loved, ['new'])

    @unittest.skipUnless(Gtk.init_check() and Gdk.Display.get_default(), 'GTK display required')
    def test_large_library_scrolls_to_the_final_song_with_bounded_widgets(self):
        calls, created, visible = [], [], []
        def make_row(_album, identifier):
            created.append(identifier)
            button = Gtk.Button(label=str(identifier))
            button.subject = identifier
            button.connect('clicked', lambda *_: calls.append(button.subject))
            return button
        def bind_row(button, _album, identifier):
            button.subject = identifier
            button.set_label(str(identifier))
        count = 20000
        adjustment = Gtk.Adjustment(value=0, lower=0, upper=count * SONG_ROW_HEIGHT,
                                   page_size=740)
        listing = SongList([(None, index) for index in range(count)], make_row, bind_row,
                           adjustment, Gtk.Box(), lambda rows: visible.append(rows))
        listing._viewport_changed()
        first_row = listing.rows[0]
        adjustment.set_value(1)
        listing._viewport_changed()
        self.assertIs(listing.rows[0], first_row)
        for offset in range(0, count * SONG_ROW_HEIGHT, 5000):
            adjustment.set_value(offset)
            listing._viewport_changed()
        adjustment.set_value(count * SONG_ROW_HEIGHT - 740)
        listing._viewport_changed()
        self.assertIn(count - 1, listing.rows)
        listing.rows[count - 1].emit('clicked')
        self.assertEqual(calls, [count - 1])
        self.assertLess(len(created), 100)
        self.assertEqual(len(visible[-1]), len(listing.rows))
        first, last = listing.span
        self.assertEqual(listing.leading.props.height_request +
                         (last - first) * SONG_ROW_HEIGHT + listing.trailing.props.height_request,
                         count * SONG_ROW_HEIGHT)

    @unittest.skipUnless(Gtk.init_check() and Gdk.Display.get_default(), 'GTK display required')
    def test_small_fixture_keeps_every_row_and_empty_library_has_none(self):
        for count in (0, 42):
            visible = []
            listing = SongList([(None, index) for index in range(count)],
                               lambda _album, identifier: Gtk.Label(label=str(identifier)),
                               lambda row, _album, identifier: row.set_label(str(identifier)),
                               Gtk.Adjustment(), Gtk.Box(), visible.extend)
            self.assertEqual(len(visible), count)
            self.assertEqual(set(listing.rows), set(range(count)))


if __name__ == '__main__':
    unittest.main()
