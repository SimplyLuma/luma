# SPDX-License-Identifier: Apache-2.0
"""Tide-only surfaces, composed from LumaUI controls and semantic tokens.

AlbumCover draws album content, not application chrome. SongRow and Deck are
Tide's arrangements; shared buttons and text keep their kit-owned appearance.
"""
from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import unquote, urlsplit

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Graphene, Gsk, Gtk, Pango
from luma_appkit import lumaui
from luma_appkit import BarAction, CoverArt, DetailsItem, MediaTransport, Selection, apply_type, icons, lumaui_tokens
from luma_appkit.action_center import make_control
from luma_appkit.media_transport import MediaGlyph
from .presentation import (
    Album, PHONE_ALBUM_ROW_HEIGHT, PHONE_SONG_ROW_HEIGHT, SONG_ROW_HEIGHT, duration_text, song_viewport,
)


def named(widget, name):
    widget.set_name(name)
    return widget


def text(value='', role='body', *, muted=False, tabular=False, weight=None, **props):
    props.setdefault('xalign', 0)
    label = apply_type(Gtk.Label(label=value, **props), role, muted=muted, weight=weight)
    if tabular:
        label.add_css_class('numeric')  # toolkit's tabular-number treatment
    return label


def action(icon, label, callback, *, name=None, words=False, primary=False, active=False):
    widget = make_control(BarAction(icon, label if words else None, on_activate=callback,
                                    tooltip=label, primary=primary, active=active))
    if name:
        widget.set_name(name)
    return widget


def clear(box):
    child = box.get_first_child()
    while child:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def image_path(uri):
    if not uri:
        return None
    parsed = urlsplit(uri)
    if parsed.scheme == 'file':
        return Path(unquote(parsed.path))
    if not parsed.scheme:
        return Path(uri)
    return None  # artwork loading never authenticates or downloads in the UI


def colour(widget, name):
    found, rgba = widget.get_style_context().lookup_color(name)
    if not found:
        raise ValueError(f'missing Luma token {name}')
    return rgba


class CoverSlot(Gtk.Box):
    """Allocate the shared cover to a Tide layout slot, without restyling it."""
    def __init__(self, cover, side, *, flexible=False, fill=False):
        super().__init__(halign=Gtk.Align.START, valign=Gtk.Align.START)
        # GtkBox owns child disposal; retain this slot's custom measurements.
        self.set_layout_manager(None)
        self.cover, self.side, self.flexible, self.fill = cover, side, flexible, fill
        if fill:
            self.set_halign(Gtk.Align.FILL)
        self.append(cover)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (1 if self.flexible else self.side), self.side, -1, -1
        side = for_size if self.fill and for_size > 0 else min(self.side, for_size) if self.flexible and for_size > 0 else self.side
        return (1 if self.flexible and for_size < 0 else side), side, -1, -1

    def do_size_allocate(self, width, height, baseline):
        # The kit's mini cover paints at 26 px inside a 38 px queue slot.
        # Center its actual requested size instead of allocating it at (0, 0).
        side = min(width, height)
        if isinstance(self.cover, CoverArt) and self.cover.size == 'mini':
            side = min(side, self.cover.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
        offset = Gsk.Transform().translate(Graphene.Point().init((width - side) / 2, (height - side) / 2))
        self.cover.allocate(side, side, baseline, offset)

    def do_snapshot(self, snapshot):
        self.snapshot_child(self.cover, snapshot)

    def do_dispose(self):
        if self.cover.get_parent() is self:
            self.cover.unparent()


class ColumnSlot(Gtk.Box):
    """A fixed-width app column whose wrapping content may ask for more room."""
    def __init__(self, child, width):
        super().__init__(hexpand=False)
        self.set_layout_manager(None)
        self.child, self.width = child, width
        self.append(child)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.width, self.width, -1, -1
        return self.child.measure(orientation, self.width)

    def do_size_allocate(self, width, height, baseline):
        self.child.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot):
        self.snapshot_child(self.child, snapshot)

    def do_dispose(self):
        if self.child.get_parent() is self:
            self.child.unparent()


