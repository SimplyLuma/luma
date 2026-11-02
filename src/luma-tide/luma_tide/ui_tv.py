# SPDX-License-Identifier: Apache-2.0
"""Tide's TV presentation: the same Tide, at the couch.

Shown inside `TideWindow` while the shell is in TV posture (docs/developer-
platform/tv-mode.md); the library, playback controller, queue, MPRIS and the
shelf media widget are the window's own. The album is the room: whatever record
has the remote's attention, or is playing, fills the screen blurred (LumaUI's
`TvRoom`). Four places in the shared top bar, moved along without OK: Listen
now, Albums, Playlists, Search. While something plays, the now-playing pill
rides in the bar on every page; OK on it opens Now playing. Left alone on Now
playing while music plays, the screen becomes the record (the idle view).

Remote: arrows move to whatever lies that way; OK opens or plays; Back steps
out level by level, then to the bar, then to the TV home. Space or the remote's
play/pause key plays and pauses from anywhere.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import lumaui_tokens as tokens  # noqa: E402
from luma_appkit.tv_focus import TvFocus  # noqa: E402
from luma_appkit.tv_idle import TvIdle  # noqa: E402
from luma_appkit.tv_media import (  # noqa: E402
    TvCover, TvEqualizer, TvNowPlayingPill, TvProgress, TvRoom, TvSlide, TvTransportButton, hues_for,
)
from luma_appkit.tv_parts import TvButton, TvField, TvTab, TvTopBar  # noqa: E402
from luma_appkit.tv_stage import TvStage, add_tv_style, tv_fade  # noqa: E402

from .presentation import Album, Playlist, Song, duration_text, next_queue, search_library  # noqa: E402
from .ui_parts import image_path  # noqa: E402

TV = tokens.TV
PLACES = (("listen", "Listen now", None), ("albums", "Albums", None), ("playlists", "Playlists", None),
          ("search", "Search", "search"))
GRID_COLUMNS = 6
# six covers and five 36-unit gaps fill the 1728-unit safe width
GRID_COVER = (1920 - 2 * 96 - 5 * 36) / 6
GRID_BATCH_ROWS = 4
SHELF_LIMIT = 12
SEARCH_LIMIT = 8
PLAY_KEYS = frozenset({Gdk.KEY_space, Gdk.KEY_AudioPlay, Gdk.KEY_AudioPause})
IDLE_SECONDS = TV.get("idle", {}).get("media_seconds", 15)


def _sheet(_opaque: bool = False) -> str:
    m = TV["materials"]
    return f"""
