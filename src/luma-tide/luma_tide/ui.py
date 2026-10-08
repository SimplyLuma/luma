# SPDX-License-Identifier: Apache-2.0
"""Tide's LumaUI window: one room, three library tabs and details drawers."""
from __future__ import annotations

import concurrent.futures
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from luma_appkit import (
    AddRow, AppWindow, BarSearch, Command, CommandGroup, CommandRegistry, ContentLitHeader,
    CornerPill, DestructiveDialog, DetailsPane, DetailsRow, IconOnlyButton, Island,
    LayerHost, Menu, ModeSwitch, NavigationTrailBar, PageHeader, PersonAvatar, ScrollView, StackedButton,
    StackedButtons, TableHeader, TextButton, TextField, Toast, ToastHost, FileRequest, ShareSheet,
    ShareSubject, ShareTarget, ShareResult, installed_targets, ask_for_file, apply_type, icons,
)
from luma_appkit.navigation import NavigationTrail, Place, bind_navigation_input
from luma_appkit.action_bubble import FloatingMenu
from luma_appkit.rows_menu import RichMenuItem
from luma_appkit.structure_table import Column
from luma_appkit.lumaui import set_css_class as lumaui_set_class
from .presentation import (
    PHONE_ALBUM_ROW_HEIGHT, PHONE_SONG_ROW_HEIGHT, SONG_ROW_HEIGHT, Library, Player, more_albums, next_queue,
    quiet_pick, search_library, songs_matching,
)
from .deferred import DeferredRemovals
from .ui_parts import AlbumCover, AlbumTile, CoverSlot, Deck, FeatureCard, QueueRow, SongList, SongRow, SourceFace, PlayingBackdrop, action, clear, named, text
from .ui_phone import SECTIONS, NowPlayingPhone, PhoneBar


