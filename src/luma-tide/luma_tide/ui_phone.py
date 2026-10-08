# SPDX-License-Identifier: Apache-2.0
"""Tide on a phone (the window under 560), as v71 draws it (`tidePhBar`, `tideNow`).

One bar, like every other app's: its top row is the mini player (what's
playing, play/pause and next, a thin neutral progress line); under a hairline,
the section you're in (Listen now, Albums, Artists, Songs as a place picker),
Search and Volume. Volume grows a slider and where it plays; Share grows people
and tiles. Now Playing is a full-screen page and hides the bar.

The bar, its panels, tiles, search and share are LumaUI's (ActionCenter and its
panel parts). The mini player and the Now Playing page are Tide's own surfaces.
"""
from __future__ import annotations

import os

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk, Pango
from luma_appkit import (
    ActionCenter, BarAction, BarSearch, BarTile, BarTiles, MediaTransport, MediaMiniPlayer, PanelHeading, PanelRow, SharePanel, SPACER,
    icons,
)
from luma_appkit.action_center import make_control
from luma_appkit.media_transport import MediaGlyph
from .presentation import duration_text, next_queue
from .ui_parts import AlbumCover, PlayingBackdrop, QueueRow, action, named, text

#: The places the section picker offers (v71 SEC): key, glyph, name.
SECTIONS = (('home', 'house', 'Listen now'), ('albums', 'disc-3', 'Albums'),
            ('artists', 'mic-vocal', 'Artists'), ('songs', 'music', 'Songs'))

#: Where it plays, on a phone (v71 tidePhBar 'vol'): the computer's speaker is this phone's.
PHONE_OUTPUT_NAMES = {'This computer': ('This phone', 'smartphone', 'Speaker')}


class MiniPlayerRow(MediaMiniPlayer):
    """Bind the shared media row to Tide's existing playback and swipe actions."""

    def __init__(self, window):
        super().__init__(on_open=window.open_now_playing,
                         on_play=lambda: window.source.toggle(),
                         on_next=lambda: window.source.step(1))
        self.window = window
        self.set_name('td-mini')
        self.open.set_name('td-mini-open')
        self.play.set_name('td-mini-play')
        self.next.set_name('td-mini-next')
        swipe = Gtk.GestureSwipe()
        swipe.connect('swipe', self._swiped)
        self.open.add_controller(swipe)
        self._album_id = None

    def _swiped(self, _gesture, velocity_x, velocity_y):
        if abs(velocity_x) > abs(velocity_y) and abs(velocity_x) > 300:
            self.window.source.step(1 if velocity_x < 0 else -1)

    def update(self, library, player):
        found = library.find_song(player.song_id)
        self.set_visible(found is not None)
        if not found:
            return
        album, song = found
        if self._album_id != album.id:
            self.set_artwork(AlbumCover(album, 44, plain=True))
            self._album_id = album.id
        super().update(song.title, album.artist, playing=player.playing,
                       fraction=player.position / song.duration if song.duration else 0)