def AlbumCover(album: Album, size=160, *, plain=False, on_loaded=None, fill=False, flexible=False, presentation="cover"):
    """Compose the shared cover using actual source artwork when available.

    `fill`: the sleeve takes its column's width (v71's two-column phone grid), `size` being only its natural side.
    """
    variant = 'hero' if size == 264 else 'mini' if size <= 38 else 'tile'
    cover = CoverArt(album.title, album.artist, picture=image_path(album.artwork),
                     hues=album.hues, shape='album', size=variant, flexible=flexible or fill, presentation=presentation)
    if fill:
        slot = CoverSlot(cover, size, flexible=True, fill=True)
    elif variant == 'mini' and size != 26 or variant == 'tile' and size not in (160, 164):
        slot = CoverSlot(cover, size, flexible=size >= 200)
    else:
        slot = cover
    if on_loaded:
        # The shared native cover owns viewport admission, retries and decoded
        # pixels. Keep Tide's real artwork lighting callback at that boundary.
        cover.connect('artwork-loaded', lambda _cover, texture: on_loaded(texture))
    return slot


class PlayingBackdrop(Gtk.Widget):
    """Tide's full-room artwork, with the kit's content blur and scrim tokens."""
    def __init__(self, album):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.texture = None
        self.hue_cover = CoverArt('', '', hues=album.hues, shape='album', size='tile')
        self.hue_cover.set_parent(self)
        from luma_appkit.media_style import _WidgetTexture
        self._preview = _WidgetTexture(self, self._loaded)
        path = image_path(album.artwork)
        self._preview.set_source(str(path) if path else None, 800)

    def _loaded(self, texture):
        self.texture = texture
        self.queue_draw()

    def do_size_allocate(self, width, height, baseline):
        side = max(width, height) + 160
        self.hue_cover.allocate(side, side, baseline, None)

    def do_dispose(self):
        self._preview.close()
        if self.hue_cover.get_parent() is self:
            self.hue_cover.unparent()

    def do_snapshot(self, snapshot):
        self._preview.refresh()
        width, height = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, width, height)
        snapshot.append_color(colour(self, 'luma_media_surface'), bounds)
        if self.texture:
            from luma_appkit.media_style import cover_rect
            snapshot.push_clip(bounds)
            snapshot.push_blur(lumaui_tokens.LIT_HEADER['blur'])
            target = cover_rect(self.texture.get_width(), self.texture.get_height(),
                                Graphene.Rect().init(-80, -80, width+160, height+160))
            snapshot.append_texture(self.texture, target)
            snapshot.pop()
            snapshot.pop()
        else:
            # The approved generated cover supplies the album's colours too.
            side = max(width, height) + 160
            snapshot.push_clip(bounds)
            snapshot.push_blur(lumaui_tokens.LIT_HEADER['blur'])
            snapshot.save()
            snapshot.translate(Graphene.Point().init((width-side)/2, (height-side)/2))
            self.snapshot_child(self.hue_cover, snapshot)
            snapshot.restore()
            snapshot.pop()
            snapshot.pop()
        snapshot.append_color(colour(self, 'luma_drawer_scrim'), bounds)