.tide-tv-pad {{ padding: 40tv 96tv 80tv; border-spacing: 0; }}
.tide-tv-hero {{ border-spacing: 48tv; margin-bottom: 60tv; }}
.tide-tv-kicker {{ font-size: 22tv; font-weight: 650; letter-spacing: 1.32tv; color: {m['ink_3']}; }}
.tide-tv-hero-title {{ font-size: 72tv; font-weight: 750; letter-spacing: -2.88tv; margin-top: 8tv; }}
.tide-tv-hero-line {{ font-size: 28tv; color: {m['ink_2']}; margin-top: 14tv; }}
.tide-tv-acts {{ border-spacing: 16tv; margin-top: 30tv; }}
.tide-tv-section {{ margin-bottom: 22tv; }}
.tide-tv-shelf-row {{ border-spacing: 36tv; }}
.tide-tv-shelf {{ margin-bottom: 56tv; }}
.tide-tv-grid {{ border-spacing: 36tv 44tv; }}
.tide-tv-collection {{ border-spacing: 72tv; }}
.tide-tv-side-title {{ margin-top: 34tv; }}
.tide-tv-by {{ font-size: 28tv; color: {m['ink_2']}; margin-top: 8tv; }}
.tide-tv-meta {{ font-size: 22tv; color: {m['ink_3']}; margin-top: 6tv; }}
.tide-tv-note {{ font-size: 25tv; color: {m['ink_2']}; }}
.tide-tv-main {{ border-spacing: 26tv; }}
.tide-tv-rows {{ border-spacing: 4tv; padding: 6tv; }}
.tide-tv-row {{ min-height: 70tv; padding: 0 24tv; border-radius: 20tv; font-size: 26tv; border-spacing: 18tv; }}
.tide-tv-row .tide-tv-num {{ min-width: 44tv; color: {m['ink_3']}; font-feature-settings: "tnum"; }}
.tide-tv-row .tide-tv-song {{ font-weight: 600; }}
.tide-tv-row .tide-tv-dim {{ font-size: 22tv; color: {m['ink_3']}; font-feature-settings: "tnum"; }}
.tide-tv-row.tv-key .tide-tv-num, .tide-tv-row.tv-key .tide-tv-dim {{ color: alpha({m['key_ink']}, .6); }}
.tide-tv-np {{ border-spacing: 90tv; }}
.tide-tv-np-title {{ font-size: 66tv; font-weight: 750; letter-spacing: -2.31tv; }}
.tide-tv-np-by {{ font-size: 32tv; color: {m['ink_2']}; margin-top: 12tv; }}
.tide-tv-track {{ margin-top: 54tv; }}
.tide-tv-times {{ margin-top: 12tv; font-size: 21tv; font-weight: 600; color: {m['ink_3']}; font-feature-settings: "tnum"; }}
.tide-tv-controls {{ border-spacing: 22tv; margin-top: 40tv; }}
.tide-tv-next {{ margin-top: 46tv; font-size: 22tv; color: {m['ink_3']}; }}
.tide-tv-idle-title {{ font-size: 52tv; font-weight: 750; letter-spacing: -1.56tv; margin-top: 52tv; }}
.tide-tv-idle-by {{ font-size: 28tv; color: {m['ink_2']}; margin-top: 10tv; }}
.tide-tv-idle-line {{ margin-top: 34tv; }}
.tide-tv-search {{ border-spacing: 44tv; }}
.tide-tv-search .tv-field {{ min-width: 900tv; min-height: 88tv; border-radius: 28tv; padding: 0 32tv; font-size: 32tv; }}
.tide-tv-search .tv-field image {{ -gtk-icon-size: 34tv; margin-right: 18tv; }}
.tide-tv-results {{ padding: 0 96tv 80tv; }}
.tide-tv-empty {{ font-size: 28tv; color: {m['ink_3']}; }}
"""


def _box(vertical: bool = False, *css: str, **props) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, **props)
    for name in css:
        box.add_css_class(name)
    return box


def _label(text: str, *css: str, wrap: bool = False, lines: int = 0, **props) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=props.pop("xalign", 0), **props)
    for name in css:
        label.add_css_class(name)
    if wrap:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if lines:
            label.set_lines(lines)
            label.set_ellipsize(Pango.EllipsizeMode.END)
    else:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_single_line_mode(True)
    return label


def _art(album: Album | None) -> str | None:
    if album is None:
        return None
    path = image_path(album.artwork)
    return str(path) if path is not None and path.is_file() else None


def _hues(album: Album) -> tuple[int, int, int]:
    return album.hues or hues_for(album.title + "\0" + album.artist)


class SongRow(Gtk.Box):
    """A song in an album, playlist or search: number (or the equaliser while it plays), title, length."""

    def __init__(self, number: int, song: Song, detail: str = "") -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.BUTTON)
        self.add_css_class("tide-tv-row")
        self.song = song
        self.number = _label(str(number), "tide-tv-num")
        self.equalizer = TvEqualizer(height=20)
        self.slot = Gtk.Stack(hhomogeneous=False)
        self.slot.add_css_class("tide-tv-num")
        self.slot.add_named(self.number, "number")
        self.slot.add_named(self.equalizer, "playing")
        self.append(self.slot)
        words = _box(False, hexpand=True)
        self.title = _label(song.title, "tide-tv-song", hexpand=detail == "")
        words.append(self.title)
        if detail:
            words.append(_label(" · " + detail, "tide-tv-dim", hexpand=True))
        self.append(words)
        self.append(_label(duration_text(song.duration), "tide-tv-dim"))
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{song.title}, {duration_text(song.duration)}"])

    def mark(self, current: bool, playing: bool) -> None:
        for widget in (self.title, self.number):
            (widget.add_css_class if current else widget.remove_css_class)("tv-playing")
        self.equalizer.add_css_class("tv-playing")
        self.slot.set_visible_child_name("playing" if current and playing else "number")
        self.equalizer.set_playing(current and playing)

    def set_tv_focus(self, on: bool) -> None:
        (self.add_css_class if on else self.remove_css_class)("tv-key")


class TideTV:
    """The TV presentation of one TideWindow (see the module docstring)."""

    def __init__(self, window) -> None:
        self.window = window
        self.source = window.source
        add_tv_style(_sheet)
        self.stage = TvStage()
        self.stage.add_css_class("tide-tv")
        self.room = TvRoom()
        self.stage.place(self.room, fill=True)
        self.page: Gtk.Widget | None = None
        self.bar = TvTopBar()
        self.tabs = [TvTab(title, icon=icon) for _key, title, icon in PLACES]
        self.bar.set_tabs(self.tabs)
        self.bar.track.set_hexpand(False)  # the track keeps its natural width, as on the TV home
        self.pill = TvNowPlayingPill()
        self.bar.end.prepend(self.pill)
        self.stage.place(self.bar, **TvTopBar.PIN)
        self.calm = self._calm_layer()
        self.calm.set_opacity(0)
        self.calm.set_can_target(False)
        self.stage.place(self.calm, fill=True)
        self.focus = TvFocus(self.stage, on_back=self.back, on_change=self._focus_changed)
        self.focus.add_filter(self._filter)
        for index, (key, _title, _icon) in enumerate(PLACES):
            self.focus.add(self.tabs[index], group="places", on_focus=lambda k=key: self.open_place(k),
                           on_activate=lambda k=key: self._enter_page(k), on_key=self._along_bar)
        self.focus.set_group_entry("places", lambda: self.tabs[self._place_index()])
        self.focus.add(self.pill, group="deck", on_activate=self.open_now, on_key=self._along_bar)
        # where we are: a place, then (album/playlist/artist) and Now playing above it
        self.place = "listen"
        self.stack: list[dict] = []
        self.view: dict = {"kind": "listen"}
        self.query = ""
        self.idle = False
        self._np = {}
        self._rows: list[SongRow] = []
        self._results: list[Gtk.Widget] = []
        self._page_items: list[Gtk.Widget] = []
        self._grid_state = None
        self._field = None
        self._idle_release = None
        # idle: after 15 s on Now playing while music plays, the record alone (LumaUI's idle hook)
        self.idle_hook = TvIdle(self.focus, seconds=IDLE_SECONDS, on_idle=self._enter_idle, on_wake=self._leave_idle)
        self.render()
        self.focus.focus(self.tabs[0])

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def library(self):
        return self.window.library

    @property
    def player(self):
        return self.window.player

    def _place_index(self) -> int:
        return next(i for i, (key, _t, _i) in enumerate(PLACES) if key == self.place)

    def playing_album(self) -> tuple[Album, Song] | None:
        return self.library.find_song(self.player.song_id)

    # ── data changes from the window ─────────────────────────────────────────

    def library_changed(self) -> None:
        self.render(keep_focus=True)

    def player_changed(self, player, previous) -> None:
        self._update_pill()
        changed = (player.song_id, player.playing) != (previous.song_id, previous.playing)
        kind = self.view["kind"]
        if kind == "now":
            if player.song_id != previous.song_id or player.queue != previous.queue:
                self.render(keep_focus=True)
            else:
                self._update_now()
        elif changed:
            for row in self._rows:
                row.mark(row.song.id == player.song_id, player.playing)
            if kind == "listen":
                self.render(keep_focus=True)
        if self.idle:
            if player.song_id:
                self._update_calm()
            else:
                self.idle_hook.wake()
        self._sync_idle()

    # ── the bar ──────────────────────────────────────────────────────────────

    def _update_pill(self) -> None:
        subject = self.playing_album()
        if subject is None or self.view["kind"] == "now":
            self.pill.show_track(None)
            if subject is None and self.focus.focused is self.pill:
                self.focus.focus(self.tabs[self._place_index()])
            return
        album, song = subject
        self.pill.show_track(song.title, album.artist, _art(album), playing=self.player.playing, hues=_hues(album))

    def open_place(self, key: str) -> None:
        """Moving along the bar opens the place, no OK needed (and leaves any album or Now playing)."""
        if key == self.place and not self.stack:
            return
        self.place, self.stack, self.view = key, [], {"kind": key}
        self.render()

    def _along_bar(self, direction: str) -> bool:
        """Left and Right on the bar move along the bar (tabs, then the pill), never down into the page."""
        if direction == "down":
            # into the page: the nearest item below, or the page's first while it is still being laid out
            if not self.focus.move("down") or self.focus.group_of(self.focus.focused) != "page":
                first = self._first_page_item()
                if first is not None and first.get_mapped():
                    self.focus.focus(first)
            return True
        if direction not in ("left", "right"):
            return False
        items = self.tabs + ([self.pill] if self.pill.get_visible() else [])
        index = items.index(self.focus.focused) + (1 if direction == "right" else -1)
        if 0 <= index < len(items):
            self.focus.focus(items[index])
        return True

    def _enter_page(self, key: str) -> None:
        self.open_place(key)
        first = self._first_page_item()
        if first is not None:
            self.focus.focus(first)

    # ── navigation ───────────────────────────────────────────────────────────

    def push(self, view: dict) -> None:
        self.stack.append({"view": self.view, "focus": self._focus_key()})
        self.view = view
        self.render()
        first = self._first_page_item(view)
        if first is not None:
            self.focus.focus(first)

    def back(self) -> bool:
        """Back: out of Now playing or an album (level by level), then to the bar, then the TV home."""
        if self.stack:
            entry = self.stack.pop()
            self.view = entry["view"]
            self.render()
            target = self._find_focus_key(entry["focus"]) or self._first_page_item()
            if target is not None:
                self.focus.focus(target)
            return True
        if self.focus.group_of(self.focus.focused) != "places":
            self.focus.focus(self.tabs[self._place_index()])
            return True
        return False

    def open_now(self, *, starting: bool = False) -> None:
        """Now playing; `starting` opens it for playback just asked for (the player catches up)."""
        if self.view["kind"] == "now" or (not starting and self.playing_album() is None):
            return
        self.push({"kind": "now"})

    def open_album(self, album: Album) -> None:
        self.push({"kind": "album", "album": album.id})

    def open_playlist(self, playlist: Playlist) -> None:
        self.push({"kind": "playlist", "playlist": playlist.id})

    def open_artist(self, artist: str) -> None:
        self.push({"kind": "artist", "artist": artist})

    def _focus_key(self):
        widget = self.focus.focused
        return getattr(widget, "tide_key", None) if widget is not None else None

    def _find_focus_key(self, key):
        if key is None or self.page is None:
            return None
        found = []

        def walk(widget):
            if getattr(widget, "tide_key", None) == key and widget.get_mapped():
                found.append(widget)
                return
            child = widget.get_first_child()
            while child is not None and not found:
                walk(child)
                child = child.get_next_sibling()
        walk(self.page)
        return found[0] if found else None

    def _first_page_item(self, view: dict | None = None):
        kind = (view or self.view)["kind"]
        preferred = getattr(self, "_preferred", None)
        if preferred is not None and preferred.get_mapped():
            return preferred
        if kind == "search" and self._field is not None:
            return self._field.text
        return getattr(self, "_first", None)

    # ── focus and the room ───────────────────────────────────────────────────

    def _focus_changed(self, widget) -> None:
        if widget is None:
            return
        if not widget.get_focusable():
            self.window.set_focus(None)  # the field (and the on-screen keyboard) let go when the remote leaves it
        node = widget.get_parent()
        while node is not None and node is not self.stage:
            if isinstance(node, TvSlide):
                node.reveal(widget)
            node = node.get_parent()
        self._room_for(widget)

    def _room_for(self, widget=None) -> None:
        album = getattr(widget, "tide_album", None) if widget is not None else None
        hues = getattr(widget, "tide_hues", None) if widget is not None else None
        if album is None and hues is None:
            album, hues = self._page_room()
        if album is not None:
            art = _art(album)
            if art:
                self.room.set_artwork(art)
            else:
                self.room.set_artwork(hues=_hues(album))
        elif hues is not None:
            self.room.set_artwork(hues=hues)

    def _page_room(self):
        kind = self.view["kind"]
        if kind == "album":
            return self._album(self.view["album"]), None
        if kind == "playlist":
            playlist = self._playlist(self.view["playlist"])
            return None, (hues_for(playlist.name) if playlist else None)
        if kind == "artist":
            albums = self._artist_albums(self.view["artist"])
            return (albums[0] if albums else None), None
        subject = self.playing_album()
        if subject is not None:
            return subject[0], None
        return (self.library.albums[0] if self.library.albums else None), None

    # ── keys: play/pause anywhere, and waking from idle ──────────────────────

    def _filter(self, keyval: int, state) -> bool:
        typing = self._field is not None and self.focus.focused is self._field.text
        if keyval in PLAY_KEYS and not (typing and keyval == Gdk.KEY_space):
            if state & Gtk.accelerator_get_default_mod_mask():
                return False
            if self.player.song_id:
                self.source.toggle()
            return True
        if self.view["kind"] == "search" and not typing and self._field is not None:
            char = Gdk.keyval_to_unicode(keyval)
            if char >= 0x20 and char != 0x7f and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
                # a real keyboard types straight into the field from anywhere on Search
                self.focus.focus(self._field.text)
                self._field.set_value(self._field.get_value() + chr(char))
                return True
        return False

    # ── playing ──────────────────────────────────────────────────────────────

    def play_album(self, album: Album, song: Song | None = None, shuffle: bool = False) -> None:
        current = self.playing_album()
        if song is None and not shuffle and current is not None and current[0].id == album.id:
            self.source.toggle()
            return
        self.source.play(album, song, shuffle)
        self.open_now(starting=True)

    def play_ids(self, ids, start: int = 0, shuffle: bool = False) -> None:
        if not ids:
            return
        if hasattr(self.source, "play_ids"):
            self.source.play_ids(ids, start, shuffle)
        else:
            subject = self.library.find_song(ids[start])
            if subject:
                self.source.play(subject[0], subject[1], shuffle)
        self.open_now(starting=True)

    # ── lookups ──────────────────────────────────────────────────────────────

    def _album(self, identifier):
        return next((a for a in self.library.albums if a.id == identifier), None)

    def _playlist(self, identifier):
        return next((p for p in self.library.playlists if p.id == identifier), None)

    def _artist_albums(self, artist: str):
        return tuple(a for a in self.library.albums if a.artist == artist)

    def _songs(self, ids):
        out = []
        for identifier in ids:
            subject = self.library.find_song(identifier)
            if subject is not None:
                out.append(subject)
        return out

    # ── drawing ──────────────────────────────────────────────────────────────

    def render(self, keep_focus: bool = False) -> None:
        key = self._focus_key() if keep_focus else None
        was_bar = self.focus.group_of(self.focus.focused) in ("places", "deck")
        self.focus.clear("page")
        self._rows, self._first, self._preferred, self._grid_state = [], None, None, None
        self._field, self._results, self._page_items = None, [], []
        self._np = {}
        if self.page is not None:
            self.stage.remove(self.page)
            self.page = None
        kind = self.view["kind"]
        builder = {"listen": self._listen, "albums": self._albums, "playlists": self._playlists,
                   "search": self._search, "album": self._collection, "playlist": self._collection,
                   "artist": self._collection, "now": self._now}[kind]
        page, pin = builder()
        self.page = page
        self.stage.place(page, **pin)
        self.stage.raise_child(self.bar)
        self.stage.raise_child(self.calm)
        self.bar.set_open(self._place_index())
        self._update_pill()
        if keep_focus and not was_bar:
            target = self._find_focus_key(key) or self._first_page_item()
            if target is not None:
                self.focus.focus(target)
        self._room_for(self.focus.focused if self.focus.group_of(self.focus.focused) == "page" else None)
        self._sync_idle()

    def _add(self, widget, key, on_activate, *, album: Album | None = None, hues=None, first: bool = False):
        widget.tide_key = key
        if album is not None:
            widget.tide_album = album
        if hues is not None:
            widget.tide_hues = hues
        self.focus.add(widget, group="page", on_activate=on_activate)
        self._page_items.append(widget)
        if first or self._first is None:
            self._first = widget
        return widget

    def _cover(self, album: Album, key: str, *, width=None, caption=True, on_activate=None, first=False):
        cover = TvCover(width=width, artwork=_art(album), hues=_hues(album),
                        title=album.title if caption else None, subtitle=album.artist if caption else None,
                        accessible=f"{album.title}, {album.artist}")
        return self._add(cover, key, on_activate or (lambda a=album: self.open_album(a)), album=album, first=first)

    def _playlist_cover(self, playlist: Playlist, key: str, *, caption=True, width=None):
        hues = hues_for(playlist.name)
        count = len(playlist.song_ids)
        cover = TvCover(width=width, hues=hues, name=playlist.name, title=playlist.name if caption else None,
                        subtitle=f"{count} song{'s' if count != 1 else ''}" if caption else None)
        return self._add(cover, key, lambda p=playlist: self.open_playlist(p), hues=hues)

    def _page_scroll(self, content: Gtk.Widget):
        slide = TvSlide(content, orientation=Gtk.Orientation.VERTICAL, lead=40)
        return slide, {"left": 0, "right": 0, "top": 150, "bottom": 0}

    def _shelf(self, title: str, widgets) -> Gtk.Widget:
        section = _box(True, "tide-tv-shelf")
        section.append(_label(title, "tv-h2", "tide-tv-section"))
        row = _box(False, "tide-tv-shelf-row")
        for widget in widgets:
            row.append(widget)
        section.append(TvSlide(row, orientation=Gtk.Orientation.HORIZONTAL, clip=False, peek=120))
        return section

    # Listen now
    def _listen(self):
        pad = _box(True, "tide-tv-pad")
        albums = self.library.albums
        if not albums:
            pad.append(_label("Your music will be here. Add music to Tide on a computer, or connect a server.",
                              "tide-tv-empty", wrap=True))
            return self._page_scroll(pad)
        subject = self.playing_album()
        played = [self._album(i) for i in self.library.played]
        played = [a for a in played if a is not None]
        hero = subject[0] if subject else (played[0] if played else albums[0])
        pad.append(self._hero(hero, playing=subject is not None and subject[0].id == hero.id))
        recent = [a for a in played if a.id != hero.id][:SHELF_LIMIT]
        if recent:
            pad.append(self._shelf("Recently played", [self._cover(a, f"recent:{a.id}") for a in recent]))
        if self.library.playlists:
            pad.append(self._shelf("Your playlists", [self._playlist_cover(p, f"pl:{p.id}")
                                                      for p in self.library.playlists[:SHELF_LIMIT]]))
        seen = {hero.id} | {a.id for a in recent}
        rest = [a for a in albums if a.id not in seen][:SHELF_LIMIT]
        if rest:
            pad.append(self._shelf("From your library", [self._cover(a, f"lib:{a.id}") for a in rest]))
        return self._page_scroll(pad)

    def _hero(self, album: Album, *, playing: bool) -> Gtk.Widget:
        hero = _box(False, "tide-tv-hero")
        cover = TvCover(width=340, artwork=_art(album), hues=_hues(album), accessible=f"{album.title}, {album.artist}")
        self._add(cover, f"hero:{album.id}", lambda: self.open_album(album), album=album)
        cover.set_valign(Gtk.Align.CENTER)
        hero.append(cover)
        words = _box(True, valign=Gtk.Align.CENTER, hexpand=True)
        words.append(_label(("Playing" if playing else "Pick up where you left off").upper(), "tide-tv-kicker"))
        words.append(_label(album.title, "tide-tv-hero-title"))
        line = album.artist + (f" · {album.year}" if album.year else "") + (f". {album.note}" if album.note else "")
        words.append(_label(line, "tide-tv-hero-line", wrap=True, lines=2, max_width_chars=60))
        acts = _box(False, "tide-tv-acts")
        current = self.playing_album()
        here = current is not None and current[0].id == album.id
        label = ("Pause" if self.player.playing else "Resume") if here else "Play"
        play = TvButton(label, icon="pause" if here and self.player.playing else "play")
        acts.append(self._add(play, f"heroplay:{album.id}", lambda: self.play_album(album), album=album, first=True))
        acts.append(self._add(TvButton("Shuffle", icon="shuffle"), f"heroshuffle:{album.id}",
                              lambda: self.play_album(album, shuffle=True), album=album))
        acts.append(self._add(TvButton("Songs", icon="list"), f"herosongs:{album.id}",
                              lambda: self.open_album(album), album=album))
        words.append(acts)
        hero.append(words)
        self._preferred = play
        return hero

    # Albums and Playlists
    def _albums(self):
        grid = Gtk.Grid()
        grid.add_css_class("tide-tv-grid")
        pad = _box(True, "tide-tv-pad")
        pad.append(grid)
        albums = self.library.albums
        if not albums:
            pad.append(_label("No albums yet.", "tide-tv-empty"))
        self._grid_state = {"grid": grid, "items": albums, "next": 0}
        self._grid_more(GRID_BATCH_ROWS * 2)
        return self._page_scroll(pad)

    def _grid_more(self, rows: int) -> None:
        state = self._grid_state
        if state is None:
            return
        start = state["next"]
        end = min(len(state["items"]), start + rows * GRID_COLUMNS)
        for index in range(start, end):
            album = state["items"][index]
            cover = self._cover(album, f"al:{album.id}", width=GRID_COVER)
            self.focus.remove(cover)
            self.focus.add(cover, group="page", on_activate=lambda a=album: self.open_album(a),
                           on_focus=lambda i=index: self._grid_focused(i))
            state["grid"].attach(cover, index % GRID_COLUMNS, index // GRID_COLUMNS, 1, 1)
        state["next"] = end

    def _grid_focused(self, index: int) -> None:
        state = self._grid_state
        if state is not None and index >= state["next"] - GRID_COLUMNS * 2:
            self._grid_more(GRID_BATCH_ROWS)

    def _playlists(self):
        pad = _box(True, "tide-tv-pad")
        if not self.library.playlists:
            pad.append(_label("No playlists yet. Playlists you make in Tide on a computer show here.",
                              "tide-tv-empty", wrap=True))
            return self._page_scroll(pad)
        grid = Gtk.Grid()
        grid.add_css_class("tide-tv-grid")
        for index, playlist in enumerate(self.library.playlists):
            cover = self._playlist_cover(playlist, f"pl:{playlist.id}", width=GRID_COVER)
            grid.attach(cover, index % GRID_COLUMNS, index // GRID_COLUMNS, 1, 1)
        pad.append(grid)
        return self._page_scroll(pad)

    # An album, a playlist or an artist: the sleeve on the left, the songs on the right
    def _collection(self):
        kind = self.view["kind"]
        if kind == "album":
            album = self._album(self.view["album"])
            if album is None:
                return self._missing()
            songs = [(album, s) for s in album.songs]
            sleeve = TvCover(width=520, artwork=_art(album), hues=_hues(album), lift=False,
                             accessible=f"{album.title}, {album.artist}")
            title, by = album.title, album.artist
            count = len(album.songs)
            meta = " · ".join(x for x in (str(album.year) if album.year else "", album.label,
                                          f"{count} song{'s' if count != 1 else ''}") if x)
            note, detail = album.note, False
        elif kind == "playlist":
            playlist = self._playlist(self.view["playlist"])
            if playlist is None:
                return self._missing()
            songs = self._songs(playlist.song_ids)
            sleeve = TvCover(width=520, hues=hues_for(playlist.name), name=playlist.name, lift=False)
            title, by, meta, note, detail = playlist.name, "Playlist", f"{len(songs)} songs", "", True
        else:
            artist = self.view["artist"]
            albums = self._artist_albums(artist)
            songs = [(a, s) for a in albums for s in a.songs]
            first = albums[0] if albums else None
            sleeve = TvCover(width=520, artwork=_art(first), hues=_hues(first) if first else hues_for(artist),
                             lift=False)
            title, by = artist, "Artist"
            meta = f"{len(albums)} album{'s' if len(albums) != 1 else ''} · {len(songs)} songs"
            note, detail = "", True
        layout = _box(False, "tide-tv-collection")
        side = _box(True)
        side.append(sleeve)
        side.append(_label(title, "tv-h1", "tide-tv-side-title", wrap=True, lines=2))
        side.append(_label(by, "tide-tv-by"))
        side.append(_label(meta, "tide-tv-meta"))
        layout.append(side)
        main = _box(True, "tide-tv-main", hexpand=True)
        head = _box(True)
        if note:
            head.append(_label(note, "tide-tv-note", wrap=True, lines=3))
        acts = _box(False, "tide-tv-acts")
        ids = [s.id for _a, s in songs]
        play = TvButton("Play", icon="play")
        play.add_css_class("tide-tv-play")
        acts.append(self._add(play, "playall", lambda: self.play_ids(ids), first=True))
        acts.append(self._add(TvButton("Shuffle", icon="shuffle"), "shuffleall",
                              lambda: self.play_ids(ids, shuffle=True)))
        head.append(acts)
        main.append(head)
        rows = _box(True, "tide-tv-rows")
        for index, (album, song) in enumerate(songs):
            row = SongRow(song.number if kind == "album" else index + 1, song, album.artist if detail else "")
            row.mark(song.id == self.player.song_id, self.player.playing)
            self._rows.append(row)
            self._add(row, f"song:{index}", lambda i=index: self.play_ids(ids, i))
            rows.append(row)
        if not songs:
            rows.append(_label("Nothing to play here yet.", "tide-tv-note"))
        main.append(TvSlide(rows, orientation=Gtk.Orientation.VERTICAL, lead=140))
        layout.append(main)
        self._preferred = play
        return layout, {"left": 96, "right": 96, "top": 190, "bottom": 56}

    def _missing(self):
        box = _box(True)
        box.append(_label("This isn’t in your library any more.", "tide-tv-empty"))
        return box, {"left": 96, "right": 96, "top": 190}

    # Now playing
    def _now(self):
        subject = self.playing_album()
        layout = _box(False, "tide-tv-np", valign=Gtk.Align.CENTER)
        if subject is None:
            layout.append(_label("Nothing is playing.", "tide-tv-empty"))
            return layout, {"left": 140, "right": 140, "top": 150, "bottom": 110}
        album, song = subject
        sleeve = TvCover(width=640, radius=28, artwork=_art(album), hues=_hues(album), lift=False)
        sleeve.set_valign(Gtk.Align.CENTER)
        layout.append(sleeve)
        info = _box(True, valign=Gtk.Align.CENTER, hexpand=True)
        title = _label(song.title, "tide-tv-np-title", wrap=True, lines=2)
        by = _label(f"{album.artist} · {album.title}", "tide-tv-np-by")
        info.append(title)
        info.append(by)
        track = _box(True, "tide-tv-track")
        progress = TvProgress()
        track.append(progress)
        times = _box(False, "tide-tv-times")
        elapsed, remaining = _label("0:00", hexpand=True), _label("", xalign=1)
        times.append(elapsed)
        times.append(remaining)
        track.append(times)
        info.append(track)
        controls = _box(False, "tide-tv-controls")
        previous = TvTransportButton("skip-back", label="Previous")
        toggle = TvTransportButton("pause" if self.player.playing else "play", main=True,
                                   label="Pause" if self.player.playing else "Play")
        following = TvTransportButton("skip-forward", label="Next")
        for widget, key, action in ((previous, "prev", lambda: self.source.step(-1)),
                                    (toggle, "toggle", self.source.toggle),
                                    (following, "next", lambda: self.source.step(1))):
            controls.append(self._add(widget, key, action))
        info.append(controls)
        upcoming = next_queue(self.player)[1:]
        nxt = self.library.find_song(upcoming[0]) if upcoming else None
        line = _label("", "tide-tv-next", use_markup=True)
        if nxt is not None:
            line.set_markup(f"Up next · <span weight='600' foreground='#ffffff' alpha='74%'>"
                            f"{GLib.markup_escape_text(nxt[1].title)}</span>, {GLib.markup_escape_text(nxt[0].artist)}")
        info.append(line)
        layout.append(info)
        self._np = {"progress": progress, "elapsed": elapsed, "remaining": remaining, "toggle": toggle,
                    "duration": song.duration}
        self._preferred = toggle
        self._update_now()
        return layout, {"left": 140, "right": 140, "top": 150, "bottom": 110}

    def _update_now(self) -> None:
        np = self._np
        if not np:
            return
        duration = max(1, np["duration"])
        position = min(self.player.position, duration)
        np["progress"].set_fraction(position / duration)
        np["elapsed"].set_label(duration_text(position))
        np["remaining"].set_label("-" + duration_text(duration - position))
        playing = self.player.playing
        np["toggle"].set_icon("pause" if playing else "play", "Pause" if playing else "Play")

    # Search: albums, artists and songs; the system's TV keyboard presents itself for the field
    def _search(self):
        page = _box(True, "tide-tv-search")
        field = TvField(placeholder="Albums, artists, songs", icon="search")
        field.set_halign(Gtk.Align.CENTER)
        field.set_value(self.query)
        field.text.connect("changed", lambda text: self._search_changed(text.get_text()))
        field.text.tide_key = "field"
        self.focus.add(field.text, group="page", editable=True, on_activate=self._search_done)
        self._field = field
        page.append(field)
        self._results_box = _box(True, "tide-tv-results")
        page.append(TvSlide(self._results_box, orientation=Gtk.Orientation.VERTICAL, lead=40))
        self._fill_results()
        return page, {"left": 0, "right": 0, "top": 190, "bottom": 0}

    def _search_done(self) -> None:
        if self._results:
            self.focus.focus(self._results[0])

    def _search_changed(self, query: str) -> None:
        if query != self.query:
            self.query = query
            self._fill_results()

    def _fill_results(self) -> None:
        box = getattr(self, "_results_box", None)
        if box is None:
            return
        for widget in self._results:
            self.focus.remove(widget)
        self._results = []
        while child := box.get_first_child():
            box.remove(child)
        self._rows = []
        query = self.query.strip()
        if not query:
            return
        before = set(self._registered())
        artists, albums, songs = search_library(self.library, query)
        if not (artists or albums or songs):
            box.append(_label(f"Nothing in your library matches “{query}”", "tide-tv-empty", xalign=0.5))
            return
        if albums:
            box.append(self._shelf("Albums", [self._cover(album, f"res:{album.id}", width=230)
                                              for album in albums[:SEARCH_LIMIT]]))
        if artists:
            widgets = []
            for name, releases in artists[:SEARCH_LIMIT]:
                cover = TvCover(width=230, artwork=_art(releases[0]), hues=_hues(releases[0]), title=name,
                                subtitle=f"{len(releases)} album{'s' if len(releases) != 1 else ''}")
                widgets.append(self._add(cover, f"artist:{name}", lambda n=name: self.open_artist(n),
                                         album=releases[0]))
            box.append(self._shelf("Artists", widgets))
        if songs:
            section = _box(True, "tide-tv-shelf")
            section.append(_label("Songs", "tv-h2", "tide-tv-section"))
            rows = _box(True, "tide-tv-rows")
            for index, (album, song) in enumerate(songs[:SEARCH_LIMIT]):
                row = SongRow(index + 1, song, album.artist)
                row.mark(song.id == self.player.song_id, self.player.playing)
                self._rows.append(row)
                rows.append(self._add(row, f"ressong:{song.id}", lambda a=album, s=song: self.play_album(a, s),
                                      album=album))
            section.append(rows)
            box.append(section)
        self._results = [w for w in self._registered() if w not in before]

    def _registered(self):
        return list(self._page_items)

    # ── idle: the record alone ───────────────────────────────────────────────

    def _calm_layer(self) -> Gtk.Widget:
        column = _box(True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self._calm_sleeve = TvCover(width=560, radius=28, lift=False)
        self._calm_sleeve.set_halign(Gtk.Align.CENTER)
        column.append(self._calm_sleeve)
        self._calm_title = _label("", "tide-tv-idle-title", xalign=0.5, justify=Gtk.Justification.CENTER)
        self._calm_by = _label("", "tide-tv-idle-by", xalign=0.5)
        column.append(self._calm_title)
        column.append(self._calm_by)
        self._calm_line = TvProgress(hairline=True)
        self._calm_line.add_css_class("tide-tv-idle-line")
        self._calm_line.set_halign(Gtk.Align.CENTER)
        column.append(self._calm_line)
        holder = Gtk.Box(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        column.set_hexpand(True)
        holder.append(column)
        return holder

    def _update_calm(self) -> None:
        subject = self.playing_album()
        if subject is None:
            return
        album, song = subject
        self._calm_sleeve.set_artwork(_art(album), _hues(album))
        self._calm_title.set_label(song.title)
        self._calm_by.set_label(f"{album.artist} · {album.title}")
        self._calm_line.set_size_request(int(560 * self.stage.unit), -1)
        self._calm_line.set_fraction(min(1.0, self.player.position / max(1, song.duration)))

    def idle_ready(self) -> bool:
        """The idle view applies: music playing, on Now playing, not typing."""
        return self.view["kind"] == "now" and bool(self.player.playing)

    def _sync_idle(self) -> None:
        """Hold the idle hook while the idle view doesn't apply; let it count down when it does."""
        if self.idle:
            return
        if self.idle_ready():
            if self._idle_release is not None:
                self._idle_release()
                self._idle_release = None
        elif self._idle_release is None and not self.idle:
            self._idle_release = self.idle_hook.hold()

    def _enter_idle(self) -> None:
        """The hook went idle (or Shift+I): with nothing playing, start the record on screen first."""
        if self.playing_album() is None:
            album = self._album(self.view.get("album")) or (self.library.albums[0] if self.library.albums else None)
            if album is None:
                self.idle_hook.idle = False
                return
            self.source.play(album)
        elif not self.player.playing:
            self.source.toggle()
        self.idle = True
        if self.view["kind"] != "now":
            self.push({"kind": "now"})
        self._update_calm()
        motion = TV["motion"]
        tv_fade(self.calm, 1.0, motion.get("idle_in_ms", 1200))
        for widget in (self.bar, self.page):
            if widget is not None:
                tv_fade(widget, 0.0, motion.get("idle_fade_ms", 900))

    def _leave_idle(self) -> None:
        if not self.idle:
            return
        self.idle = False
        motion = TV["motion"]
        tv_fade(self.calm, 0.0, motion.get("idle_fade_ms", 900))
        for widget in (self.bar, self.page):
            if widget is not None:
                tv_fade(widget, 1.0, motion.get("idle_fade_ms", 900) // 2)
        self._sync_idle()

    # ── teardown ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        if self._idle_release is not None:
            self._idle_release()
            self._idle_release = None