class PhoneBar:
    """Tide's one bar on a phone, built from the kit's ActionCenter."""

    def __init__(self, window, host):
        self.window = window
        self.center = named(ActionCenter(), 'td-phone-bar')
        self.center.attach(host)
        self.search_item = None
        self.shown = False

    def section(self):
        w = self.window
        if w.view.startswith('search'):
            return 'search'
        if w.view == 'album':
            root = w.trail.places[0].view
            return root if root in ('albums', 'artists', 'songs', 'home') else 'albums'
        return w.view if w.view in ('home', 'albums', 'artists', 'songs') else 'home'

    def show(self):
        w = self.window
        current = self.section()
        if current == 'search':
            current = w.phone_root
        key, glyph, name = next((s for s in SECTIONS if s[0] == current), SECTIONS[0])
        places = BarAction(glyph, name, dropdown=True, key='lib', tooltip=name,
                           panel=lambda: self._places_panel(current))
        searching = w.view.startswith('search')
        self.search_item = BarSearch('Artists, albums and songs', label='Search',
                                              on_change=w.phone_search_changed, collapsed=True, text=w.query if searching else '',
                                              on_close=w.phone_search_closed)
        volume = BarAction('volume-2' if w.player.output in ('This computer', '') else 'headphones',
                           tooltip='Volume and output', key='vol', panel=self._volume_panel)
        w.mini = MiniPlayerRow(w)
        self.center.show_bar([places, SPACER, self.search_item, volume], head=w.mini, fill=True, head_inset=False)
        names = iter(('td-phone-section', None, 'td-phone-search', 'td-phone-volume'))
        child = self.center.bar_row.get_first_child()
        while child:
            name = next(names)
            if name:
                child.set_name(name)
            child = child.get_next_sibling()
        self.shown = True
        self.update()
        if not searching and w.source.fixture and os.environ.get('LUMA_TIDE_PHONE_SEARCH') \
                and not getattr(w, '_fixture_phone_search', False):
            w._fixture_phone_search = True
            searching = True
        if searching:
            # v71: while searching, the bar's row is the field (the mini player stays above it).
            self.center.open_search(self.search_item)

    def hide(self):
        if self.shown:
            self.center.hide_bar()
            self.shown = False

    def update(self):
        mini = getattr(self.window, 'mini', None)
        if mini is not None:
            mini.update(self.window.library, self.window.player)

    def open_search(self):
        if self.search_item is not None and not self.center.searching:
            self.center.open_search(self.search_item)

    def share(self, what):
        self.center.grow('share', SharePanel(people=self.window.share_people(), heading=f'Send {what} to',
                                             on_choice=self.window.shared))

    def _places_panel(self, current):
        tiles = BarTiles([BarTile(glyph, name, lambda k=key: self.window.show_section(k), on=key == current,
                                  name=name) for key, glyph, name in SECTIONS], columns=4)
        for (key, _glyph, _name), button in zip(SECTIONS, tiles.buttons):
            button.set_name('td-tab-' + key)
        return tiles

    def _volume_panel(self):
        w = self.window
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class('td-volume-panel')
        box.append(PanelHeading('Volume'))
        transport = MediaTransport('deck', volume=w.player.volume / 100,
                                   on_volume=lambda value: w.source.set_volume(value * 100))
        line = volume_row(transport, 'td-volume-row')
        box.append(line)
        box._transport = transport
        box.append(PanelHeading('Playing on'))
        for name, icon, subtitle in w.player.outputs:
            label, glyph, sub = PHONE_OUTPUT_NAMES.get(name, (name, icon, subtitle))
            row = PanelRow(label, icon=glyph, subtitle=sub, selected=name == w.player.output,
                           on_activate=lambda value=name: w.choose_output(value))
            row.set_name('td-output-' + name.lower().replace(' ', '-'))
            box.append(row)
        return box


def volume_row(transport, css):
    """v71 `.tvolr` / `.tnvol`: the quiet speaker, the kit's volume range, the loud speaker."""
    row = transport.volume_control
    first = row.get_first_child()
    if first is not None and not isinstance(first, Gtk.Scale):
        row.remove(first)
    row.prepend(MediaGlyph('volume-1', 18))
    row.append(MediaGlyph('volume-2', 18))
    for child in (row.get_first_child(), row.get_last_child()):
        child.set_valign(Gtk.Align.CENTER)
    scale = row.get_first_child().get_next_sibling()
    scale.set_hexpand(True)
    row.set_hexpand(True)
    row.add_css_class(css)
    return row