class SourceFace(Gtk.Widget):
    """Tide's source mark (v70 tSrcIco), from kit glyphs and tokens."""
    def __init__(self, icon, state='', *, large=False):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.add_css_class('td-source-face')
        self.side, self.state = (56 if large else 32), state
        self.radius = lumaui_tokens.DIALOG['icon_radius'] if large else lumaui_tokens.STRUCTURE['details']['row_radius']
        self.dot = 12 if large else 9
        self.glyph = icons.image(icon, pixel_size=24 if large else 16)
        self.glyph.set_parent(self)
    def do_dispose(self):
        if self.glyph.get_parent() is self:
            self.glyph.unparent()

    def do_measure(self, _orientation, _for_size):
        return self.side, self.side, -1, -1
    def do_size_allocate(self, width, height, baseline):
        self.glyph.allocate(width, height, baseline, None)
    def do_snapshot(self, snapshot):
        size, radius = self.side, self.radius
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, size + 5, size + 5))
        cr.new_sub_path()
        for x, y, begin in ((size-radius, radius, -90), (size-radius, size-radius, 0),
                            (radius, size-radius, 90), (radius, radius, 180)):
            cr.arc(x, y, radius, math.radians(begin), math.radians(begin+90))
        cr.close_path()
        bg = colour(self, 'luma_fill')
        cr.set_source_rgba(bg.red, bg.green, bg.blue, bg.alpha)
        cr.fill_preserve()
        ring = colour(self, 'luma_hairline')
        cr.set_source_rgba(ring.red, ring.green, ring.blue, ring.alpha)
        cr.set_line_width(1)
        cr.stroke()
        self.snapshot_child(self.glyph, snapshot)
        if self.state not in ('ok', 'busy', 'bad'):
            return
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, size + 5, size + 5))
        cr.arc(size + 3 - self.dot/2, size + 3 - self.dot/2, self.dot/2 + 2, 0, math.tau)
        ring = colour(self, 'luma_content')
        cr.set_source_rgba(ring.red, ring.green, ring.blue, ring.alpha)
        cr.fill()
        cr.arc(size + 3 - self.dot/2, size + 3 - self.dot/2, self.dot/2, 0, math.tau)
        dot = colour(self, {'ok':'luma_good', 'busy':'luma_accent', 'bad':'luma_danger_ink'}[self.state])
        cr.set_source_rgba(dot.red, dot.green, dot.blue, dot.alpha)
        cr.fill()


class AlbumTile(Gtk.Button):
    def __init__(self, album, callback, *, year=False, size=160, fill=False):
        super().__init__(halign=Gtk.Align.FILL, valign=Gtk.Align.START)
        self.add_css_class('td-album-tile')
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.cover = AlbumCover(album, size, fill=fill, presentation="card")
        column.append(self.cover)
        column.append(text(album.title, 'body', weight=600, margin_top=9,
                           ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
        column.append(text(album.artist + (f' · {album.year}' if year and album.year else ''), 'meta', muted=True,
                           ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
        self.set_child(column)
        self.set_tooltip_text(album.title)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f'{album.title}, {album.artist}'])
        self.connect('clicked', lambda *_: callback(album.id))
        self.set_name('td-album-' + album.id)


class FeatureCard(Gtk.Button):
    """Listen now's feature (v71 `.feature`): the album's own colour, its sleeve top right, a kicker, the name, a line.

    The light is the album's hue, drawn like its generated cover (oklch 0.55 to 0.25 at 160°), and the words
    take the kit's media inks over it.
    """

    def __init__(self, album, kicker, line, callback, *, hue=None):
        super().__init__(halign=Gtk.Align.FILL, hexpand=True)
        self.add_css_class('td-feature')
        lumaui.media_context(self, True)
        self.hue = hue if hue is not None else (album.hues[0] if album.hues else 250)
        self.set_size_request(-1, 250)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        stack = Gtk.Overlay()
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END, margin_start=22,
                        margin_end=22, margin_bottom=22, margin_top=22)
        words.append(text(kicker, 'small', weight=600))
        # The name keeps to the left 60% (v71 max-width: 60%), clear of the sleeve.
        title = text(album.title, 'feature-title', wrap=True, margin_top=4, margin_bottom=6, margin_end=130)
        title.add_css_class('td-feature-title')
        words.append(title)
        words.append(text(line, 'body', ellipsize=Pango.EllipsizeMode.END, max_width_chars=1))
        stack.set_child(words)
        sleeve = AlbumCover(album, 146, presentation="feature")
        sleeve.set_halign(Gtk.Align.END)
        sleeve.set_valign(Gtk.Align.START)
        sleeve.set_margin_top(22)
        sleeve.set_margin_end(22)
        sleeve.set_can_target(False)
        stack.add_overlay(sleeve)
        self.words = words
        self.title = title
        self.set_child(stack)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f'{kicker}: {album.title}, {line}'])
        self.connect('clicked', lambda *_: callback(album.id))
        self.set_name('td-feature-' + album.id)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, width, height)
        radius = Graphene.Size().init(16, 16)
        rounded = Gsk.RoundedRect()
        rounded.init(bounds, radius, radius, radius, radius)
        snapshot.push_rounded_clip(rounded)
        # 160deg: from the top left a little past the top, to the bottom right.
        angle = math.radians(160)
        dx, dy = math.sin(angle), -math.cos(angle)
        half = (abs(width * dx) + abs(height * dy)) / 2
        cx, cy = width / 2, height / 2
        start = Graphene.Point().init(cx - dx * half, cy - dy * half)
        end = Graphene.Point().init(cx + dx * half, cy + dy * half)
        stops = []
        for offset, (lightness, chroma) in ((0.0, (0.55, 0.13)), (1.0, (0.25, 0.08))):
            stop = Gsk.ColorStop()
            stop.offset = offset
            stop.color = _oklch(lightness, chroma, self.hue)
            stops.append(stop)
        snapshot.append_linear_gradient(bounds, start, end, stops)
        Gtk.Button.do_snapshot(self, snapshot)
        snapshot.pop()


