# SPDX-License-Identifier: Apache-2.0
"""Photos on a television: the same app, browsed from the sofa with a remote.

Design: the tv-luma/ Photos board and its handoff (9 Nov 2026). Terminal keeps
its subject on screen and hides its rail; Photos hides everything, because the
television is where photographs want to be.

* Days are rows, photographs are columns: Left/Right walk a day, Up/Down change
  day. The day you stand in grows and shows its full header; the rest go quiet.
* Reachability is the subject. A photograph whose copies are all out of reach
  stays in the grid, dimmed and badged with where it lives, and every count says
  how many. Every number is derived from the photographs' copies, never stored.
* The rail is every destination flattened: Library, smart albums, albums,
  places, "Only on <a source that is out of reach>", "Where they live".
* The viewer is full bleed and contain; its chrome leaves after four seconds.
  One write: favourite. No delete, no album editing, no adjustments.
* The slideshow skips what it cannot open, and says how many first.
* No search field: the destinations are the search.
"""
from __future__ import annotations

import threading
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

from luma_appkit import lumaui_tokens  # noqa: E402
from luma_appkit.tv_focus import TvFocus  # noqa: E402
from luma_appkit.tv_media import TvSlide  # noqa: E402
from luma_appkit.tv_parts import TvTopBar  # noqa: E402
from luma_appkit.tv_stage import TvStage, add_tv_style  # noqa: E402

from .photos_content import decode_photo  # noqa: E402
from .photos_state import source_from_environment  # noqa: E402

# Ten-foot measures and colours (TV units on a 1920x1080 stage): tv.photos in
# config/shared/design-tokens.json.
T = lumaui_tokens.TV["photos"]
SAFE_X, CONTENT_TOP = lumaui_tokens.TV["layout"]["safe_x"], T["content_top"]
DAY_TILE, QUIET_TILE, GAP = tuple(T["day_tile"]), tuple(T["quiet_tile"]), T["gap"]
GRID_COLUMNS, GRID_TILE, GRID_GAP = T["grid_columns"], tuple(T["grid_tile"]), T["grid_gap"]
RAIL_H, CHROME_SECONDS, SLIDE_SECONDS, INFO_PANEL = T["rail_height"], T["chrome_seconds"], T["slide_seconds"], T["info_panel"]
OK_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_Select)
PLAY_KEYS = (Gdk.KEY_AudioPlay, Gdk.KEY_AudioPause, Gdk.KEY_p, Gdk.KEY_P)


def _sheet(_opaque: bool) -> str:
    return f"""
.phtv {{ background-color: {T['panel']}; }}
.phtv-title {{ font-size: 40tv; font-weight: 700; color: {T['ink']}; }}
.phtv-sub {{ font-size: 24tv; color: {T['quiet']}; }}
.phtv-day-date {{ font-size: 34tv; font-weight: 700; color: {T['ink']}; }}
.phtv-day-place {{ font-size: 26tv; color: {T['ink_soft']}; }}
.phtv-quiet-date {{ font-size: 24tv; font-weight: 650; color: {T['quiet']}; }}
.phtv label.phtv-chip {{ font-size: 21tv; font-weight: 650; padding: 4tv 14tv; border-radius: 14tv;
  background-color: {T['chip']}; color: {T['warn']}; }}
.phtv-tile {{ border-radius: 16tv; }}
.phtv-tile picture {{ border-radius: 16tv; }}
.phtv-tile.phtv-gone picture {{ opacity: {T['gone_opacity']}; }}
.phtv-tile.phtv-focused {{ outline: 5tv solid {T['pick']}; outline-offset: 5tv; }}
.phtv label.phtv-badge {{ font-size: 19tv; font-weight: 700; color: {T['ink']}; background-color: {T['badge']};
  padding: 4tv 12tv; border-radius: 12tv; margin: 10tv; }}
.phtv label.phtv-star {{ font-size: 26tv; color: {T['star']}; margin: 8tv 12tv; }}
.phtv-dest {{ background-color: {T['card']}; border-radius: 22tv; padding: 16tv 22tv; min-width: 250tv; }}
.phtv-dest.phtv-focused {{ background-color: {T['card_focus']}; outline: 4tv solid {T['pick']}; outline-offset: 4tv; }}
.phtv-dest-name {{ font-size: 26tv; font-weight: 700; color: {T['ink']}; }}
.phtv-dest-sub {{ font-size: 20tv; color: {T['quiet']}; }}
.phtv-card {{ background-color: {T['card']}; border-radius: 28tv; padding: 28tv; border-spacing: 10tv; }}
.phtv-card.phtv-focused {{ background-color: {T['card_focus']}; outline: 5tv solid {T['pick']}; outline-offset: 5tv; }}
.phtv-card-name {{ font-size: 32tv; font-weight: 700; color: {T['ink']}; }}
.phtv-card-path {{ font-size: 21tv; color: {T['quiet']}; }}
.phtv label.phtv-ok {{ color: {T['ok']}; }} .phtv label.phtv-warn {{ color: {T['warn']}; }} .phtv label.phtv-off {{ color: {T['off']}; }}
.phtv-card-count {{ font-size: 24tv; color: {T['ink_2']}; }}
.phtv-black {{ background-color: {T['black']}; }}
.phtv-caption {{ background-image: linear-gradient(to top, {T['caption']}, transparent); padding: 60tv 96tv 48tv 96tv; }}
.phtv-cap-title {{ font-size: 36tv; font-weight: 700; color: {T['white']}; }}
.phtv-cap-meta {{ font-size: 24tv; color: {T['ink_2']}; }}
.phtv-cap-hint {{ font-size: 21tv; color: {T['quiet']}; }}
.phtv-panel {{ background-color: {T['info']}; padding: 40tv 96tv; border-spacing: 14tv; }}
.phtv-panel-title {{ font-size: 30tv; font-weight: 700; color: {T['ink']}; }}
.phtv-copy {{ font-size: 25tv; color: {T['copy']}; }}
.phtv-copy-gone {{ font-size: 25tv; color: {T['quiet']}; }}
.phtv-reassure {{ font-size: 23tv; color: {T['ink_soft']}; }}
.phtv-intro {{ background-color: {T['intro']}; border-radius: 36tv; padding: 40tv 48tv; border-spacing: 14tv; }}
.phtv-intro-title {{ font-size: 44tv; font-weight: 700; color: {T['white']}; }}
.phtv-intro-body {{ font-size: 26tv; color: {T['ink_2']}; }}
.phtv-hair trough {{ min-height: 4tv; background-color: {T['hair']}; border-radius: 0; }}
.phtv-hair progress {{ min-height: 4tv; background-color: {T['pick']}; border-radius: 0; }}
.phtv-empty {{ font-size: 30tv; color: {T['quiet']}; }}
"""