class NowPlayingPhone(Gtk.Overlay):
    """Now Playing on a phone (v71 `.now.tnowph`): the sleeve large, the song, the scrubber, the
    transport, the volume, and a row for Up next and where it plays. Up next shrinks the sleeve to a
    header and lists what's coming."""

    def __init__(self, window, album, song, queue_open=False):
        super().__init__(hexpand=True, vexpand=True)
        self.set_name('td-now-phone')
        self.add_css_class('td-now-phone')
        player = window.player
        self.set_child(PlayingBackdrop(album))
        # v71 `.now.tnowph`: 52 down (under the clock), 24 a side, 44 up.
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=52, margin_start=24, margin_end=24,
                       margin_bottom=44)
        page.add_css_class('td-now-page')
        self.add_overlay(page)
        head = Gtk.Box(spacing=8)
        head.add_css_class('td-now-head')
        head.append(action('chevron-down', 'Close now playing', window.close_now_playing, name='td-now-close'))
        source = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        source.append(text('Playing from', 'caption', xalign=.5))
        source.append(text(album.title, 'body', weight=650, xalign=.5, ellipsize=Pango.EllipsizeMode.END,
                           max_width_chars=1))
        head.append(source)
        head.append(action('share-2', 'Share', lambda: window.share_playing(), name='td-now-share'))
        page.append(head)
        if queue_open:
            top = Gtk.Box(spacing=14, margin_top=12)
            top.add_css_class('td-now-queue-head')
            top.append(AlbumCover(album, 64, plain=True))
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
            words.append(text(song.title, 'body', weight=650, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
            words.append(text(album.artist, 'meta', ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
            top.append(words)
            page.append(top)
            page.append(text('Up next', 'label', margin_top=14, margin_bottom=4))
            listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for identifier in next_queue(player)[1:]:
                found = window.library.find_song(identifier)
                if found:
                    listing.append(QueueRow(found[0], found[1], False, window.play_song))
            scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=listing)
            page.append(scroll)
        else:
            art = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True,
                          valign=Gtk.Align.CENTER, halign=Gtk.Align.FILL)
            art.add_css_class('td-now-art')
            cover = AlbumCover(album, 360)
            art.append(cover)
            page.append(art)
            line = Gtk.Box(spacing=10)
            line.add_css_class('td-now-song')
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            words.append(text(song.title, 'now-title', ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
            artist = Gtk.Button(halign=Gtk.Align.START)
            artist.add_css_class('td-link')
            artist.set_child(text(album.artist, 'hero-title', weight=400, ellipsize=Pango.EllipsizeMode.END))
            artist.connect('clicked', lambda *_: window.show_section('artists'))
            words.append(artist)
            line.append(words)
            line.append(action('heart', 'Love', window.love_playing, name='td-now-love', active=song.loved))
            page.append(line)
        self.transport = MediaTransport('now', duration=song.duration, position=player.position,
            playing=player.playing, shuffle=player.shuffle, repeat=player.repeat, volume=player.volume / 100,
            on_play=lambda _playing: window.source.toggle(), on_seek=window.source.seek,
            on_step=window.source.step, on_shuffle=lambda _on: window.source.shuffle(),
            on_repeat=lambda _on: window.source.repeat(),
            on_volume=lambda value: window.source.set_volume(value * 100))
        self.transport.set_name('td-now-transport')
        for key, name in (('play', 'td-play-pause'), ('shuffle', 'td-shuffle'), ('previous', 'td-previous'),
                          ('next', 'td-next'), ('repeat', 'td-repeat')):
            self.transport.key(key).set_name(name)
        self.transport.add_css_class('td-now-transport')
        page.append(self.transport)
        self.transport.volume_control.set_name('td-now-volume')
        foot = Gtk.Box(margin_top=16)
        foot.add_css_class('td-now-foot')
        foot.append(action('list-music', 'Up next', window.toggle_now_queue, name='td-now-queue', active=queue_open))
        label, glyph, _sub = PHONE_OUTPUT_NAMES.get(player.output, (player.output, 'headphones', ''))
        device = Gtk.Button(hexpand=True, halign=Gtk.Align.CENTER)
        device.set_name('td-output')
        device.add_css_class('td-now-device')
        device_line = Gtk.Box(spacing=8)
        device_line.append(icons.image(glyph))
        device_line.append(Gtk.Label(label=label or 'This phone'))
        device.set_child(device_line)
        device.connect('clicked', lambda *_: window.phone_output())
        foot.append(device)
        foot.append(action('ellipsis', 'More', window.now_more, name='td-now-more'))
        page.append(foot)

    def update(self, player, song):
        self.transport.set_position(player.position)
        self.transport.set_playing(player.playing)
        self.transport.set_shuffle(player.shuffle)
        self.transport.set_repeat(player.repeat)