def _oklch(lightness, chroma, hue):
    from gi.repository import Gdk
    rgba = Gdk.RGBA()
    rgba.parse(lumaui.oklch_rgba(lightness, chroma, hue))
    return rgba


class SongRow(Gtk.Box):
    def __init__(self, album, song, player, on_play, on_love, *, show_album=False, phone=False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.song, self.album, self.show_album = song, album, show_album
        self.set_name('td-song-' + song.id)
        self.add_css_class('td-song-row')
        if not show_album:
            self.add_css_class('td-album-song-row')
        # v71 on a phone: a song list's rows are two lines (the title over its album), 60 tall; an album's are 52.
        two_lines = phone and show_album
        self.set_size_request(-1, PHONE_SONG_ROW_HEIGHT if two_lines else PHONE_ALBUM_ROW_HEIGHT if phone
                              else SONG_ROW_HEIGHT)
        current = song.id == player.song_id
        if current:
            self.add_css_class('playing')
        Selection.mark(self, current)
        self.index = Gtk.Stack(width_request=34 if two_lines else 40, valign=Gtk.Align.CENTER)
        number = text(str(song.number), 'body', muted=True, tabular=True, halign=Gtk.Align.CENTER)
        bars = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, spacing=2)
        for height in (8, 14, 10):
            bar = Gtk.Box(width_request=3, height_request=height, valign=Gtk.Align.END)
            bar.add_css_class('td-equalizer-bar')
            bars.append(bar)
        self.index.add_named(number, 'number')
        self.index.add_named(bars, 'playing')
        hover_play = Gtk.Button(child=icons.image('play', pixel_size=15),
                                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                tooltip_text='Play ' + song.title)
        hover_play.add_css_class('td-song-title')
        hover_play.connect('clicked', lambda *_: on_play(self.album, self.song))
        self.index.add_named(hover_play, 'hover')
        motion = Gtk.EventControllerMotion()
        motion.connect('enter', lambda *_: self.index.set_visible_child_name('hover'))
        motion.connect('leave', lambda *_: self.index.set_visible_child_name('playing' if self.playing else 'number'))
        self.add_controller(motion)
        self.play = Gtk.Button(hexpand=True, halign=Gtk.Align.FILL)
        self.play.set_name('td-play-song-' + song.id)
        self.play.add_css_class('td-song-title')
        self.play.set_child(text(song.title, 'reading' if phone else 'body', weight=500,
                                 ellipsize=Pango.EllipsizeMode.END))
        self.play.update_property([Gtk.AccessibleProperty.LABEL], [f'Play {song.title}'])
        self.play.connect('clicked', lambda *_: on_play(self.album, self.song))
        self.append(self.index)
        self.index.set_visible_child_name('playing' if current else 'number')
        if show_album:
            self.credit = text(f'{album.title} · {album.artist}', 'meta', muted=True, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.END, max_width_chars=1 if two_lines else -1)
        if two_lines:
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
            words.append(self.play)
            words.append(self.credit)
            self.append(words)
        else:
            self.append(self.play)
            if show_album:
                self.append(self.credit)
        self.love = Gtk.ToggleButton(active=song.loved, width_request=40,
                                     tooltip_text='Love', valign=Gtk.Align.CENTER)
        self.love.set_name('td-love-' + song.id)
        self.love.add_css_class('td-song-love')
        self.love.set_child(MediaGlyph('heart', 15, filled=song.loved))
        self.love.update_property([Gtk.AccessibleProperty.LABEL], ['Love'])
        self._love_handler = self.love.connect('toggled', lambda *_: on_love(self.song.id))
        self.append(self.love)
        self.time = text(duration_text(song.duration), 'body', muted=True, tabular=True,
                         width_request=40 if two_lines else 56, xalign=1)
        self.append(self.time)
        self.playing = current

    def set_subject(self, album, song, player):
        """Reuse the app row without retaining callbacks to its previous song."""
        self.album, self.song = album, song
        self.set_name('td-song-' + song.id)
        self.index.get_child_by_name('number').set_label(str(song.number))
        self.play.set_name('td-play-song-' + song.id)
        self.play.get_child().set_label(song.title)
        self.play.update_property([Gtk.AccessibleProperty.LABEL], [f'Play {song.title}'])
        self.index.get_child_by_name('hover').set_tooltip_text('Play ' + song.title)
        if self.show_album:
            self.credit.set_label(f'{album.title} · {album.artist}')
        self.love.set_name('td-love-' + song.id)
        self.love.handler_block(self._love_handler)
        try:
            self.love.set_active(song.loved)
        finally:
            self.love.handler_unblock(self._love_handler)
        self.love.set_child(MediaGlyph('heart', 15, filled=song.loved))
        self.time.set_label(duration_text(song.duration))
        self.set_player(player)

    def set_player(self, player):
        current = self.song.id == player.song_id
        Selection.mark(self, current)
        (self.add_css_class if current else self.remove_css_class)('playing')
        self.index.set_visible_child_name('playing' if current else 'number')
        self.playing = current