def local_moment(photo) -> datetime:
    """When a photograph was taken, on this TV's clock: days are the room's days,
    not UTC's (a photograph from 8 PM is tonight's, not tomorrow's)."""
    moment = photo.captured or photo.modified
    return moment.astimezone() if moment.tzinfo is not None else moment


def _day_title(moment: datetime) -> str:
    return f"{moment:%A} {moment.day} {moment:%B}"


def _short(moment: datetime) -> str:
    return f"{moment.day} {moment:%B}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class _Textures:
    """Decoded pictures, by path and size: decoded in a worker, made textures on the GTK thread."""

    def __init__(self) -> None:
        self._done: dict[tuple[str, int], Gdk.Texture] = {}
        self._waiting: dict[tuple[str, int], list] = {}

    def get(self, path, side: int, callback) -> None:
        key = (str(path), side)
        if key in self._done:
            callback(self._done[key])
            return
        if key in self._waiting:
            self._waiting[key].append(callback)
            return
        self._waiting[key] = [callback]

        def work():
            try:
                pixbuf = decode_photo(path, side)
            except Exception:  # unreadable or vanished: the tile keeps its plate
                pixbuf = None
            GLib.idle_add(done, pixbuf)

        def done(pixbuf):
            texture = Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf is not None else None
            if texture is not None:
                self._done[key] = texture
            for waiting in self._waiting.pop(key, []):
                if texture is not None:
                    waiting(texture)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, daemon=True).start()


class KenBurns(Gtk.Widget):
    """A still that is not a still screen: a slow drift and push over its time."""

    def __init__(self) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.picture.set_parent(self)
        self.t = 0.0
        self.direction = 1

    def do_size_allocate(self, width, height, baseline):
        self.picture.allocate(width, height, baseline, None)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        scale = 1.0 + 0.06 * self.t
        drift = self.direction * 0.018 * width * self.t
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2 + drift, height / 2))
        snapshot.scale(scale, scale)
        snapshot.translate(Graphene.Point().init(-width / 2, -height / 2))
        self.snapshot_child(self.picture, snapshot)
        snapshot.restore()

    def do_dispose(self):
        if self.picture is not None:
            self.picture.unparent()
            self.picture = None