class TideWindow(AppWindow):
    def __init__(self, application, source=None):
        if source is None:
            from .live import LiveLibrary
            source = LiveLibrary(application)
        self.app, self.source = application, source
        self.controller = getattr(application, 'controller', None)
        self.library = Library()
        self.player = getattr(source, 'player', Player())
        self.view = getattr(source, 'view', 'albums')
        self.album_id = getattr(source, 'album_id', None)
        self.query, self.sort_key, self.descending = '', 'album', False
        self._share_subject = None
        self.pane_kind, self.source_id, self.editing = None, None, False
        self.now_tab = None
        # v71 phone (the window under 560): one bar, a Listen now place, Now Playing full screen.
        self.phone = False
        self.phone_root = 'albums'
        self.now_queue = False
        self.mini = None
        self._generation = 0
        self._closed = False
        self._rows = []
        self._library_pad = None
        self._tile_grid = None
        self._tile_items = ()
        self._tile_next = 0
        self._tile_factory = None
        self._tile_fill_source = 0
        self._search_source = 0
        self._search_origin = None
        self._status_toast = None
        self._loader = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='tide-view-read')
        self._load_future = None
        self._removals = DeferredRemovals(GLib.timeout_add, GLib.source_remove, self.source.remove_source)
        commands = CommandRegistry((CommandGroup('', (
            Command('tide.add-folder', 'Add music folder…', self._add_folder, 'folder-plus', shortcut=('Ctrl', 'O')),
            Command('tide.search', 'Search', self.open_search, 'search', shortcut=('Ctrl', 'F')),
            Command('tide.quit', 'Quit Tide', self._quit, 'log-out', shortcut=('Ctrl', 'Q')),
        )),))
        super().__init__(application=application, app_id=application.get_application_id(), title='Tide',
                         icon_name='org.projectluma.Tide', commands=commands,
                         default_width=1180, default_height=740, minimum_width=360, minimum_height=420)
        # The media wash paints beneath the phone status area; scroll content
        # retains the shared status inset, while Now Playing pads itself.
        self.set_phone_bleed(True)
        self.set_name('td-window')
        self.add_css_class('tide-window')
        self.trail = NavigationTrail(Place(self.view, 'Tide', self.album_id), on_change=self._navigated)
        bind_navigation_input(self, self.trail.back, self.trail.forward)
        self.pane_trail = NavigationTrail(Place('sources', 'Sources'), on_change=self._pane_navigated)
        self.root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        self.island = named(Island(), 'td-island')
        self.island.set_hexpand(True)
        self.layer = Gtk.Overlay(vexpand=True, hexpand=True)
        self.backdrop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.light = named(ContentLitHeader(tone='media'), 'td-light')
        self.backdrop.append(self.light)
        # The decorative 520px light wash must not become the window's
        # minimum content height and push short-window controls below it.
        self.layer.set_child(Gtk.Box(hexpand=True, vexpand=True))
        self.layer.add_overlay(self.backdrop)
        self.layer.set_measure_overlay(self.backdrop, False)
        self.page = named(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  margin_top=self.status_inset), 'td-body')
        self.scroll = ScrollView(self.page)
        self.scroll.set_hexpand(True)
        self.scroll.set_vexpand(True)
        adjustment = self.scroll.get_vadjustment()
        adjustment.connect('value-changed', self._queue_tile_fill)
        adjustment.connect('changed', self._queue_tile_fill)
        self.layer.add_overlay(self.scroll)
        self.modes = ModeSwitch([('albums', 'Albums', None), ('artists', 'Artists', None), ('songs', 'Songs', None)],
                                on_change=self.show_view, current='albums', label='Library', labels_only=True, ellipsize=True)
        for key, button in self.modes.buttons.items():
            button.set_name('td-view-' + key)
            button.connect('clicked', lambda _button, mode=key: self.show_view(mode) if mode == 'albums' and self.view == 'album' else None)
        self.search_item = BarSearch('Search', label='Search music', span='narrow',
                                     on_change=self._search_changed, on_close=self.close_search)
        self.corner = named(CornerPill(modes=self.modes, search=self.search_item, actions=[
            ('search', 'Search', self.toggle_search), ('library', 'Sources', self.toggle_sources)]), 'td-corner')
        self.corner.controls['actions.0'].set_name('td-search-open')
        self.corner.controls['actions.1'].set_name('td-sources-open')
        self.layer.add_overlay(self.corner)
        self.search = self.corner.controls['search']
        self.search.set_name('td-search')
        self.search_item.entry.set_name('td-search-entry')
        self.search.set_visible(False)
        self.now_layer = Gtk.Overlay(hexpand=True, vexpand=True, visible=False)
        self.layer.remove_overlay(self.corner)
        self.layer.add_overlay(self.now_layer)
        self.layer.add_overlay(self.corner)
        self.deck = Deck(self)
        self.island.append(self.layer)
        self.host = ToastHost(self.island)
        self.root.append(self.host)
        self.pane = named(DetailsPane('Sources', on_close=self._pane_closed), 'td-pane-slot')
        self.pane.sheet.set_name('td-pane')
        self.root.append(self.pane)
        self.set_body(self.root)
        # The app-owned deck grows from the window frame below the island.
        self.deck_host = LayerHost.window_host(self)
        self.deck.set_halign(Gtk.Align.START)
        self.deck_host.add_overlay(self.deck)
        self.deck_host.set_measure_overlay(self.deck, False)
        self.host.track_bar(self.deck)
        self.phone_bar = PhoneBar(self, self.host)
        self._breakpoints()
        self.tier_watch.connect('tier-changed', lambda _watch, tier: self._tier_changed(tier))
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._key)
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(keys)
        self.connect('close-request', lambda *_: self._on_close())
        self.page.append(text('Loading music…', 'caption', margin_start=30, margin_top=72))
        self._unsubscribe = self.source.subscribe(lambda player: GLib.idle_add(self._player_changed, player))
        self.refresh_library()

    def _breakpoints(self):
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 559px'))
        phone.add_setter(self.root, 'margin-start', 8)
        phone.add_setter(self.deck.previous, 'visible', False)
        self.add_breakpoint(phone)
        self.layer.add_tick_callback(self._size_deck)

    def _tier_changed(self, tier):
        phone = tier == 'phone'
        if phone == self.phone:
            return
        self.phone = phone
        if phone and self.search.get_visible():
            # The corner pill's search gives way to the bar's: same words, same results.
            query = self.search_item.text
            self._hide_search()
            self.query = query
            if query.strip():
                self.view = 'search'
                self.trail.replace(Place('search', 'Results'))
        if not phone and self.view == 'home':
            self.view = 'albums'
        self._render()

    def _size_deck(self, *_):
        width = self.layer.get_width()
        # A drawer is detached from this row; reserve the gutter only for
        # an attached side sheet, including during desktop/phone resizing.
        spacing = 8 if self.pane.shown and self.pane.sheet.get_parent() is self.pane else 0
        if self.root.get_spacing() != spacing:
            self.root.set_spacing(spacing)
        if self.phone:
            if self.deck.get_visible():
                self.deck.set_visible(False)
            return not self._closed
        if self._library_pad is not None:
            margin = 16 if self.get_width() <= 559 else 28
            for edge in ('start', 'end'):
                if getattr(self._library_pad, 'get_margin_' + edge)() != margin:
                    getattr(self._library_pad, 'set_margin_' + edge)(margin)
        if width > 0:
            viewport = self.get_width()
            compact = viewport <= 720
            title = getattr(self, '_album_title_label', None)
            if title is not None and title.get_root() is self:
                role = 'album-hero-title-compact' if compact else 'album-hero-title'
                if not title.has_css_class('lumaui-t-' + role):
                    apply_type(title, role)
            hero = getattr(self, '_album_hero', None)
            if hero is not None and hero.get_root() is self:
                self._fit_album_hero(hero, width <= 720)
            self.deck.set_compact_layout(compact)
            for widget in (self.deck.scrub, self.deck.previous, self.deck.love):
                widget.set_visible(not compact)
            for widget in (self.deck.end, self.deck.shuffle, self.deck.repeat):
                widget.set_visible(viewport > 900)
            self.deck.scrub.set_size_request(300 if not compact else -1, -1)
            self.deck.transport.set_volume_compact(viewport <= 1100)
            self.deck.update_width(viewport)
            # Keep the reference's 880 px deck at its 1180 px viewport, but
            # give a wide library room for title, transport and output controls.
            wanted = min(880, max(250, width - 96))
            ok, bounds = self.layer.compute_bounds(self.deck_host)
            if ok:
                left = round(bounds.get_x() + (width - wanted - 36) / 2)
                if self.deck.get_margin_start() != left:
                    self.deck.set_margin_start(left)
            if self.deck.props.width_request != wanted + 36:
                self.deck.set_size_request(wanted + 36, 104 if compact else 128)
                self.deck.body.props.height_request = 84 if compact else 92
            self.deck.sleeve.set_margin_start(36)
            cover = self.deck.sleeve.get_child()
            cover_size = 78 if compact else 108
            if isinstance(cover, CoverSlot) and cover.side != cover_size:
                cover.side = cover_size
                cover.queue_resize()
        return not self._closed

    def refresh_library(self):
        if self._closed:
            return
        self._generation += 1
        generation = self._generation
        # A sync can publish many batches while one read is running. Retain
        # that read and only the latest pending one, rather than queueing a
        # full-library query for every obsolete batch.
        if self._load_future is not None:
            self._load_future.cancel()
        future = self._loader.submit(self.source.load)
        self._load_future = future
        def finished(done):
            try:
                library, error = done.result(), ''
            except Exception as failure:
                library, error = None, str(failure)
            GLib.idle_add(self._loaded, generation, library, error)
        future.add_done_callback(finished)

    def _loaded(self, generation, library, error):
        if self._closed or generation != self._generation:
            return False
        if error:
            clear(self.page)
            self.page.append(text(error, 'body', wrap=True, margin_start=30, margin_top=72))
            return False
        self.library = library
        if self.source.fixture and os.environ.get('LUMA_TIDE_NOW') and not getattr(self, '_fixture_now_started', False):
            self.now_tab = os.environ['LUMA_TIDE_NOW']
            self._fixture_now_started = True
        if self.source.fixture and os.environ.get('LUMA_TIDE_QUERY') and not getattr(self, '_fixture_query_started', False):
            self._fixture_query_started = True
            if self.phone:
                self.query = os.environ['LUMA_TIDE_QUERY']
                self._search_origin = self.trail.current
                self.view = 'search'
                self.trail.open(Place('search', 'Results'))
            else:
                self.open_search()
                self.search_item.entry.set_text(os.environ['LUMA_TIDE_QUERY'])
        if self._removals.pending:
            from dataclasses import replace
            self.library = replace(library, sources=tuple(s for s in library.sources if s.id not in self._removals.pending))
        if self.album_id and not any(a.id == self.album_id for a in library.albums):
            self.view, self.album_id = 'albums', None
        self._render()
        self.deck.update(self.library, self.player)
        self.phone_bar.update()
        self._fill_pane()
        return False

    def library_changed(self, **_kwargs):
        self.refresh_library()

    def _populate_sources(self):
        self.refresh_library()

    def refresh_offline_state(self):
        self.refresh_library()

    def set_status(self, value):
        if self._status_toast is not None:
            self._status_toast.dismiss()
            self._status_toast = None
        if value:
            self._status_toast = Toast.show(self.layer, value, busy=True)

    def show_view(self, view):
        if view == 'sources':
            self.open_sources()
            return
        if view not in ('albums', 'artists', 'songs', 'home'):
            return
        searching = self.search.get_visible()
        if searching:
            self._hide_search()
        title = 'Listen now' if view == 'home' else view.title()
        (self.trail.start if searching else self.trail.open)(Place(view, title))

    def open_album(self, identifier, artist=None):
        if artist is not None:
            identifier = next((a.id for a in self.library.albums if a.title == identifier and a.artist == artist), None)
        if identifier:
            album = self.library.album(identifier)
            self.trail.open(Place('album', album.title, album.id))

    def open_playing_album(self):
        subject = self.library.find_song(self.player.song_id)
        if subject:
            album, _song = subject
            self.open_album(album.id)

    def _navigated(self, place, _direction):
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        self.now_tab = None
        self.view = place.view
        if place.view == 'album':
            self.album_id = place.subject
        self._render()
        self.scroll.get_vadjustment().set_value(0)

    def go_back(self):
        return self.trail.back()

    def go_forward(self):
        return self.trail.forward()

    def _render(self):
        if self.now_tab and not self.library.find_song(self.player.song_id):
            self.now_tab = None
        self._library_pad = None
        if self._tile_fill_source:
            GLib.source_remove(self._tile_fill_source)
            self._tile_fill_source = 0
        self._tile_grid = None
        self._tile_items = ()
        self._tile_next = 0
        self._tile_factory = None
        clear(self.page)
        self._rows = []
        self.light.set_size_request(-1, -1 if self.phone else 520)
        if self.view in ('albums', 'artists', 'songs', 'album'):
            self.modes.set_current('albums' if self.view == 'album' else self.view)
        self.now_layer.set_visible(bool(self.now_tab))
        from luma_appkit.lumaui import media_context
        media_context(self.now_layer, bool(self.now_tab))
        self.scroll.set_visible(not self.now_tab)
        # v71 phone: the corner pill and the deck give way to the one bar (hidden under Now Playing).
        self.corner.set_visible(not self.phone and not self.now_tab)
        self.deck.set_visible(not self.phone and self.library.find_song(self.player.song_id) is not None)
        lumaui_set_class(self, 'td-phone', self.phone)
        if self.phone and not self.now_tab:
            if not self.phone_bar.center.searching:
                self.phone_bar.show()
        else:
            self.phone_bar.hide()
        self.deck.sleeve.set_visible(not self.now_tab)
        self.deck.update_width(self.get_width())
        if self.now_tab:
            self._now_page()
        elif self.view == 'album' and self.album_id:
            # Allocation can change tier before the asynchronous first library arrives.
            album = next((album for album in self.library.albums if album.id == self.album_id), None)
            if album is not None:
                self._album_page(album)
        elif self.view.startswith('search'):
            self._search_page()
        elif self.view == 'home':
            self._home_page()
        else:
            self._library_page()
        if not self.phone:
            # The deck floats over the room's foot; on a phone the kit's safe area clears the bar.
            self.page.append(Gtk.Box(height_request=130))
        # Rebuilding results removes a focused song button. Keep the active
        # search keyboard reachable instead of leaving the window unfocused.
        focus = self.get_focus()
        if self.search.get_visible() and (focus is None or not focus.get_mapped()):
            self.search_item.entry.grab_focus()

    # ── v71 phone ─────────────────────────────────────────────────────

    def show_section(self, key):
        """A place from the bar's section picker: a root, so the trail starts again (v71 data-ttabroot)."""
        if key not in ('home', 'albums', 'artists', 'songs'):
            return
        self.phone_root = key
        self.now_tab = None
        if self.phone_bar.center.searching:
            self.phone_bar.center.close_search()
        self.query = ''
        self.trail.start(Place(key, dict((k, n) for k, _g, n in SECTIONS)[key]))

    def phone_search_changed(self, query):
        """The bar's search field (v71 `.tphq`): the page becomes the results as you type."""
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        if not self.view.startswith('search'):
            if not query.strip():
                return
            self._search_origin = self.trail.current
            self.query = query
            self.trail.open(Place('search', 'Results'))
            return
        self.query = query
        if not self._closed:
            self._search_source = GLib.timeout_add(120, self._apply_phone_search)

    def _apply_phone_search(self):
        self._search_source = 0
        if not self._closed and self.view.startswith('search'):
            self._render()
            self.scroll.get_vadjustment().set_value(0)
        return GLib.SOURCE_REMOVE

    def phone_search_closed(self):
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        self.query = ''
        if self.view.startswith('search'):
            origin = self._search_origin or Place(self.phone_root, dict((k, n) for k, _g, n in SECTIONS)[self.phone_root])
            self._search_origin = None
            self.trail.start(origin)
        else:
            self.phone_bar.show()

    def share_people(self):
        # Tide keeps no contacts of its own; the sample shows v71's recent five.
        if not self.source.fixture:
            return ()
        from luma_appkit.content_contact import Person
        return tuple(Person(name, hue=hue) for name, hue in (
            ('Priya Raman', 330), ('Nora Feld', 45), ('Sam Kaur', 200), ('Theo Marsh', 350), ('Dad', 150)))

    def shared(self, choice, value):
        if choice == 'send-to':
            return f'Sent to {value.name.split()[0]}'
        if choice == 'copy-link':
            album = self._share_subject
            if album is not None:
                self.get_clipboard().set(f'{album.title} · {album.artist}')
            return 'Link copied'
        return {'messages': 'Opening Messages', 'mail': 'Opening a new email',
                'nearby': 'Looking for devices nearby'}.get(choice, '')

    def share_playing(self):
        found = self.library.find_song(self.player.song_id)
        if not found:
            return
        if not self.source.fixture:
            self.close_now_playing()
            self._share_audio(found[1].title, (found[1].id,))
            return
        self._share_subject = found[0]
        self.close_now_playing()
        self.phone_bar.share(found[1].title)

    def choose_output(self, name):
        self.source.output(name)
        Toast.show(self.layer, 'Playing on ' + ('this phone' if name == 'This computer' else name))

    def phone_output(self):
        self.close_now_playing()
        self.phone_bar.center.grow('vol', self.phone_bar._volume_panel())

    def toggle_now_queue(self):
        self.now_queue = not self.now_queue
        self._render()

    def now_more(self):
        found = self.library.find_song(self.player.song_id)
        if found:
            self._album_menu(found[0], self.get_focus() or self.now_layer)

    def open_now_playing(self, tab='next'):
        if self.player.song_id:
            self.now_tab = tab
            self._render()

    def close_now_playing(self):
        self.now_tab = None
        self.now_queue = False
        self._render()

    def _quit(self):
        # Ordinary window close can keep a playback session alive. The Quit
        # command must use the application's existing stop-and-quit action.
        if self.app.has_action('quit'):
            self.app.activate_action('quit', None)
        else:
            self.app.quit()  # Isolated presentation fixture has no player.

    def _now_page(self):
        def now_text(value, role='body', **props):
            label = text(value, role, **props)
            label.add_css_class('td-now-copy')
            return label
        album, song = self.library.song(self.player.song_id)
        old = getattr(self, '_now_content', None)
        if old:
            self.now_layer.remove_overlay(old)
            self._now_content = None
        if self.phone:
            # v71 Now Playing on a phone: full screen, the bar hidden.
            self.now_layer.set_child(NowPlayingPhone(self, album, song, queue_open=self.now_queue))
            return
        self.now_layer.set_child(PlayingBackdrop(album))
        room = named(Gtk.Box(spacing=40, margin_top=48, margin_start=44,
                             margin_end=44, margin_bottom=130), 'td-now')
        room.add_css_class('td-now')
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, margin_start=12, valign=Gtk.Align.CENTER)
        cover = AlbumCover(album, 340)
        cover.set_halign(Gtk.Align.START)
        stage.append(cover)
        stage.append(text(song.title, 'now-display', wrap=True, margin_top=22, margin_bottom=4))
        stage.append(text(f'{album.artist} · {album.title}' + (f' · {album.year}' if album.year else ''),
                          'media-credit', wrap=True))
        close = action('chevron-down', 'Close now playing', self.close_now_playing, name='td-now-close')
        for child in self._children(stage):
            if isinstance(child, Gtk.Label):
                child.add_css_class('td-now-copy')
        room.append(stage)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=360, hexpand=False)
        tabs = ModeSwitch([('next', 'Up next'), ('about', 'About')], current=self.now_tab,
                          label='Now playing', labels_only=True, on_change=self.open_now_playing)
        tabs.set_halign(Gtk.Align.START)
        tabs.set_hexpand(False)
        for key, button in tabs.buttons.items():
            button.set_name('td-now-' + key)
        side.append(tabs)
        if self.now_tab == 'next':
            for index, identifier in enumerate(next_queue(self.player)):
                a, queued = self.library.song(identifier)
                if index == 1:
                    side.append(now_text('Next from ' + album.title, 'label', margin_top=14, margin_bottom=4))
                side.append(QueueRow(a, queued, index == 0, self.play_song))
        else:
            if album.note:
                side.append(now_text(album.note, 'reading', wrap=True, margin_top=20, margin_bottom=24))
            facts = Gtk.Grid(column_spacing=16, row_spacing=12)
            side.append(facts)
            def fact(label, value):
                index = len(list(self._children(facts))) // 2
                facts.attach(now_text(label, 'caption'), 0, index, 1, 1)
                facts.attach(now_text(value, 'body', wrap=True), 1, index, 1, 1)
            if album.year:
                fact('Released', str(album.year))
            if album.label:
                fact('Label', album.label)
            # Real libraries supply their own source names; no fictional server.
            if self.source.fixture:
                fact('In your library', 'Navidrome, on your home server')
            elif song.library_sources:
                fact('In your library', ', '.join(song.library_sources))
        from .ui_parts import ColumnSlot
        side_slot = named(ColumnSlot(side, 360), 'td-now-side')
        room.append(side_slot)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 720px'))
        phone.add_setter(side_slot, 'visible', False)
        phone.add_setter(cover, 'width-request', 1)
        phone.add_setter(cover, 'height-request', 1)
        phone.add_setter(cover, 'halign', Gtk.Align.FILL)
        phone.add_setter(room, 'margin-top', 60)
        phone.add_setter(room, 'margin-start', 22)
        phone.add_setter(room, 'margin-end', 22)
        box = Adw.BreakpointBin(child=ScrollView(room), width_request=1, height_request=1)
        box.add_breakpoint(phone)
        overlay = Gtk.Overlay(child=box, hexpand=True, vexpand=True)
        close.set_halign(Gtk.Align.START)
        close.set_valign(Gtk.Align.START)
        close.set_margin_start(14)
        close.set_margin_top(12)
        overlay.add_overlay(close)
        self._now_content = overlay
        self.now_layer.add_overlay(overlay)

    @staticmethod
    def _fit_album_hero(hero, compact):
        """Fit the reference hero to the actual room, including an open pane."""
        orientation = Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL
        if hero.get_orientation() != orientation:
            hero.set_orientation(orientation)
        for edge, wanted in (('top', 76 if compact else 64), ('start', 20 if compact else 40),
                             ('end', 20 if compact else 40)):
            if getattr(hero, 'get_margin_' + edge)() != wanted:
                getattr(hero, 'set_margin_' + edge)(wanted)

    def _album_page(self, album):
        self.trail.replace(Place('album', album.title, album.id))
        if self.phone:
            self._phone_album_page(album)
            return
        hero = named(Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=36,
                             margin_top=64, margin_start=40, margin_end=40, margin_bottom=30), 'td-hero')
        hero.set_valign(Gtk.Align.START)
        self._album_hero = hero
        self._fit_album_hero(hero, 0 < self.layer.get_width() <= 720)
        self.light.set_source(hues=album.hues, name=album.title)
        def light_artwork(texture):
            if self.view == 'album' and self.album_id == album.id and not self.now_tab:
                self.light.set_source(picture=texture, hue=album.hues[0] if album.hues else None,
                                      name=album.title)
        cover = named(CoverSlot(AlbumCover(album, 264, on_loaded=light_artwork, flexible=True), 264, flexible=True), 'td-hero-cover')
        hero.append(cover)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, hexpand=True)
        trail = NavigationTrailBar(self.trail, title=False) if self.trail.can_go_back else text('Album', 'small', weight=600, muted=True)
        words.append(trail)
        self._album_title_label = named(text(album.title, 'album-hero-title', wrap=True, margin_top=6, margin_bottom=8), 'td-album-title')
        words.append(self._album_title_label)
        artist = Gtk.Button(halign=Gtk.Align.START)
        artist.set_child(text(album.artist, 'album-artist'))
        artist.add_css_class('td-link')
        artist.connect('clicked', lambda *_: self.show_view('artists'))
        words.append(artist)
        line = str(album.year) if album.year else ''
        if album.songs:
            line += f' · {len(album.songs)} songs · {album.minutes} min'
        words.append(text(line, 'body', muted=True, margin_top=6))
        controls = Adw.WrapBox(child_spacing=6, line_spacing=6, margin_top=20)
        here = bool(album.songs and self.player.song_id in {s.id for s in album.songs} and self.player.playing)
        play = named(TextButton('Pause' if here else 'Play', icon='pause' if here else 'play',
                                style='key', size='large', on_click=lambda: self.play_album(album)), 'td-play-album')
        controls.append(play)
        shuffle = named(TextButton('Shuffle', icon='shuffle', style='fill', size='large',
                                   on_click=lambda: self.play_album(album, shuffle=True)), 'td-shuffle-album')
        controls.append(shuffle)
        controls.append(named(IconOnlyButton('heart', 'Love album', size='large',
            active=self._album_loved(album), on_click=lambda: self.love_album(album)), 'td-love-album'))
        controls.append(named(IconOnlyButton('share-2', 'Share album', size='large',
            on_click=lambda: self.share_album(album)), 'td-share-album'))
        more_button = named(IconOnlyButton('ellipsis', 'More', size='large',
            on_click=lambda: self._album_menu(album, more_button)), 'td-album-more')
        more_button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
        controls.append(more_button)
        words.append(controls)
        hero.append(words)
        self.page.append(hero)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=28, margin_end=28, margin_bottom=10)
        listing = SongList(tuple((album, song) for song in album.songs),
                           lambda a, song: SongRow(a, song, self.player, self.play_song, self.love_song),
                           lambda row, a, song: row.set_subject(a, song, self.player),
                           self.scroll.get_vadjustment(), self.page,
                           lambda visible: setattr(self, '_rows', visible))
        listing.set_margin_top(0)
        rows.append(listing)
        self.page.append(rows)
        # v70's empty album uses a paragraph, whose default block margins
        # supplement .tfoot's padding; populated albums use a div.
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=16 if album.songs else 18,
                         margin_bottom=0 if album.songs else 12, margin_start=30, margin_end=30)
        if album.label:
            footer.append(text(f'{album.year} · {album.label}', 'small', muted=True))
        if album.note:
            footer.append(text(album.note, 'small', muted=True, wrap=True))
        if not album.songs:
            footer.append(text('This album is still being added from your library.', 'small', muted=True, wrap=True))
        self.page.append(footer)
        more = more_albums(self.library, album)
        if more:
            related = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=30,
                              margin_end=30, margin_top=26)
            related.append(text('More to listen to', 'title-2', margin_bottom=12))
            strip = Gtk.Box(spacing=16)
            for other in more:
                strip.append(AlbumTile(other, self.open_album))
            scroll = ScrollView(strip, horizontal=True)
            scroll.set_vexpand(False)
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
            scroll.set_propagate_natural_height(True)
            related.append(scroll)
            related_bin = Adw.BreakpointBin(child=related, width_request=1, height_request=1)
            related_phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 559px'))
            related_phone.add_setter(related, 'margin-start', 16)
            related_phone.add_setter(related, 'margin-end', 16)
            related_bin.add_breakpoint(related_phone)
            self.page.append(related_bin)

    # ── Listen now and the phone's page parts (v71 tideMain) ──────────

    def _phone_header(self, meta):
        """v71 `.tnarrow .ttl.tttl`: the kit's page header, 30 px title and the meta on its own line."""
        header = named(PageHeader(self.trail, meta=meta), 'td-trail')
        header.set_margin_top(62)
        header.set_margin_start(20)
        header.set_margin_end(20)
        header.set_margin_bottom(10)
        self.page.append(header)
        return header

    def _album_strip(self, albums, *, name=None):
        """v71 `.acrow`: 160 px sleeves in a row that scrolls sideways."""
        strip = Gtk.Box(spacing=16)
        for album in albums:
            strip.append(AlbumTile(album, self.open_album))
        scroll = ScrollView(strip, horizontal=True)
        scroll.set_vexpand(False)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.set_propagate_natural_height(True)
        if name:
            scroll.set_name(name)
        return scroll

    def _home_page(self):
        """Listen now (v71, a phone place): two features, then Recently played; the library's state under the title."""
        found = self.library.find_song(self.player.song_id)
        current = found[0] if found else next(iter(self.library.albums), None)
        if current is not None:
            self.light.set_source(hue=current.hues[0] if current.hues else None, name=current.title)
        self.trail.replace(Place('home', 'Listen now'))
        sources = self.library.sources
        state = ('A source needs attention' if any(source.state == 'bad' for source in sources)
                 else 'Library up to date')
        count = len(sources)
        meta = ['Picked from what you play here',
                (f'{state} · {count} source' + ('' if count == 1 else 's'), self.open_sources)]
        if self.phone:
            self._phone_header(meta)
        else:
            header = named(NavigationTrailBar(self.trail, meta=meta), 'td-trail')
            header.set_margin_top(24)
            header.set_margin_start(16)
            header.set_margin_end(20)
            header.set_margin_bottom(12)
            self.page.append(header)
        margin = 16 if self.phone else 28
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=margin, margin_end=margin)
        self._library_pad = pad
        self.page.append(pad)
        features = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if self.phone else Gtk.Orientation.HORIZONTAL,
                           spacing=14, homogeneous=not self.phone)
        features.set_name('td-features')
        if current is not None:
            if found and found[0].id == current.id:
                line = f'{current.artist} · you stopped at {found[1].title}'
            else:
                line = current.artist
            features.append(FeatureCard(current, 'Jump back in', line, self.open_album))
        quiet = quiet_pick(self.library, current)
        if quiet is not None:
            n = len(quiet.songs)
            features.append(FeatureCard(quiet, 'For a quiet evening',
                                        f'{quiet.artist} · {n} song' + ('' if n == 1 else 's') + f' · {quiet.minutes} min',
                                        self.open_album))
        pad.append(features)
        recent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=26)
        recent.append(text('Recently played', 'hero_title', margin_bottom=12))
        recent.append(self._album_strip(self.library.albums[:8], name='td-recent'))
        pad.append(recent)

    def _phone_album_page(self, album):
        """v71 `.tnarrow .hero`: the sleeve centred, the name, the artist, then Play and Shuffle side by side.

        No blurred sleeve behind it: the album's wash runs down the page and fades out at 78%.
        """
        self.light.set_source(hue=album.hues[0] if album.hues else None, name=album.title)
        self.light.set_size_request(-1, int(max(1, self.layer.get_height() or self.get_height()) * .78))
        hero = named(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.FILL,
                             margin_top=76, margin_start=24, margin_end=24, margin_bottom=8), 'td-hero')
        side = min(248, int(self.get_width() * .64)) if self.get_width() > 0 else 248
        cover = named(AlbumCover(album, side, fill=False), 'td-hero-cover')
        cover.set_halign(Gtk.Align.CENTER)
        hero.append(cover)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL)
        if self.trail.can_go_back:
            trail = NavigationTrailBar(self.trail, title=False)
            trail.set_halign(Gtk.Align.CENTER)
            trail.set_margin_bottom(4)
            words.append(trail)
        words.append(named(text(album.title, 'page-title', wrap=True, justify=Gtk.Justification.CENTER, xalign=.5,
                                margin_top=2), 'td-album-title'))
        artist = Gtk.Button(label=album.artist, halign=Gtk.Align.CENTER)
        artist.add_css_class('td-link')
        artist.connect('clicked', lambda *_: self.show_section('artists'))
        words.append(artist)
        line = str(album.year) if album.year else ''
        if album.songs:
            line += f' · {len(album.songs)} songs · {album.minutes} min'
        words.append(text(line, 'body', muted=True, xalign=.5, margin_top=6))
        controls = Gtk.Box(spacing=10, homogeneous=True, halign=Gtk.Align.CENTER, margin_top=16)
        here = bool(album.songs and self.player.song_id in {s.id for s in album.songs} and self.player.playing)
        play = named(TextButton('Pause' if here else 'Play', icon='pause' if here else 'play',
            style='key', size='touch', on_click=lambda: self.play_album(album)), 'td-play-album')
        shuffle = named(TextButton('Shuffle', icon='shuffle', style='fill', size='touch',
            on_click=lambda: self.play_album(album, shuffle=True)), 'td-shuffle-album')
        for button in (play, shuffle):
            controls.append(button)
        words.append(controls)
        hero.append(words)
        self.page.append(hero)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=16, margin_end=16, margin_bottom=10)
        rows.append(SongList(tuple((album, song) for song in album.songs),
                             lambda a, song: SongRow(a, song, self.player, self.play_song, self.love_song, phone=True),
                             lambda row, a, song: row.set_subject(a, song, self.player),
                             self.scroll.get_vadjustment(), self.page,
                             lambda visible: setattr(self, '_rows', visible), row_height=PHONE_ALBUM_ROW_HEIGHT))
        self.page.append(rows)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=16, margin_start=16, margin_end=16)
        if album.label:
            footer.append(text(f'{album.year} · {album.label}', 'small', muted=True))
        if album.note:
            footer.append(text(album.note, 'small', muted=True, wrap=True))
        if not album.songs:
            footer.append(text('This album is still being added from your library.', 'small', muted=True, wrap=True))
        self.page.append(footer)
        more = more_albums(self.library, album)
        if more:
            related = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=16, margin_end=16, margin_top=26)
            related.append(text('More to listen to', 'hero-title', margin_bottom=12))
            related.append(self._album_strip(more))
            self.page.append(related)

    def _append_tiles(self, count):
        grid = self._tile_grid
        if grid is None:
            return
        stop = min(len(self._tile_items), self._tile_next + count)
        for item in self._tile_items[self._tile_next:stop]:
            grid.append(self._tile_factory(item))
        self._tile_next = stop

    def _queue_tile_fill(self, _adjustment):
        if self._tile_grid is None or self._tile_fill_source or self._tile_next >= len(self._tile_items):
            return
        adjustment = self.scroll.get_vadjustment()
        page = adjustment.get_page_size()
        if page <= 0 or adjustment.get_upper() - adjustment.get_value() - page > page * 1.5:
            return
        self._tile_fill_source = GLib.idle_add(self._fill_tiles)

    def _fill_tiles(self):
        self._tile_fill_source = 0
        if self._tile_grid is not None:
            self._append_tiles(32)
        return False

    def _artist_tile(self, item):
        artist, releases = item
        button = Gtk.Button()
        button.add_css_class('td-artist-tile')
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # TODO(kit-request tide-02-album-surfaces.md): semantic artist gradient and initials.
        portrait = Gtk.Box()
        portrait.add_css_class('td-artist-portrait')
        portrait.set_overflow(Gtk.Overflow.VISIBLE)
        face = PersonAvatar(artist.removeprefix('The ')[:1], 150,
                            hue=releases[-1].hues[0] if releases[-1].hues else None)
        portrait.append(face)
        col.append(CoverSlot(portrait, 150, fill=True))
        col.append(text(artist, 'body', weight=600, margin_top=10, halign=Gtk.Align.FILL, hexpand=True, xalign=.5,
                        ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
        n = len(releases)
        col.append(text(f'{n} album' + ('s' if n != 1 else ''), 'meta', muted=True, halign=Gtk.Align.CENTER))
        button.set_child(col)
        button.connect('clicked', lambda _b, identifier=releases[-1].id: self.open_album(identifier))
        button.set_name('td-artist-' + artist)
        return button

    def _search_page(self):
        artists, albums, songs = search_library(self.library, self.query)
        if self.phone:
            self._phone_search(artists, albums, songs)
            return
        chosen = self.view
        counts = {'search-artists': len(artists), 'search-albums': len(albums),
                  'search-songs': len(songs)}
        title = {'search': 'Search results', 'search-artists': 'Artists',
                 'search-albums': 'Albums', 'search-songs': 'Songs'}[chosen]
        meta = [f'{counts[chosen]:,} matches'] if chosen in counts else []
        self.trail.replace(Place(chosen, title))
        header = named(NavigationTrailBar(self.trail, meta=meta), 'td-trail')
        header.title.set_valign(Gtk.Align.CENTER)
        header.set_margin_top(24)
        header.set_margin_start(16)
        header.set_margin_end(20)
        header.set_margin_bottom(16)
        self.page.append(header)
        margin = 16 if self.get_width() <= 559 else 28
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=26,
                      margin_start=margin, margin_end=margin)
        self._library_pad = pad
        self.page.append(pad)
        if not self.query.strip():
            pad.append(text('Search artists, albums and songs', 'body', muted=True, margin_top=12))
            return
        if not (artists or albums or songs):
            pad.append(text(f'No results for “{self.query.strip()}”', 'body', muted=True, margin_top=12))
            return

        def section(name, count, view):
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            heading.append(text(name, 'title-2', weight=600, hexpand=True))
            if chosen == 'search' and count:
                more = Gtk.Button(label=f'View all {count:,}', valign=Gtk.Align.CENTER)
                more.add_css_class('td-search-more')
                more.connect('clicked', lambda *_: self.trail.open(Place(view, name)))
                heading.append(more)
            block.append(heading)
            pad.append(block)
            return block

        if artists and chosen in ('search', 'search-artists'):
            block = section('Artists', len(artists), 'search-artists')
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               min_children_per_line=1, max_children_per_line=16,
                               column_spacing=18, row_spacing=22)
            grid.set_name('td-search-artists')
            block.append(grid)
            if chosen == 'search-artists':
                self._tile_grid, self._tile_items, self._tile_factory = grid, artists, self._artist_tile
                self._append_tiles(64)
            else:
                for item in artists[:8]:
                    grid.append(self._artist_tile(item))
        if albums and chosen in ('search', 'search-albums'):
            block = section('Albums', len(albums), 'search-albums')
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               min_children_per_line=1, max_children_per_line=16,
                               column_spacing=18, row_spacing=24)
            grid.set_name('td-search-albums')
            block.append(grid)
            if chosen == 'search-albums':
                self._tile_grid, self._tile_items = grid, albums
                self._tile_factory = lambda album: AlbumTile(album, self.open_album, year=True, size=164)
                self._append_tiles(64)
            else:
                for album in albums[:12]:
                    grid.append(AlbumTile(album, self.open_album, year=True, size=164))
        if songs and chosen in ('search', 'search-songs'):
            block = section('Songs', len(songs), 'search-songs')
            columns = [Column(None, '', width=40), Column('title', 'Title', expand=True),
                       Column('album', 'Album', expand=True), Column(None, '', width=40),
                       Column('time', 'Time', width=56, end=True)]
            table = TableHeader(columns, sort=(self.sort_key, 'descending' if self.descending else 'ascending'),
                                on_sort=self._sort)
            block.append(table)
            def make_row(album, song):
                row = SongRow(album, song, self.player, self.play_song, self.love_song, show_album=True)
                table.align(row)
                return row
            listing = SongList(songs if chosen == 'search-songs' else songs[:30], make_row,
                               lambda row, album, song: row.set_subject(album, song, self.player),
                               self.scroll.get_vadjustment(), self.page,
                               lambda visible: setattr(self, '_rows', visible))
            block.append(listing)

    def _phone_search(self, artists, albums, songs):
        """v71 phone search: Results, counted; then Artists (rows), Albums (a row of sleeves), Songs (two lines)."""
        query = self.query.strip()
        if not query:
            rows = songs_matching(self.library, '', self.sort_key, self.descending)
            self.trail.replace(Place('search', 'Songs'))
            self._phone_header([f'{len(rows)} songs'])
            pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=16, margin_end=16)
            self._library_pad = pad
            self.page.append(pad)
            pad.append(self._phone_songs(rows))
            return

        def counted(n, noun):
            return f'{n} {noun}' + ('' if n == 1 else 's')
        meta = ', '.join([counted(len(artists), 'artist')] * bool(artists) + [counted(len(albums), 'album')] * bool(albums)
                         + [counted(len(songs), 'song')])
        self.trail.replace(Place('search', 'Results'))
        self._phone_header([meta])
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=16, margin_end=16)
        pad.set_name('td-search-results')
        self._library_pad = pad
        self.page.append(pad)

        def heading(name, first):
            label = text(name, 'hero_title', weight=700, margin_top=4 if first else 18, margin_bottom=8)
            label.add_css_class('td-search-heading')
            pad.append(label)

        if not (artists or albums or songs):
            pad.append(text(f'Nothing matches “{query}”.', 'body', muted=True, margin_top=20, margin_bottom=20))
            return
        if artists:
            heading('Artists', True)
            listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            listing.set_name('td-search-artists')
            for artist, releases in artists[:8]:
                listing.append(self._artist_row(artist, releases))
            pad.append(listing)
        if albums:
            heading('Albums', not artists)
            strip = self._album_strip(albums[:12], name='td-search-albums')
            pad.append(strip)
        if songs:
            heading('Songs', not (artists or albums))
            pad.append(self._phone_songs(songs))

    def _artist_row(self, artist, releases):
        """v71 `.tsart`: the artist's face (52), the name, "Artist · n albums"."""
        button = Gtk.Button()
        button.add_css_class('td-artist-row')
        line = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=4, margin_end=4)
        line.append(PersonAvatar(artist.removeprefix('The ')[:1], 52,
                                 hue=releases[-1].hues[0] if releases[-1].hues else None))
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        words.append(text(artist, 'title_2', ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
        n = len(releases)
        words.append(text(f'Artist · {n} album' + ('s' if n != 1 else ''), 'meta', muted=True))
        line.append(words)
        button.set_child(line)
        button.update_property([Gtk.AccessibleProperty.LABEL], [artist])
        button.connect('clicked', lambda _b, identifier=releases[-1].id: self.open_album(identifier))
        button.set_name('td-artist-row-' + artist)
        return button

    def _library_page(self):
        subject = self.library.find_song(self.player.song_id)
        if subject:
            playing_album, _ = subject
            self.light.set_source(hue=playing_album.hues[0] if playing_album.hues else None,
                                  name=playing_album.title)
        rows = songs_matching(self.library, self.query, self.sort_key, self.descending)
        title = 'Results' if self.query and self.view == 'songs' else self.view.title()
        count = len(self.library.albums) if self.view == 'albums' else len(self.library.artists) if self.view == 'artists' else len(rows)
        unit = {'albums': 'albums', 'artists': 'artists', 'songs': 'songs'}[self.view]
        meta = [f'{count} {unit}']
        if self.view == 'albums':
            meta.append('Recently played first')
        if self.query and self.view == 'songs':
            meta = [f'{count} songs match “{self.query.strip()}”']
        # replace changes the current trail title, never creates a second history.
        self.trail.replace(Place(self.view, title))
        if self.phone:
            self._phone_library(rows, meta)
            return
        header = named(NavigationTrailBar(self.trail, meta=meta), 'td-trail')
        header.title.set_valign(Gtk.Align.CENTER)
        header.title.set_margin_bottom(2)
        header.set_margin_top(24)
        # The trail's back key contributes its own 30 px allocation. Align the
        # title with the reference header instead of adding a second inset.
        header.set_margin_start(16)
        header.set_margin_end(300)
        header.set_margin_bottom(12)
        header_bin = Adw.BreakpointBin(child=header, width_request=1, height_request=1)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 899px'))
        narrow.add_setter(header, 'margin-end', 20)
        header_bin.add_breakpoint(narrow)
        phone = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 559px'))
        phone.add_setter(header, 'margin-top', 74)
        phone.add_setter(header, 'margin-start', 16)
        phone.add_setter(header, 'margin-end', 20)
        header_bin.add_breakpoint(phone)
        self.page.append(header_bin)
        # FlowBox needs height-for-width measurement to reach the viewport.
        # A nested BreakpointBin measures its natural wide height, clipping
        # later rows and leaving the scroll adjustment too short on phones.
        margin = 16 if self.get_width() <= 559 else 28
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=margin, margin_end=margin)
        self._library_pad = pad
        self.page.append(pad)
        if self.view == 'albums':
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               min_children_per_line=1, max_children_per_line=16,
                               column_spacing=18, row_spacing=24)
            grid.set_name('td-album-grid')
            self._tile_grid = grid
            self._tile_items = self.library.albums
            self._tile_factory = lambda album: AlbumTile(album, self.open_album, year=True, size=164)
            self._append_tiles(64)
            pad.append(grid)
        elif self.view == 'artists':
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                               min_children_per_line=1, max_children_per_line=16,
                               column_spacing=18, row_spacing=22)
            grid.set_name('td-artist-grid')
            self._tile_grid = grid
            self._tile_items = self.library.artists
            self._tile_factory = self._artist_tile
            self._append_tiles(64)
            pad.append(grid)
        else:
            # TODO(kit-request tide-03-song-rows.md): title/album expand weights 1.3/1.
            columns = [Column(None, '', width=40), Column('title', 'Title', expand=True),
                       Column('album', 'Album', expand=True), Column(None, '', width=40),
                       Column('time', 'Time', width=56, end=True)]
            header = named(TableHeader(columns, sort=(self.sort_key, 'descending' if self.descending else 'ascending'),
                                      on_sort=self._sort), 'td-table-header')
            for key, button in zip(('number', 'title', 'album', 'love', 'time'), header.get_first_child() and list(self._children(header))):
                button.set_name('td-sort-' + key)
            pad.append(header)
            def make_row(album, song):
                row = SongRow(album, song, self.player, self.play_song, self.love_song, show_album=True)
                header.align(row)
                return row
            listing = SongList(rows, make_row,
                               lambda row, album, song: row.set_subject(album, song, self.player),
                               self.scroll.get_vadjustment(), self.page,
                               lambda visible: setattr(self, '_rows', visible))
            pad.append(listing)

    def _phone_grid(self, name, items, factory, count=64):
        """v71 `.tnarrow .agrid` / `.artgrid`: two columns, 14 apart, rows 18 apart."""
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=2, max_children_per_line=2,
                           column_spacing=14, row_spacing=18)
        grid.set_name(name)
        self._tile_grid, self._tile_items, self._tile_factory = grid, items, factory
        self._append_tiles(count)
        return grid

    def _phone_songs(self, rows):
        """v71 `.tnarrow .songs .tr`: two-line rows (the title over its album), no column head."""
        return SongList(rows,
                        lambda album, song: SongRow(album, song, self.player, self.play_song, self.love_song,
                                                    show_album=True, phone=True),
                        lambda row, album, song: row.set_subject(album, song, self.player),
                        self.scroll.get_vadjustment(), self.page,
                        lambda visible: setattr(self, '_rows', visible), row_height=PHONE_SONG_ROW_HEIGHT)

    def _phone_library(self, rows, meta):
        self._phone_header(meta)
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=16, margin_end=16)
        self._library_pad = pad
        self.page.append(pad)
        if self.view == 'albums':
            pad.append(self._phone_grid('td-album-grid', self.library.albums,
                                        lambda album: AlbumTile(album, self.open_album, year=True, size=178,
                                                                fill=True)))
        elif self.view == 'artists':
            pad.append(self._phone_grid('td-artist-grid', self.library.artists, self._artist_tile))
        else:
            pad.append(self._phone_songs(rows))

    @staticmethod
    def _children(widget):
        child = widget.get_first_child()
        while child:
            yield child
            child = child.get_next_sibling()

    def _sort(self, key, direction):
        self.sort_key, self.descending = key, direction == 'descending'
        self._render()

    def play_album(self, album, shuffle=False):
        if album.songs:
            if not shuffle and self.player.song_id in {song.id for song in album.songs}:
                self.source.toggle()
            else:
                self.source.play(album, shuffle=shuffle)

    def play_song(self, album, song):
        if self.player.song_id == song.id:
            self.source.toggle()
        else:
            self.source.play(album, song)

    def love_song(self, song_id):
        self.source.love(song_id)
        self.refresh_library()

    def love_playing(self):
        if self.player.song_id:
            self.love_song(self.player.song_id)

    def love_album(self, album):
        loved = self._album_loved(album)
        self.source.love_album(album.id)
        Toast.show(self.layer, 'Removed from favorites' if loved else 'Added to favorites')
        self.refresh_library()

    def _album_loved(self, album):
        if self.source.fixture:
            return album.id in getattr(self.source, 'album_loves', ())
        return bool(album.songs) and all(song.loved for song in album.songs)

    def share_album(self, album):
        if not self.source.fixture:
            self._share_audio(album.title, tuple(song.id for song in album.songs))
            return
        if self.phone:
            self._share_subject = album
            self.phone_bar.share(album.title)
            return
        self.get_clipboard().set(f'{album.title} · {album.artist}')
        Toast.show(self.layer, 'Copied', kind='copied')

    def _share_audio(self, title, track_ids):
        from .sharing import local_files
        try:
            paths = local_files(self.source.store, track_ids)
        except (OSError, ValueError) as error:
            Toast.show(self.layer, str(error), kind='warning')
            return
        from luma_appkit.application_directory import discover
        from luma_appkit.bar_share import installed_targets
        anchor = self.phone_bar.center if self.phone else self.corner
        receivers = {}
        self.share_sheet = sheet = ShareSheet.present(
            anchor, document=ShareSubject(title, f'{len(paths)} audio file' + ('s' if len(paths) != 1 else ''),
                                         kind='file', icon='org.projectluma.Tide', share_link_available=False),
            targets=(), choices=('send-copy',), copy_actions=('copy', 'save'), show_link=False,
            on_choice=lambda choice, value: self._share_audio_choice(paths, receivers, choice, value))
        # Mixed albums require a receiver for every actual format, rather than
        # showing an app that can only receive one track's content type.
        mimes = {Gio.content_type_guess(path.name, None)[0] for path in paths}
        pending = set(mimes)
        candidates = {}
        def ready(mime, apps, error):
            if self._closed or sheet._dismissed: return
            pending.discard(mime)
            candidates[mime] = {} if error else {
                app.get_id(): app for app in apps
                if app.get_id() != 'org.projectluma.Tide.desktop'
                and (app.supports_files() or app.supports_uris()) and app.should_show()}
            if pending: return
            common = set.intersection(*(set(rows) for rows in candidates.values())) if candidates else set()
            targets = []
            for target in installed_targets(ShareSubject(title, mime_type=next(iter(mimes), 'audio/mpeg'))):
                if target.app_id + '.desktop' in common:
                    targets.append(target)
                    receivers[target.key] = candidates[next(iter(mimes))][target.app_id + '.desktop']
            sheet.set_targets(targets)
        self._share_discovery = [discover(mime, lambda apps, error, mime=mime: ready(mime, apps, error))
                                 for mime in sorted(mimes)]

    def _share_audio_choice(self, paths, receivers, choice, value):
        if choice == 'copy':
            uris = ('\r\n'.join(path.as_uri() for path in paths) + '\r\n').encode()
            provider = Gdk.ContentProvider.new_for_bytes('text/uri-list', GLib.Bytes.new(uris))
            if not self.get_clipboard().set_content(provider):
                Toast.show(self.layer, 'Could not copy these files', kind='error')
                return ShareResult(False)
            return ShareResult(True, 'Files copied')
        if choice == 'target':
            receiver = receivers.get(value)
            if receiver is not None:
                from luma_appkit.application_directory import launch
                def accepted(ok, error):
                    if self._closed: return
                    Toast.show(self.layer, 'Opened in ' + receiver.get_display_name() if ok else error or
                               'Could not open these files in that application', kind='sent' if ok else 'error')
                launch(receiver.get_id(), [Gio.File.new_for_path(str(path)) for path in paths], callback=accepted)
            return ShareResult(False)
        if choice == 'save':
            def selected(files):
                if not files or self._closed:
                    return
                destination = files[0].get_path()
                if destination is None:
                    Toast.show(self.layer, 'Choose a folder on this computer', kind='warning')
                    return
                from .sharing import save_copies
                Toast.show(self.layer, 'Saving audio files…')
                future = self._loader.submit(save_copies, paths, destination)
                def finished(done):
                    try:
                        done.result()
                        message, kind = 'Audio files saved', 'done'
                    except OSError:
                        message, kind = 'Could not save these audio files', 'error'
                    def notify():
                        if not self._closed:
                            Toast.show(self.layer, message, kind=kind)
                        return False
                    GLib.idle_add(notify)
                future.add_done_callback(finished)
            ask_for_file(self, FileRequest('Save audio files', mode='folder', start='music'), selected)
        return ShareResult(False)

    def _album_menu(self, album, anchor):
        loved = self._album_loved(album)
        playing = (self.player.playing and
                   self.player.song_id in {song.id for song in album.songs})
        menu = FloatingMenu([
            album.title,
            RichMenuItem('Pause album' if playing else 'Play album', icon='pause' if playing else 'play',
                         on_activate=lambda: self.play_album(album)),
            RichMenuItem('Shuffle album', icon='shuffle', on_activate=lambda: self.play_album(album, shuffle=True)),
            RichMenuItem('Remove from favorites' if loved else 'Add to favorites', icon='heart',
                         on_activate=lambda: self.love_album(album)),
            RichMenuItem('Copy album details' if self.source.fixture else 'Share audio files', icon='share-2', on_activate=lambda: self.share_album(album)),
        ], label=album.title, title=album.title)
        menu.popup(anchor, align='end')

    def _player_changed(self, player):
        if self._closed:
            return False
        previous = self.player
        self.player = player
        ordered = self.source.order_albums(self.library) if not self.source.fixture else self.library
        reordered = ordered is not self.library
        self.library = ordered
        self.deck.update(self.library, player)
        self.phone_bar.update()
        if self.now_tab and self.phone and (player.song_id, player.playing, player.shuffle, player.repeat) == (
                previous.song_id, previous.playing, previous.shuffle, previous.repeat):
            page = self.now_layer.get_child()
            if isinstance(page, NowPlayingPhone):
                found = self.library.find_song(player.song_id)
                if found:
                    page.update(player, found[1])
        if reordered or (player.song_id, player.playing) != (previous.song_id, previous.playing):
            adjustment = self.scroll.get_vadjustment()
            position = adjustment.get_value()
            self._render()
            GLib.idle_add(lambda: adjustment.set_value(position))
        for row in self._rows:
            row.set_player(player)
        if self.pane_kind == 'queue' and (player.song_id, player.queue) != (previous.song_id, previous.queue):
            self._fill_pane()
        return False

    def open_search(self):
        if self.phone:
            self.phone_bar.open_search()
            return
        if self.search.get_visible():
            self.search_item.entry.grab_focus()
            return
        self._search_origin = self.trail.current
        self.modes.set_visible(True)
        self.search.set_visible(True)
        self.corner.controls['actions.0'].set_visible(False)
        self.search_item.entry.grab_focus()

    def toggle_search(self):
        if self.search.get_visible():
            self.close_search()
        else:
            self.open_search()

    def _hide_search(self):
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = 0
        self.search.set_visible(False)
        self.search_item.set_text('')
        self.query = ''
        self.modes.set_visible(True)
        self.corner.controls['actions.0'].set_visible(True)

    def close_search(self):
        origin = self._search_origin or Place('albums', 'Albums')
        self._hide_search()
        self._search_origin = None
        self.trail.replace(origin)
        self._navigated(origin, "replace")

    def _search_changed(self, query):
        if not self.search.get_visible():
            return
        if self._search_source:
            GLib.source_remove(self._search_source)
        if not self._closed:
            self._search_source = GLib.timeout_add(120, self._apply_search)

    def _apply_search(self):
        self._search_source = 0
        if self._closed:
            return GLib.SOURCE_REMOVE
        self.query = self.search_item.text
        if self.view != 'songs':
            self.trail.replace(Place('songs', 'Results'))
            self.view = 'songs'
            self.now_tab = None
        self._render()
        self.scroll.get_vadjustment().set_value(0)
        return GLib.SOURCE_REMOVE

    def _key(self, _controller, key, _code, state):
        if key == Gdk.KEY_Escape:
            if self.phone and self.phone_bar.center.searching:
                self.phone_bar.center.close_search()
                return True
            if self.search.get_visible():
                self.close_search()
                return True
            if self.now_tab:
                self.close_now_playing()
                return True
        if state & Gdk.ModifierType.CONTROL_MASK and key in (Gdk.KEY_f, Gdk.KEY_F):
            self.open_search()
            return True
        if key == Gdk.KEY_space and not isinstance(self.get_focus(), (Gtk.Editable, Gtk.Button)):
            self.source.toggle()
            return True
        return False

    def toggle_sources(self):
        if self.pane_kind == 'sources':
            self.pane.close()
        else:
            self.open_sources()

    def open_sources(self):
        self.pane_kind, self.source_id, self.editing = 'sources', None, False
        self.pane_trail.start(Place('sources', 'Sources'))
        self._fill_pane()
        self.pane.open(subject='sources')

    def toggle_queue(self):
        if self.pane_kind == 'queue':
            self.pane.close()
        else:
            self.pane_kind, self.source_id = 'queue', None
            self._fill_pane()
            self.pane.open(subject=self.player.song_id)

    def set_queue_visible(self, visible):
        if visible and self.pane_kind != 'queue':
            self.toggle_queue()
        elif not visible and self.pane_kind == 'queue':
            self.pane.close()

    def _pane_closed(self):
        self.pane_kind, self.source_id = None, None

    def _pane_navigated(self, place, _direction):
        self.source_id = place.subject
        self.editing = place.view == 'edit'
        self._fill_pane()

    def _open_source(self, identifier, edit=False):
        source = self.library.source(identifier)
        if edit:
            self.pane_trail.start(Place('sources', 'Sources'))
        self.pane_trail.open(Place('edit' if edit else 'source', ('Edit ' if edit else '') + source.name, identifier))

    def _add_source(self):
        self.pane_trail.open(Place('add', 'Add source', 'add'))

    def _add_server(self):
        self.pane_trail.open(Place('add-server', 'Navidrome or Subsonic', 'add-server'))

    def _add_folder(self):
        self.app.activate_action('add-music', None)

    def present_source_menu(self):
        self.open_sources()
        self._add_source()

    def _fill_pane(self):
        if not self.pane_kind:
            return
        self.pane.clear()
        if self.pane_kind == 'queue':
            self.pane.set_title('Up next')
            if not self.player.song_id:
                self.pane.close()
                return
            try:
                album, _ = self.library.song(self.player.song_id)
            except StopIteration:
                self.pane.close()
                return
            self.pane.add_section('Playing from ' + album.title, action=('Clear', self._clear_queue))
            queue = next_queue(self.player)
            first_row = []
            later_rows = []
            for index, identifier in enumerate(queue):
                try:
                    a, song = self.library.song(identifier)
                except StopIteration:
                    continue
                (first_row if index == 0 else later_rows).append(QueueRow(a, song, index == 0, self.play_song))
            if first_row:
                self.pane.add_list(first_row)
            if later_rows:
                self.pane.add(text(f'Then {len(later_rows)} more', 'label', margin_top=14, margin_bottom=4))
                self.pane.add_list(later_rows)
            return
        self.pane.set_title(self.pane_trail.current.title)
        if self.source_id:
            self.pane.add(NavigationTrailBar(self.pane_trail, title=False))
        if self.source_id in ('add', 'add-server') or self.editing:
            self._source_form()
        elif self.source_id:
            try:
                source = self.library.source(self.source_id)
            except StopIteration:
                self.source_id = None
                self.pane_trail.start(Place('sources', 'Sources'))
                return
            self.pane.add_hero(source.name, source.subtitle, lead=SourceFace(source.icon, source.state, large=True))
            self.pane.add_facts(source.facts)
            first = [StackedButton('refresh-cw', 'Sync now', on_click=lambda: self._sync(source.id)),
                     StackedButton('pencil' if source.remote else 'folder', 'Edit' if source.remote else 'Show in Filer',
                                   on_click=lambda: self._open_source(source.id, True) if source.remote else self._show_folder(source.id))]
            first[1].set_name('td-source-edit' if source.remote else 'td-source-show-folder')
            self.pane.add(StackedButtons(first, small=True))
            if source.remote:
                sign = StackedButton('key-round' if source.state == 'bad' else 'log-out',
                                     'Sign in' if source.state == 'bad' else 'Sign out',
                                     on_click=lambda: self._open_source(source.id, True) if source.state == 'bad' else self._confirm_sign_out(source))
                sign.set_name('td-source-sign-out')
                remove = StackedButton('trash-2', 'Remove', danger=True,
                                       on_click=lambda: self._confirm_remove(source))
                remove.set_name('td-source-remove')
                self.pane.add(StackedButtons([sign, remove], small=True))
        else:
            self._source_menu_buttons = {}
            rows = []
            for source in self.library.sources:
                row = DetailsRow(source.name, source.subtitle, lead=SourceFace(source.icon, source.state),
                                 on_activate=lambda identifier=source.id: self._open_source(identifier),
                                 actions=[('refresh-cw', 'Sync now', lambda identifier=source.id: self._sync(identifier)),
                                          ('ellipsis', 'More', lambda identifier=source.id: self._source_menu(identifier))])
                row.button.set_name('td-source-' + source.id)
                context = Gtk.GestureClick(button=3)
                context.connect('pressed', lambda gesture, *_args, identifier=source.id:
                                self._source_context(gesture, identifier))
                row.button.add_controller(context)
                hold = Gtk.GestureLongPress(touch_only=True)
                hold.connect('pressed', lambda gesture, *_args, identifier=source.id:
                             self._source_context(gesture, identifier))
                row.button.add_controller(hold)
                keys = Gtk.EventControllerKey()
                keys.connect('key-pressed', lambda _controller, key, _code, state, identifier=source.id:
                             self._source_context_key(key, state, identifier))
                row.button.add_controller(keys)
                buttons = list(self._children(row.actions))
                if buttons:
                    buttons[-1].set_name('td-source-more-' + source.id)
                    self._source_menu_buttons[source.id] = buttons[-1]
                rows.append(row)
            add = TextButton('Add source', icon='plus', style='raised', on_click=self._add_source)
            add.set_name('td-source-add')
            self.pane.add_list(rows)
            self.pane.set_footer(add)

    def _source_form(self):
        source = self.library.source(self.source_id) if self.editing else None
        facts = dict(source.facts) if source else {}
        self.pane.add_section('Server')
        heading = Gtk.Box(spacing=10, margin_bottom=16)
        heading.append(SourceFace('server', source.state if source else ''))
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, valign=Gtk.Align.CENTER, hexpand=True)
        words.append(text('Navidrome or Subsonic', 'body', weight=600))
        words.append(text('Any server that speaks Subsonic', 'caption'))
        heading.append(words)
        self.pane.add(heading)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.pane.add(form)
        address = (self.source.source_address(source.id)
                   if source and not self.source.fixture else facts.get('Server', ''))
        self.server_address = TextField('Server address', value=address,
                                        placeholder='music.example.com', purpose='url',
                                        on_activate=lambda _: self._connect_source())
        self.server_user = TextField('Username', value=facts.get('Account', ''),
                                    on_activate=lambda _: self._connect_source())
        self.server_password = TextField('Password', placeholder='Unchanged' if source else None, purpose='password',
                                        on_activate=lambda _: self._connect_source())
        for name, field in (('url', self.server_address), ('user', self.server_user), ('password', self.server_password)):
            field.entry.set_name('td-source-field-' + name)
            form.append(field)
        form.append(text('Kept in your keyring on this computer.', 'caption', wrap=True))
        self.connect_button = action(None, 'Save' if source else 'Connect', self._connect_source,
                                     name='td-source-connect', words=True, primary=True)
        self.connect_button.set_halign(Gtk.Align.END)
        self.pane.set_footer(self.connect_button)
        def focus_address():
            self.server_address.entry.grab_focus()
            self.server_address.entry.select_region(0, 0)
            return False
        GLib.idle_add(focus_address)

    def _connect_source(self):
        address, user, password = self.server_address.text, self.server_user.text, self.server_password.text
        edited = self.editing
        if not address.strip() or not user.strip():
            (self.server_address if not address.strip() else self.server_user).entry.grab_focus()
            return
        def connected(error=''):
            if error:
                Toast.show(self.layer, error, kind='error')
                self.connect_button.set_sensitive(True)
                return
            self.server_password.text = ''
            self.pane_trail.start(Place('sources', 'Sources'))
            self.refresh_library()
            Toast.show(self.layer, 'Saved' if edited else 'Connected', kind='done')
        self.connect_button.set_sensitive(False)
        if self.source.fixture:
            self.source.connect(address, user, password, self.source_id if self.editing else None)
            connected()
        else:
            self.source.connect(address, user, password, self.source_id if self.editing else None,
                                callback=lambda error='': GLib.idle_add(lambda: connected(error) or False), where=self.layer)

    def _sync(self, identifier):
        self.source.sync(identifier)
        self.refresh_library()
        if self.source.fixture:
            Toast.show(self.layer, self.library.source(identifier).name + ' is up to date')
        else:
            self.set_status('Syncing…')

    def _show_folder(self, identifier):
        if self.source.fixture:
            Toast.show(self.layer, 'Showing Downloads in Filer' if identifier == 'dl' else 'Showing Music in Filer')
        else:
            self.source.show_folder(identifier)

    def _source_context(self, gesture, identifier):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._source_menu(identifier)

    def _source_context_key(self, key, state, identifier):
        if key == Gdk.KEY_Menu or (key == Gdk.KEY_F10 and state & Gdk.ModifierType.SHIFT_MASK):
            self._source_menu(identifier)
            return True
        return False

    def _source_menu(self, identifier):
        source = self.library.source(identifier)
        rows = [source.name, RichMenuItem('Sync now', icon='refresh-cw', on_activate=lambda: self._sync(identifier))]
        if source.remote:
            rows += [RichMenuItem('Edit', icon='pencil', on_activate=lambda: self._open_source(identifier, True)),
                     RichMenuItem('Sign out', icon='log-out', on_activate=lambda: self._confirm_sign_out(source)),
                     None, RichMenuItem('Remove', icon='trash-2', danger=True,
                                        on_activate=lambda: self._confirm_remove(source))]
        else:
            rows.append(RichMenuItem('Show in Filer', icon='folder', on_activate=lambda: self._show_folder(identifier)))
        menu = FloatingMenu(rows, label=source.name, title=source.name)
        anchor = self._source_menu_buttons.get(identifier, self.corner.controls['actions.1'])
        menu.popup(anchor, align='end')

    def _confirm_sign_out(self, source):
        dialog = DestructiveDialog.ask(self.layer, title=f'Sign out of {source.name}?',
            body='Its songs leave your library until you sign in again. Songs kept offline stay.',
            action='Sign out', icon='log-out', on_confirm=lambda _: self._sign_out(source),
            on_cancel=lambda: self._restore_source_details(source.id))
        dialog.action_button.set_name('td-confirm-sign-out')

    def _restore_source_details(self, identifier):
        # A phone confirmation replaces the details drawer. Keep the app's
        # selected source reachable again when the user cancels that action.
        # Reopen even when shown is still true: replacement closes the modal
        # handle without changing the pane's remembered visibility wish.
        if (not self._closed
                and any(source.id == identifier for source in self.library.sources)):
            self.open_sources()
            self._open_source(identifier)

    def _sign_out(self, source):
        if self.source.fixture:
            previous = self.source.library
            self.source.sign_out(source.id)
            self.refresh_library()
            Toast.show(self.layer, f'Signed out of {source.name}', undo=lambda: self._fixture_undo(previous))
        else:
            self.source.sign_out(source.id, lambda undo, error: GLib.idle_add(self._signed_out, source, undo, error))

    def _signed_out(self, source, undo, error):
        if error:
            Toast.show(self.layer, error, kind='error')
        else:
            self.refresh_library()
            Toast.show(self.layer, f'Signed out of {source.name}', undo=lambda: self.source.undo_sign_out(undo, self.refresh_library))
        return False

    def _confirm_remove(self, source):
        dialog = DestructiveDialog.ask(self.layer, title=f'Remove {source.name}?',
            body=f'Its songs leave your library. Nothing is deleted from {dict(source.facts).get("Server", source.name)}.',
            action='Remove', on_confirm=lambda _: self._remove_source(source),
            on_cancel=lambda: self._restore_source_details(source.id))
        dialog.action_button.set_name('td-confirm-remove')

    def _remove_source(self, source):
        if self.source.fixture:
            previous = self.source.library
            self.source.remove_source(source.id)
            self.pane_trail.start(Place('sources', 'Sources'))
            self.refresh_library()
            Toast.show(self.layer, f'Removed {source.name}', undo=lambda: self._fixture_undo(previous))
        else:
            # Defer irreversible deletion until Undo expires; closing flushes it.
            self._removals.add(source.id)
            self.pane_trail.start(Place('sources', 'Sources'))
            self.refresh_library()
            Toast.show(self.layer, f'Removed {source.name}', undo=lambda: self._undo_remove(source.id))

    def _undo_remove(self, identifier):
        self._removals.undo(identifier)
        self.refresh_library()

    def _flush_remove(self):
        self._removals.flush()
        return False

    def _fixture_undo(self, previous):
        self.source.library = previous
        self.refresh_library()

    def _clear_queue(self):
        self.source.clear_queue()
        Toast.show(self.layer, 'Cleared Up next', kind='done')

    def open_output(self):
        commands = tuple(Command('output.' + str(index), name, lambda value=name: self.source.output(value), icons.icon_name(icon),
                                 description=subtitle, checked=lambda value=name: self.player.output == value)
                         for index, (name, icon, subtitle) in enumerate(self.player.outputs))
        menu = Menu(CommandRegistry((CommandGroup('', commands),)))
        menu.set_parent(self.deck.output)
        menu.set_position(Gtk.PositionType.TOP)
        menu.connect('closed', lambda widget: GLib.idle_add(widget.unparent))
        menu.popup()

    def close_resources(self):
        if not self._closed:
            self._closed = True
            if self._search_source:
                GLib.source_remove(self._search_source)
                self._search_source = 0
            self._unsubscribe()
            self._loader.shutdown(wait=False, cancel_futures=True)
            self.source.close()

    def _on_close(self):
        self._flush_remove()
        return False