class SongList(Gtk.Box):
    """Tide's fixed-height rows, bounded to the viewport for large libraries."""
    def __init__(self, subjects, make_row, bind_row, adjustment, page, on_rows, *, row_height=SONG_ROW_HEIGHT):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, margin_top=6)
        self.row_height = row_height
        self.set_name('td-song-list')
        self.subjects, self.make_row = subjects, make_row
        self.bind_row, self.pool = bind_row, []
        self.adjustment, self.page, self.on_rows = adjustment, page, on_rows
        self.rows = {}
        self.span = None
        self.leading = Gtk.Box()
        self.listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.trailing = Gtk.Box()
        self.append(self.leading)
        self.append(self.listing)
        self.append(self.trailing)
        self._set_span((0, len(subjects) if len(subjects) <= 128 else 40))
        self.add_tick_callback(self._viewport_changed)

    def _viewport_changed(self, *_):
        if len(self.subjects) <= 128:
            span = (0, len(self.subjects))
        else:
            ok, bounds = self.compute_bounds(self.page)
            origin = bounds.get_y() if ok else 0
            span = song_viewport(len(self.subjects), self.adjustment.get_value() - origin,
                                 self.adjustment.get_page_size(), row_height=self.row_height)
        self._set_span(span)
        return True

    def _set_span(self, span):
        if span == self.span:
            return
        first, last = span
        retained = {index: row for index, row in self.rows.items() if first <= index < last}
        self.pool.extend(row for index, row in self.rows.items() if index not in retained)
        clear(self.listing)
        for index in range(first, last):
            row = retained.get(index)
            if row is None:
                if self.pool:
                    row = self.pool.pop()
                    self.bind_row(row, *self.subjects[index])
                else:
                    row = self.make_row(*self.subjects[index])
                retained[index] = row
            self.listing.append(row)
        self.rows, self.span = retained, span
        self.leading.set_size_request(-1, first * self.row_height)
        self.leading.set_visible(first > 0)
        after = len(self.subjects) - last
        self.trailing.set_size_request(-1, after * self.row_height)
        self.trailing.set_visible(after > 0)
        self.on_rows(list(retained.values()))