class PhotoTile(Gtk.Widget):
    """A photograph in a row or a set, at exactly its tile size.

    A plain widget around the overlay (picture, badge, star), as KenBurns is: a
    picture's natural size is its pixels, and a row would otherwise give a large
    photograph all of them.
    """

    def __init__(self, tv: "PhotosTV", photo, size: tuple[int, int]) -> None:
        super().__init__()
        self.photo = photo
        self.add_css_class("phtv-tile")
        width, height = (round(v * tv.unit) for v in size)
        self._size = (width, height)
        self.overlay = Gtk.Overlay()
        self.overlay.set_parent(self)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                                   overflow=Gtk.Overflow.HIDDEN)  # clipped to its rounded corners
        self.overlay.set_child(self.picture)
        if not tv.reachable(photo):
            self.add_css_class("phtv-gone")
            badge = Gtk.Label(label="● " + tv.where(photo), halign=Gtk.Align.START, valign=Gtk.Align.END)
            badge.add_css_class("phtv-badge")
            self.overlay.add_overlay(badge)
        if photo.favorite:
            star = Gtk.Label(label="★", halign=Gtk.Align.END, valign=Gtk.Align.START)
            star.add_css_class("phtv-star")
            self.overlay.add_overlay(star)
        tv.textures.get(tv.thumbnail_path(photo), 512, self._loaded)

    def do_measure(self, orientation, _for_size):
        size = self._size[0 if orientation == Gtk.Orientation.HORIZONTAL else 1]
        return size, size, -1, -1

    def do_size_allocate(self, width, height, baseline):
        self.overlay.allocate(width, height, baseline, None)

    def do_dispose(self):
        if self.overlay is not None:
            self.overlay.unparent()
            self.overlay = None
            self.picture = None

    def _loaded(self, texture) -> None:
        if self.picture is not None:
            self.picture.set_paintable(texture)

    def set_focused(self, on: bool) -> None:
        (self.add_css_class if on else self.remove_css_class)("phtv-focused")


class PhotosTV:
    def __init__(self, window, library=None) -> None:
        self.window = window
        add_tv_style(_sheet)
        self.stage = TvStage()
        self.stage.add_css_class("phtv")
        self.textures = _Textures()
        self.source = library
        self.photos: list = []
        self.sources: dict = {}
        self.albums: list = []
        self.days: list = []
        self.dests: list = []
        self.screen = "library"     # library | set | sources | viewer | slides
        self.rail_open = False
        self.day = self.col = 0
        self.rail_index = 0
        self.set_list: list = []
        self.set_title = ""
        self.set_index = 0
        self.card_index = 0
        self.viewer = None
        self.slides = None
        self._came_from = "library"
        self._set_back = "library"
        self.bar = TvTopBar()
        self.bar.track.set_visible(False)
        head = Gtk.Box(spacing=20, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label="Photos", xalign=0)
        self.title.add_css_class("phtv-title")
        self.subtitle = Gtk.Label(xalign=0)
        self.subtitle.add_css_class("phtv-sub")
        head.append(self.title)
        head.append(self.subtitle)
        self.bar.prepend(head)
        self.stage.place(self.bar, **TvTopBar.PIN)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stage.place(self.content, left=0, right=0, top=CONTENT_TOP, bottom=0)
        self.rail = Gtk.Box(spacing=18)
        self.rail_slide = TvSlide(self.rail, orientation=Gtk.Orientation.HORIZONTAL, clip=False, peek=160)
        self.rail_slide.set_visible(False)
        self.stage.place(self.rail_slide, left=SAFE_X, right=SAFE_X, top=CONTENT_TOP, height=RAIL_H)
        self.overlay = Gtk.Box()  # the viewer and the slideshow, full bleed
        self.overlay.set_visible(False)
        self.stage.place(self.overlay, fill=True)
        self.focus = TvFocus(self.stage, on_back=self.back)
        self.focus.add_filter(self._key)
        self.stage.connect("notify::unit", lambda *_a: self.render())
        self._loading = Gtk.Label(label="Opening your library…", xalign=0)
        self._loading.add_css_class("phtv-empty")
        self.content.append(self._loading)
        threading.Thread(target=self._load, daemon=True).start()

    @property
    def unit(self) -> float:
        return self.stage.unit or 1.0

    # ── the model: every number derived from the photographs' copies ─────

    def _load(self) -> None:
        source = self.source or source_from_environment()
        photos, offset = [], 0
        while True:
            page, total = source.page(collection="all", offset=offset, limit=500)
            photos.extend(page)
            offset += len(page)
            if not page or offset >= total:
                break
        albums = []
        for album in source.albums():
            members, offset = [], 0
            while True:
                page, total = source.page(album_id=album.id, offset=offset, limit=500)
                members.extend(page)
                offset += len(page)
                if not page or offset >= total:
                    break
            albums.append((album, members))
        sources = {s.id: s for s in source.sources()}
        GLib.idle_add(self._loaded, source, photos, albums, sources)

    def _loaded(self, source, photos, albums, sources) -> bool:
        self.source = source
        self.sources = sources
        when = local_moment
        # Newest day first; within a day, in the order it happened (left to right).
        newest = sorted(photos, key=when, reverse=True)
        days: dict = {}
        for p in newest:
            days.setdefault(when(p).date(), []).append(p)
        self.days = [(when(items[0]), sorted(items, key=when)) for items in days.values()]
        self.photos = [p for _moment, items in self.days for p in items]
        self.albums = albums
        self._build_dests()
        self.render()
        return GLib.SOURCE_REMOVE

    def reachable(self, photo) -> bool:
        return photo.available

    def where(self, photo) -> str:
        """Where an out-of-reach photograph's only copies are."""
        names = [self.sources[c.source_id].name for c in photo.copies if c.source_id in self.sources]
        return names[0] if names else "Out of reach"

    def thumbnail_path(self, photo):
        """The picture a tile shows: the thumbnail Photos kept, else a reachable copy, else the record's path."""
        try:
            from .photos_backend import ThumbnailCache
            cache = ThumbnailCache(size="large")
            for copy in photo.copies:
                found = cache.lookup(copy)
                if found:
                    return found
        except Exception:
            pass
        for copy in photo.copies:
            if copy.reachable:
                return copy.path
        return photo.path

    def gone(self, items) -> int:
        return sum(not self.reachable(p) for p in items)

    def _build_dests(self) -> None:
        dests = [("Library", "All photographs", lambda: self.photos, None),
                 ("Favourites", "Smart", lambda: [p for p in self.photos if p.favorite], None),
                 ("Recents", "Smart", lambda: self.photos[:14], None)]
        for album, members in self.albums:
            ids = {m.id for m in members}
            dests.append((album.name, "Album", lambda ids=ids: [p for p in self.photos if p.id in ids], None))
        for place in dict.fromkeys(p.place for p in self.photos if p.place):
            dests.append((place, "Place", lambda pl=place: [p for p in self.photos if p.place == pl], None))
        for source in self.sources.values():
            if not source.reachable:
                only = lambda s=source: [p for p in self.photos  # noqa: E731
                                         if p.copies and all(c.source_id == s.id for c in p.copies)]
                dests.append((f"Only on {source.name}", "Not backed up anywhere else", only, None))
        dests.append(("Where they live", _plural(len(self.sources), "source"), None, "sources"))
        self.dests = dests

    # ── drawing ─────────────────────────────────────────────────────────

    def render(self) -> None:
        if not self.days and not self.photos:
            if self.source is not None:
                self._clear(self.content)
                empty = Gtk.Label(label="No photographs yet. Add some to Photos on a computer.", xalign=0)
                empty.add_css_class("phtv-empty")
                empty.set_margin_start(round(SAFE_X * self.unit))
                self.content.append(empty)
            return
        self.rail_slide.set_visible(self.rail_open)
        top = CONTENT_TOP + (RAIL_H + 30 if self.rail_open else 0)
        self.stage.place(self.content, left=0, right=0, top=top, bottom=0)
        if self.rail_open:
            self._render_rail()
        if self.screen == "library":
            self._render_library()
        elif self.screen == "set":
            self._render_set()
        elif self.screen == "sources":
            self._render_sources()

    @staticmethod
    def _clear(box) -> None:
        while child := box.get_first_child():
            box.remove(child)

    def _head(self, title: str, sub: str) -> None:
        self.title.set_label(title)
        self.subtitle.set_label(sub)

    def _render_library(self) -> None:
        n, g = len(self.photos), self.gone(self.photos)
        self._head("Photos", f"{_plural(n, 'photograph')}" + (f" · {g} out of reach" if g else ""))
        self._clear(self.content)
        u = self.unit
        first = max(0, self.day - 1)
        for index in range(first, min(len(self.days), self.day + 4)):
            moment, items = self.days[index]
            current = index == self.day
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=round(14 * u))
            row.set_margin_bottom(round(40 * u))
            header = Gtk.Box(spacing=round(16 * u))
            header.set_margin_start(round(SAFE_X * u))
            places = ", ".join(dict.fromkeys(p.place for p in items if p.place))
            if current:
                date = Gtk.Label(label=_day_title(moment), xalign=0)
                date.add_css_class("phtv-day-date")
                header.append(date)
                if places:
                    place = Gtk.Label(label=places, xalign=0)
                    place.add_css_class("phtv-day-place")
                    header.append(place)
                count = Gtk.Label(label=_plural(len(items), "photo"), xalign=0)
                count.add_css_class("phtv-day-place")
                header.append(count)
                gone = self.gone(items)
                if gone:
                    chip = Gtk.Label(label=f"● {gone} out of reach")
                    chip.add_css_class("phtv-chip")
                    header.append(chip)
            else:
                date = Gtk.Label(label=_short(moment) + (f" · {places}" if places else ""), xalign=0)
                date.add_css_class("phtv-quiet-date")
                header.append(date)
            row.append(header)
            strip = Gtk.Box(spacing=round(GAP * u))
            strip.set_margin_start(round(SAFE_X * u))
            focused = None
            for col, photo in enumerate(items):
                tile = PhotoTile(self, photo, DAY_TILE if current else QUIET_TILE)
                if current and col == self.col and not self.rail_open:
                    tile.set_focused(True)
                    focused = tile
                strip.append(tile)
            slide = TvSlide(strip, orientation=Gtk.Orientation.HORIZONTAL, clip=False, peek=200)
            row.append(slide)
            if focused is not None:
                GLib.idle_add(lambda s=slide, f=focused: (s.reveal(f, animate=False), False)[1])
            self.content.append(row)

    def _render_set(self) -> None:
        items = self.set_list
        g = self.gone(items)
        self._head(self.set_title, _plural(len(items), "photo") + (f" · {g} out of reach" if g else ""))
        self._clear(self.content)
        u = self.unit
        grid = Gtk.Grid(column_spacing=round(GRID_GAP * u), row_spacing=round(GRID_GAP * u))
        grid.set_margin_start(round(SAFE_X * u))
        grid.set_margin_top(round(10 * u))
        focused = None
        for index, photo in enumerate(items):
            tile = PhotoTile(self, photo, GRID_TILE)
            if index == self.set_index and not self.rail_open:
                tile.set_focused(True)
                focused = tile
            grid.attach(tile, index % GRID_COLUMNS, index // GRID_COLUMNS, 1, 1)
        slide = TvSlide(grid, orientation=Gtk.Orientation.VERTICAL, lead=60)
        slide.set_vexpand(True)
        self.content.append(slide)
        if focused is not None:
            GLib.idle_add(lambda: (slide.reveal(focused, animate=False), False)[1])
        if not items:
            empty = Gtk.Label(label="Nothing here yet.", xalign=0)
            empty.add_css_class("phtv-empty")
            empty.set_margin_start(round(SAFE_X * u))
            self.content.append(empty)

    def _source_counts(self, source):
        held = [p for p in self.photos if any(c.source_id == source.id for c in p.copies)]
        only = [p for p in held if all(c.source_id == source.id for c in p.copies)]
        return held, only

    def _render_sources(self) -> None:
        self._head("Where they live", _plural(len(self.sources), "source"))
        self._clear(self.content)
        u = self.unit
        row = Gtk.Box(spacing=round(24 * u), homogeneous=True)
        row.set_margin_start(round(SAFE_X * u))
        row.set_margin_end(round(SAFE_X * u))
        row.set_margin_top(round(30 * u))
        for index, source in enumerate(self.sources.values()):
            held, only = self._source_counts(source)
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            card.add_css_class("phtv-card")
            if index == self.card_index and not self.rail_open:
                card.add_css_class("phtv-focused")
            name = Gtk.Label(label=source.name, xalign=0)
            name.add_css_class("phtv-card-name")
            path = Gtk.Label(label=str(source.root), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            path.add_css_class("phtv-card-path")
            state = Gtk.Label(label="● " + ("Reachable" if source.reachable else "Out of reach"), xalign=0)
            state.add_css_class("phtv-card-count")
            state.add_css_class("phtv-ok" if source.reachable else "phtv-off")
            holds = Gtk.Label(label=f"Holds {_plural(len(held), 'photo')}", xalign=0)
            holds.add_css_class("phtv-card-count")
            alone = Gtk.Label(label=(f"{len(only)} only here" if only else "Nothing only here"), xalign=0)
            alone.add_css_class("phtv-card-count")
            if only and not source.reachable:
                alone.add_css_class("phtv-warn")
            for widget in (name, path, state, holds, alone):
                card.append(widget)
            row.append(card)
        self.content.append(row)

    def _render_rail(self) -> None:
        self._clear(self.rail)
        focused = None
        for index, (name, sub, listing, special) in enumerate(self.dests):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
            box.add_css_class("phtv-dest")
            title = Gtk.Label(label=name, xalign=0)
            title.add_css_class("phtv-dest-name")
            if listing is not None:
                items = listing()
                g = self.gone(items)
                sub = _plural(len(items), "photo") + (f" · {g} out of reach" if g else "")
            caption = Gtk.Label(label=sub, xalign=0)
            caption.add_css_class("phtv-dest-sub")
            box.append(title)
            box.append(caption)
            if index == self.rail_index:
                box.add_css_class("phtv-focused")
                focused = box
            self.rail.append(box)
        if focused is not None:
            GLib.idle_add(lambda: (self.rail_slide.reveal(focused, animate=False), False)[1])

    # ── the viewer ──────────────────────────────────────────────────────

    def open_viewer(self, items, index) -> None:
        self.viewer = {"items": items, "index": index, "panel": False, "chrome": True, "hide": 0}
        self.screen = "viewer"
        self._draw_viewer()

    def _draw_viewer(self) -> None:
        v = self.viewer
        photo = v["items"][v["index"]]
        u = self.unit
        self._clear(self.overlay)
        self.overlay.set_visible(True)
        frame = Gtk.Overlay(hexpand=True, vexpand=True)
        frame.add_css_class("phtv-black")
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        gone = not self.reachable(photo)
        if gone:
            picture.set_opacity(0.45)
            self.textures.get(self.thumbnail_path(photo), 512, picture.set_paintable)
        else:
            path = next((c.path for c in photo.copies if c.reachable), photo.path)
            self.textures.get(path, 2560, picture.set_paintable)
        stack.append(picture)
        if v["panel"]:
            stack.append(self._info_panel(photo))
        frame.set_child(stack)
        if v["chrome"] and not v["panel"]:
            frame.add_overlay(self._caption(photo, gone))
        self.overlay.append(frame)
        self._arm_chrome()

    def _caption(self, photo, gone: bool) -> Gtk.Widget:
        moment = local_moment(photo)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, spacing=6)
        box.add_css_class("phtv-caption")
        title = Gtk.Label(label=(("★  " if photo.favorite else "") + (photo.place or photo.display_name)), xalign=0)
        title.add_css_class("phtv-cap-title")
        meta = f"{_day_title(moment)} · {moment:%-I:%M %p}"
        if gone:
            meta += f" · Out of reach: only on {self.where(photo)}"
        info = Gtk.Label(label=meta, xalign=0)
        info.add_css_class("phtv-cap-meta")
        hint = Gtk.Label(label="OK " + ("remove from Favourites" if photo.favorite else "favourite")
                         + "   ·   ▲ where it lives   ·   ◀ ▶ previous / next   ·   ⏯ slideshow", xalign=0)
        hint.add_css_class("phtv-cap-hint")
        for widget in (title, info, hint):
            box.append(widget)
        return box

    def _info_panel(self, photo) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("phtv-panel")
        panel.set_size_request(-1, round(INFO_PANEL * self.unit))
        title = Gtk.Label(label="Where it lives", xalign=0)
        title.add_css_class("phtv-panel-title")
        panel.append(title)
        for copy in photo.copies:
            source = self.sources.get(copy.source_id)
            name = source.name if source else copy.source_id
            line = Gtk.Label(label=(f"●  {name}   {source.root if source else ''}" if copy.reachable
                                    else f"○  {name}   Out of reach"), xalign=0)
            line.add_css_class("phtv-copy" if copy.reachable else "phtv-copy-gone")
            panel.append(line)
        reachable = sum(c.reachable for c in photo.copies)
        if reachable == 0:
            words = (f"No copy of this photograph is reachable right now, so it cannot be opened: this is the "
                     f"thumbnail Photos kept. {self.where(photo)} has the only one.")
        elif len(photo.copies) == 1:
            words = "This is the only copy. It is safe here, and nowhere else."
        else:
            words = f"{len(photo.copies)} copies, {reachable} reachable now."
        reassure = Gtk.Label(label=words, xalign=0, wrap=True)
        reassure.add_css_class("phtv-reassure")
        panel.append(reassure)
        return panel

    def _arm_chrome(self) -> None:
        v = self.viewer
        if v is None:
            return
        if v["hide"]:
            GLib.source_remove(v["hide"])
            v["hide"] = 0
        if v["chrome"] and not v["panel"]:
            def hide():
                if self.viewer is v:
                    v["hide"] = 0
                    v["chrome"] = False
                    self._draw_viewer()
                return False
            v["hide"] = GLib.timeout_add_seconds(CHROME_SECONDS, hide)

    def close_viewer(self) -> None:
        if self.viewer and self.viewer["hide"]:
            GLib.source_remove(self.viewer["hide"])
        self.viewer = None
        self._clear(self.overlay)
        self.overlay.set_visible(False)
        self.screen = self._came_from
        self.render()

    def _favourite(self) -> None:
        v = self.viewer
        photo = v["items"][v["index"]]
        try:
            self.source.set_favorite(photo.id, not photo.favorite)
            fresh = self.source.asset(photo.id)
        except Exception:
            return
        for listing in (self.photos, v["items"], self.set_list):
            for i, p in enumerate(listing):
                if p.id == photo.id:
                    listing[i] = fresh
        self.days = [(m, [fresh if p.id == photo.id else p for p in items]) for m, items in self.days]
        v["chrome"] = True
        self._draw_viewer()
        from luma_appkit.tv_toast import TvToast
        TvToast.show(self.stage, "Added to Favourites" if fresh.favorite else "Removed from Favourites", lead="star")

    # ── the slideshow ───────────────────────────────────────────────────

    def start_slides(self, items, index) -> None:
        live = [p for p in items if self.reachable(p)]
        if not live:
            from luma_appkit.tv_toast import TvToast
            TvToast.show(self.stage, "None of these can be opened right now", lead="image")
            return
        # From here: this photograph, or the next one that can be opened.
        index = max(0, min(index, len(items) - 1))
        following = [p for p in items[index:] if self.reachable(p)] or live
        at = live.index(following[0])
        skipped = len(items) - len(live)
        back = self.screen if self.screen != "viewer" else "viewer"
        self.slides = {"list": live, "i": at, "skipped": skipped, "back": back, "viewer": self.viewer,
                       "tick": 0, "elapsed": 0.0, "intro": True}
        self.screen = "slides"
        self._clear(self.overlay)
        self.overlay.set_visible(True)
        frame = Gtk.Overlay(hexpand=True, vexpand=True)
        frame.add_css_class("phtv-black")
        self.burns = KenBurns()
        frame.set_child(self.burns)
        self.slide_caption = Gtk.Label(xalign=0, valign=Gtk.Align.END, halign=Gtk.Align.START)
        self.slide_caption.add_css_class("phtv-cap-meta")
        self.slide_caption.set_margin_start(round(SAFE_X * self.unit))
        self.slide_caption.set_margin_bottom(round(48 * self.unit))
        frame.add_overlay(self.slide_caption)
        self.hair = Gtk.ProgressBar(valign=Gtk.Align.END)
        self.hair.add_css_class("phtv-hair")
        frame.add_overlay(self.hair)
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        intro.add_css_class("phtv-intro")
        intro.set_size_request(round(1100 * self.unit), -1)
        heading = Gtk.Label(label=_plural(len(live), "photograph"), xalign=0)
        heading.add_css_class("phtv-intro-title")
        if skipped:
            where = self.where(next(p for p in items if not self.reachable(p)))
            body = (f"{skipped} more {'is' if skipped == 1 else 'are'} in this set and will be skipped: the only "
                    f"copies are on {where}, which is out of reach. Photos has their thumbnails, not the photographs.")
        else:
            body = "Every one of them is reachable, so none will be skipped."
        text = Gtk.Label(label=body, xalign=0, wrap=True)
        text.add_css_class("phtv-intro-body")
        intro.append(heading)
        intro.append(text)
        self.slide_intro = intro
        frame.add_overlay(intro)
        self.overlay.append(frame)
        self._show_slide()
        self.slides["tick"] = GLib.timeout_add(33, self._slide_tick)

    def _show_slide(self) -> None:
        s = self.slides
        photo = s["list"][s["i"]]
        path = next((c.path for c in photo.copies if c.reachable), photo.path)
        self.burns.picture.set_paintable(None)
        self.textures.get(path, 2560, lambda t, b=self.burns: b.picture is not None and b.picture.set_paintable(t))
        self.burns.direction = 1 if s["i"] % 2 == 0 else -1
        moment = local_moment(photo)
        self.slide_caption.set_label(f"{photo.place or photo.display_name} · {_short(moment)}")
        self.slide_caption.set_opacity(1.0)
        s["elapsed"] = 0.0

    def _slide_tick(self) -> bool:
        s = self.slides
        if s is None:
            return False
        s["elapsed"] += 0.033
        if s["intro"]:
            if s["elapsed"] > 4.0:
                s["intro"] = False
                self.slide_intro.set_visible(False)
                s["elapsed"] = 0.0
            return True
        t = min(1.0, s["elapsed"] / SLIDE_SECONDS)
        self.burns.t = t
        self.burns.queue_draw()
        self.hair.set_fraction(t)
        # the caption introduces itself, then gets out of the way
        self.slide_caption.set_opacity(max(0.0, min(1.0, (3.0 - s["elapsed"]) / 0.8)))
        if t >= 1.0:
            s["i"] = (s["i"] + 1) % len(s["list"])
            self._show_slide()
        return True

    def stop_slides(self) -> None:
        s = self.slides
        if s is None:
            return
        if s["tick"]:
            GLib.source_remove(s["tick"])
        self.slides = None
        self._clear(self.overlay)
        self.overlay.set_visible(False)
        current = s["list"][s["i"]]
        if s["back"] == "viewer" and s["viewer"] is not None:
            items = s["viewer"]["items"]
            index = next((i for i, p in enumerate(items) if p.id == current.id), s["viewer"]["index"])
            self.screen = "viewer"
            self.viewer = dict(s["viewer"], index=index, chrome=True, hide=0)
            self._draw_viewer()
        else:
            self.screen = s["back"]
            self.render()

    # ── keys ────────────────────────────────────────────────────────────

    def _key(self, keyval: int, _state) -> bool:
        ok, play = keyval in OK_KEYS, keyval in PLAY_KEYS
        left, right = keyval == Gdk.KEY_Left, keyval == Gdk.KEY_Right
        up, down = keyval == Gdk.KEY_Up, keyval == Gdk.KEY_Down
        if not (ok or play or left or right or up or down):
            return False
        if not self.photos:
            return True
        if self.screen == "slides":
            s = self.slides
            if ok or play:
                self.stop_slides()
            elif left or right:
                s["i"] = (s["i"] + (1 if right else -1)) % len(s["list"])
                s["intro"] = False
                self.slide_intro.set_visible(False)
                self._show_slide()
            return True
        if self.screen == "viewer":
            v = self.viewer
            if left or right:
                v["index"] = max(0, min(len(v["items"]) - 1, v["index"] + (1 if right else -1)))
                v["chrome"] = True
            elif up:
                v["panel"] = True
            elif down:
                if v["panel"]:
                    v["panel"] = False
                v["chrome"] = True
            elif ok:
                self._favourite()
                return True
            elif play:
                self.start_slides(v["items"], v["index"])
                return True
            self._draw_viewer()
            return True
        if self.rail_open:
            if left or right:
                self.rail_index = max(0, min(len(self.dests) - 1, self.rail_index + (1 if right else -1)))
                self._render_rail()
            elif down:
                self.rail_open = False
                self.render()
            elif ok:
                self._go(self.rail_index)
            return True
        if self.screen == "library":
            items = self.days[self.day][1]
            if left or right:
                self.col = max(0, min(len(items) - 1, self.col + (1 if right else -1)))
            elif up:
                if self.day == 0:
                    self.rail_open = True
                else:
                    self.day -= 1
                    self.col = min(self.col, len(self.days[self.day][1]) - 1)
            elif down:
                if self.day < len(self.days) - 1:
                    self.day += 1
                    self.col = min(self.col, len(self.days[self.day][1]) - 1)
            elif ok or play:
                flat = self.photos
                index = flat.index(items[self.col])
                self._came_from = "library"
                (self.start_slides if play else self.open_viewer)(flat, index)
                return True
            self.render()
            return True
        if self.screen == "set":
            n = len(self.set_list)
            if left or right:
                self.set_index = max(0, min(n - 1, self.set_index + (1 if right else -1)))
            elif up:
                if self.set_index < GRID_COLUMNS:
                    self.rail_open = True
                else:
                    self.set_index -= GRID_COLUMNS
            elif down:
                self.set_index = min(n - 1, self.set_index + GRID_COLUMNS)
            elif (ok or play) and n:
                self._came_from = "set"
                (self.start_slides if play else self.open_viewer)(self.set_list, self.set_index)
                return True
            self.render()
            return True
        if self.screen == "sources":
            sources = list(self.sources.values())
            if left or right:
                self.card_index = max(0, min(len(sources) - 1, self.card_index + (1 if right else -1)))
            elif up:
                self.rail_open = True
            elif ok:
                source = sources[self.card_index]
                held, _only = self._source_counts(source)
                self._open_set(f"On {source.name}", held, back="sources")
                return True
            self.render()
            return True
        return False

    def _go(self, index: int) -> None:
        name, _sub, listing, special = self.dests[index]
        self.rail_open = False
        if special == "sources":
            self.screen = "sources"
            self.card_index = 0
        elif name == "Library":
            self.screen = "library"
        else:
            self._open_set(name, listing())
            return
        self.render()

    def _open_set(self, title, items, back="library") -> None:
        self.set_title, self.set_list, self.set_index = title, list(items), 0
        self._set_back = back
        self.screen = "set"
        self.rail_open = False
        self.render()

    def back(self) -> bool:
        if self.screen == "slides":
            self.stop_slides()
            return True
        if self.screen == "viewer":
            if self.viewer["panel"]:
                self.viewer["panel"] = False
                self.viewer["chrome"] = True
                self._draw_viewer()
            else:
                self.close_viewer()
            return True
        if self.rail_open:
            self.rail_open = False
            self.render()
            return True
        if self.screen == "set":
            self.screen = getattr(self, "_set_back", "library")
            self.render()
            return True
        if self.screen == "sources":
            self.screen = "library"
            self.render()
            return True
        return False