class QueueRow(DetailsItem):
    def __init__(self, album, song, current, on_play):
        cover = AlbumCover(album, 38, plain=True)
        cover.set_valign(Gtk.Align.CENTER)
        super().__init__(song.title, album.artist, lead=cover,
                         when=duration_text(song.duration), selected=current,
                         on_activate=lambda: on_play(album, song))
        self.set_name('td-queue-' + song.id)


class DeckControls(Gtk.Box):
    """Tide's minmax(0,1fr) / transport / minmax(0,1fr) deck grid."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_layout_manager(None)
        self.compact = False

    def do_measure(self, orientation, for_size):
        children = [child for child in (self.get_first_child(),) if child]
        while children and children[-1].get_next_sibling() is not None:
            children.append(children[-1].get_next_sibling())
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, sum(c.measure(orientation, -1)[1] for c in children if c.get_visible()), -1, -1
        height = max((c.measure(orientation, -1)[1] for c in children if c.get_visible()), default=0)
        return height, height, -1, -1

    def do_size_allocate(self, width, height, baseline):
        if width <= 0:
            return
        mid = self.get_first_child()
        transport = mid.get_next_sibling()
        end = transport.get_next_sibling()
        center_width = min(width, transport.measure(Gtk.Orientation.HORIZONTAL, -1)[1])
        center_x = width - center_width if self.compact else (width - center_width) // 2
        def place(child, x, w):
            child.allocate(max(0, w), height, baseline,
                           Gsk.Transform().translate(Graphene.Point().init(x, 0)))
        place(mid, 0, max(0, center_x - (8 if self.compact else 16)))
        place(transport, center_x, center_width)
        if end.get_visible():
            end_width = end.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            place(end, width - end_width, end_width)

    def do_snapshot(self, snapshot):
        if self.get_width() <= 0:
            return
        child = self.get_first_child()
        while child:
            if child.get_visible():
                self.snapshot_child(child, snapshot)
            child = child.get_next_sibling()


class Deck(Gtk.Overlay):
    """The frame's Tide-only deck, using kit transport buttons and labels."""
    def __init__(self, window):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        self.window = window
        self.set_name('td-deck-frame')
        self.set_size_request(-1, 128)
        self.add_css_class('td-deck')
        self.background = Gtk.DrawingArea(content_height=92)
        self.background.set_draw_func(self._draw)
        self.set_child(self.background)
        self.body = named(Gtk.Overlay(height_request=92, valign=Gtk.Align.END,
                                      margin_start=18, margin_end=18), 'td-deck')
        self.inner = DeckControls(valign=Gtk.Align.FILL, margin_start=140,
                                  margin_end=20, margin_bottom=8)
        self.body.set_child(self.inner)
        self.add_overlay(self.body)
        self.sleeve = Gtk.Button(valign=Gtk.Align.END, halign=Gtk.Align.START,
                                 margin_start=36, margin_bottom=20)
        self.sleeve.add_css_class('td-sleeve')
        self.sleeve.set_name('td-open-playing-album')
        self.sleeve.connect('clicked', lambda *_: window.open_playing_album())
        self.add_overlay(self.sleeve)
        self.mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title_line = Gtk.Box(spacing=2)
        self.title = text('', 'title-2', weight=500, ellipsize=Pango.EllipsizeMode.END)
        self.title.set_width_chars(1)
        self.title.set_max_width_chars(34)
        self.title_action = named(Gtk.Button(child=self.title), 'td-open-now-playing')
        self.title_action.add_css_class('td-link')
        self.title_action.set_tooltip_text('Open Now Playing')
        self.title_action.update_property([Gtk.AccessibleProperty.DESCRIPTION], ['Open Now Playing'])
        self.title_action.connect('clicked', lambda *_: window.open_now_playing())
        title_line.append(self.title_action)
        self.love = action('heart', 'Love', window.love_playing, name='td-player-love')
        title_line.append(self.love)
        self.mid.append(title_line)
        self.credit = Gtk.Box()
        self.mid.append(self.credit)
        self.inner.append(self.mid)
        self.transport = MediaTransport('deck', playing=window.player.playing,
            position=window.player.position, shuffle=window.player.shuffle,
            repeat=window.player.repeat, volume=window.player.volume / 100,
            on_play=lambda _playing: window.source.toggle(), on_seek=window.source.seek,
            on_step=window.source.step, on_shuffle=lambda _on: window.source.shuffle(),
            on_repeat=lambda _on: window.source.repeat(), on_volume=lambda value: window.source.set_volume(value * 100))
        self.transport.set_valign(Gtk.Align.CENTER)
        self.transport.set_hexpand(False)
        self.transport.set_name('td-transport')
        for key, name in (('play', 'td-play-pause'), ('shuffle', 'td-shuffle'), ('previous', 'td-previous'),
                          ('next', 'td-next'), ('repeat', 'td-repeat')):
            self.transport.key(key).set_name(name)
        self.play = self.transport.key('play')
        self.shuffle = self.transport.key('shuffle')
        self.previous = self.transport.key('previous')
        self.next = self.transport.key('next')
        self.repeat = self.transport.key('repeat')
        self.scrub = self.transport.get_last_child()
        self.inner.append(self.transport)
        self.end = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.queue = action('list', 'Up next', window.toggle_queue, name='td-up-next')
        self.output = action('monitor', 'Play on This computer', window.open_output, name='td-output')
        self.end.append(self.queue)
        self.end.append(self.output)
        self.end.append(self.transport.volume_control)
        self.inner.append(self.end)
        self._album_id = None
        self._compact = False

    def set_compact_layout(self, compact):
        if self._compact != compact:
            self._compact = self.inner.compact = compact
            self.inner.queue_allocate()

    def update_width(self, width):
        # CenterBox keeps transport centered independently of the start child.
        # Keep ordinary credits visible at the reference width, but give an
        # unusually long artist/album pair room before it reaches the controls.
        narrow = width <= 1099
        if hasattr(self, 'album_credit') and width < 1500:
            credit_chars = (len(self.artist_credit.get_child().get_label()) +
                            len(self.album_credit.get_child().get_label()))
            narrow = narrow or credit_chars > (26 if width < 1300 else 32)
        if hasattr(self, 'album_credit'):
            self.album_credit.set_visible(not narrow)
            self.credit_separator.set_visible(not narrow)
            self.artist_credit.get_child().set_max_width_chars(18 if width > 639 else 13)
            self.album_credit.get_child().set_max_width_chars(22)
        self.title.set_max_width_chars(34 if width >= 1500 else 22 if width > 899 else 25 if width > 639 else 17)
        self.inner.set_margin_start(28 if self.window.now_tab else 108 if width <= 720 else 140)

    def update(self, library, player):
        if not player.song_id:
            self.set_visible(False)
            return
        try:
            album, song = library.song(player.song_id)
        except StopIteration:
            self.set_visible(False)
            return
        # v71: on a phone the deck gives way to the one bar's mini player.
        self.set_visible(not getattr(self.window, 'phone', False))
        if self._album_id != album.id:
            cover_size = 78 if self.window.get_width() <= 720 else 108
            self.sleeve.set_child(AlbumCover(album, cover_size, plain=True))
            self.sleeve.set_tooltip_text('Open ' + album.title)
            clear(self.credit)
            artist = Gtk.Button(hexpand=False)
            artist.add_css_class('td-credit')
            artist.set_child(text(album.artist, 'meta', muted=True, ellipsize=Pango.EllipsizeMode.END,
                                  width_chars=1, max_width_chars=18))
            artist.set_tooltip_text(album.artist)
            artist.connect('clicked', lambda *_: self.window.show_view('artists'))
            self.credit.append(artist)
            separator = text(' · ', 'meta', muted=True)
            self.credit.append(separator)
            release = Gtk.Button(hexpand=False)
            release.add_css_class('td-credit')
            release.set_child(text(album.title, 'meta', muted=True, ellipsize=Pango.EllipsizeMode.END,
                                   width_chars=1, max_width_chars=22))
            release.set_tooltip_text(album.title)
            release.connect('clicked', lambda *_: self.window.open_album(album.id))
            self.credit.append(release)
            self.artist_credit, self.album_credit, self.credit_separator = artist, release, separator
            self._album_id = album.id
        self.update_width(self.window.get_width())
        self.title.set_label(song.title)
        self.title_action.set_tooltip_text(f'{song.title} · Open Now Playing')
        self.transport.set_duration(song.duration)
        self.transport.set_position(player.position)
        self.transport.set_playing(player.playing)
        self.transport.set_shuffle(player.shuffle)
        self.transport.set_repeat(player.repeat)
        self.transport.set_volume(player.volume / 100)
        self.output.set_tooltip_text('Play on ' + player.output)
        self.output.update_property([Gtk.AccessibleProperty.LABEL], ['Play on ' + player.output])
        # A one-item chooser only repeats the current device and crowds the
        # deck. It appears again as soon as another output is available.
        self.output.set_visible(len(player.outputs) > 1)
        (self.love.add_css_class if song.loved else self.love.remove_css_class)('on')

    def _draw(self, area, cr, width, height):
        # v70 shapeDeck: frame-connected shoulders, rather than a rounded bar.
        F, R, B = 18, 20, 84
        body_height = 112 if self.window.layer.get_width() <= 900 else 92
        top = max(0, height - body_height)
        B = body_height - 8
        k = 4 * (math.sqrt(2) - 1) / 3
        cr.move_to(0, top + B)
        cr.curve_to(k * F, top + B, F, top + B - F + k * F, F, top + B - F)
        cr.line_to(F, top + R)
        cr.curve_to(F, top + R - k * R, F + R - k * R, top, F + R, top)
        cr.line_to(width - F - R, top)
        cr.curve_to(width - F - R + k * R, top, width - F, top + R - k * R, width - F, top + R)
        cr.line_to(width - F, top + B - F)
        cr.curve_to(width - F, top + B - F + k * F, width - k * F, top + B, width, top + B)
        cr.line_to(width, height)
        cr.line_to(0, height)
        cr.close_path()
        # This shape rises directly out of the window frame; the ActionCenter
        # bar surface is intentionally lighter and made the deck look detached.
        bg = colour(self, 'luma_window')
        cr.set_source_rgba(bg.red, bg.green, bg.blue, bg.alpha)
        cr.fill_preserve()
        border = colour(self, 'luma_bar_ring')
        cr.set_source_rgba(border.red, border.green, border.blue, border.alpha)
        cr.set_line_width(.5)
        cr.stroke()
