# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: an app's icon, and a note's mini card (ID1, ID3).

    AppIcon("org.projectluma.Notes", size=48)                       # the installed icon
    AppIcon(name="Kiln", size=56, category="create")                 # no icon: a monogram in the category's hue
    AppIcon(picture=texture, name="Kiln", size=108)                  # a picture the app already has
    NoteCard("Launch plan", "Freeze strings on the 10th…", on_activate=open_note)

v70 `.dic` (Depot, Monitor's apps, Valet's hero): a rounded square, its corner
22% of its side, a soft drop. An app with no icon gets `.dic.mono`: its
initial at 36% of the side, white on a radial wash of its hue (0.74 → 0.46
lightness, 0.12 chroma), the hue its category's (`CATEGORIES`) or its name's.
Sizes are `APP_ICON_SIZES` (22 a list's glyph, 28 a row, 32 an app's own bar,
40, 44 a story card's face, 48 and 52 tiles, 56 a card, 64 the system update,
72, 88 the phone app page, 96 an installer's prompt and 108 a hero); nothing in
between. v71 Depot rounds three of them by hand (`APP_ICON_CORNERS`: 32 at 9,
44 at 12, 88 at 22); the rest at 22% of the side.
`hue=` is the app's own colour when the catalogue has one.

`NoteCard` is Notes' own mini card for owning-app parity with `ContactCard`
and `EventCard` (v70 `#n-kids .kid`): the page glyph, its title and a line of
it on the recessed well, a chevron; it opens the note.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import icons, lumaui, rows_tokens  # noqa: E402

__all__ = ["APP_ICON_SIZES", "AppIcon", "NoteCard", "app_monogram"]

APP_ICON_SIZES = (22, 28, 32, 40, 44, 48, 52, 56, 64, 72, 88, 96, 108)
#: v71 Depot's own corners where they are not 22% of the side (`.dpappid > .dic`, `.dfcst`, `.dapph > .dic`).
APP_ICON_CORNERS = {32: 9, 44: 12, 88: 22}


def app_icon_corner(size: float) -> float:
    """An app icon's corner radius at `size`: Depot's own where v71 sets one, else 22% of the side."""
    return APP_ICON_CORNERS.get(round(size), size * rows_tokens.value("app_icon", "corner_ratio"))
#: v70 --cat-*: the hue each Depot category washes a monogram with.
_CATEGORY_HUES = {"create": 25, "work": 250, "media": 330, "play": 150, "tools": 200}


def app_monogram(name: str) -> str:
    """What an app without an icon is known by: its name's first two letters ("Blender" is Bl, v70 .dic.mono)."""
    letters = [c for c in name.strip() if c.isalnum()]
    if not letters:
        return "?"
    return letters[0].upper() + "".join(letters[1:2]).lower()


def _resolve_icon(app_id: str | None, size: int, widget: Gtk.Widget) -> Gdk.Paintable | None:
    if not app_id:
        return None
    display = widget.get_display() or Gdk.Display.get_default()
    if display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    gicon = None
    try:
        info = Gio.DesktopAppInfo.new(app_id if app_id.endswith(".desktop") else f"{app_id}.desktop")
    except (TypeError, AttributeError):
        info = None
    if info is not None:
        gicon = info.get_icon()
    if gicon is None and theme.has_icon(app_id):
        gicon = Gio.ThemedIcon.new(app_id)
    if gicon is None:
        return None
    return theme.lookup_by_gicon(gicon, size, max(1, widget.get_scale_factor()), Gtk.TextDirection.NONE,
                                 Gtk.IconLookupFlags.PRELOAD)


class AppIcon(Gtk.Widget):
    """An app, as its icon: the installed one, a picture, or its monogram in its hue."""

    __gtype_name__ = "LumaUIAppIcon"

    def __init__(self, app_id: str | None = None, *, picture: Gdk.Paintable | None = None, name: str = "",
                 size: int = 48, category: str | None = None, hue: float | None = None) -> None:
        if size not in APP_ICON_SIZES:
            raise ValueError(f"an app icon is one of {', '.join(map(str, APP_ICON_SIZES))} px, not {size}")
        if category is not None and category not in _CATEGORY_HUES:
            raise ValueError(f"an app's category is one of {', '.join(_CATEGORY_HUES)}")
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.IMG,
                         overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("lumaui-appicon")
        self.add_css_class(f"s{size}")
        self.size, self.app_id, self.category = size, app_id, category
        self.name = name or (app_id.rsplit(".", 1)[-1] if app_id else "")
        self.paintable = picture if picture is not None else _resolve_icon(app_id, size, self)
        self.monogram = app_monogram(self.name) if self.paintable is None else None
        # The app's own hue when it has one (Depot's catalogue gives each app one), else its category's, else its name's.
        self.hue = (float(hue) % 360 if hue is not None else _CATEGORY_HUES.get(category) if category
                    else lumaui.person_hue(self.name))
        lumaui.set_css_class(self, "mono", self.paintable is None)
        self.label = None
        if self.monogram:
            # The initials are a real label, so they are measured, selectable by tools and themable.
            m = rows_tokens.group("app_icon")
            px = round(size * m["mono_size_ratio"])
            attributes = Pango.AttrList()
            attributes.insert(Pango.attr_size_new_absolute(px * Pango.SCALE))
            attributes.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            attributes.insert(Pango.attr_letter_spacing_new(int(px * m["mono_tracking_em"] * Pango.SCALE)))
            self.label = Gtk.Label(label=self.monogram, attributes=attributes, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            self.label.add_css_class("lumaui-appicon-initials")
            self.label.set_parent(self)
        self.update_property([Gtk.AccessibleProperty.LABEL], [self.name or "App"])

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        return self.size, self.size, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self.label is not None:
            self.label.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        if self.label is not None and self.label.get_parent() is self:
            self.label.unparent()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, width, height)
        corner = Graphene.Size().init(*(2 * [app_icon_corner(width)]))
        clip = Gsk.RoundedRect()
        clip.init(bounds, corner, corner, corner, corner)
        snapshot.push_rounded_clip(clip)
        if self.paintable is not None:
            self.paintable.snapshot(snapshot, width, height)
        else:
            m = rows_tokens.group("app_icon")
            stops = []
            for offset, lightness in ((0.0, m["mono_light_ratio"]), (1.0, m["mono_dark_ratio"])):
                colour = Gdk.RGBA()
                colour.parse(lumaui.oklch_rgba(lightness, m["mono_chroma_ratio"], self.hue))
                stop = Gsk.ColorStop()
                stop.offset, stop.color = offset, colour
                stops.append(stop)
            # v70: radial-gradient(90% 80% at 30% 20%, light, dark)
            center = Graphene.Point().init(width * 0.3, height * 0.2)
            snapshot.append_radial_gradient(bounds, center, width * 0.9, height * 0.8, 0.0, 1.0, stops)
            snapshot.pop()
            self.snapshot_child(self.label, snapshot)
            return
        snapshot.pop()


class NoteCard(Gtk.Button):
    """A note, as Notes shows it anywhere else: its page glyph, title, a line of it, a chevron."""

    __gtype_name__ = "LumaUINoteCard"

    def __init__(self, title: str, preview: str = "", *, on_activate: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.add_css_class("lumaui-note-card")
        line = Gtk.Box()
        glyph = icons.image("file-text")
        glyph.add_css_class("lumaui-note-card-glyph")
        line.append(glyph)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label=title or "New note", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title.add_css_class("lumaui-note-card-title")
        text.append(self.title)
        self.preview = Gtk.Label(label=preview, xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=bool(preview))
        self.preview.add_css_class("lumaui-note-card-preview")
        text.append(self.preview)
        line.append(text)
        chevron = icons.image("chevron-right")
        chevron.add_css_class("lumaui-note-card-chevron")
        line.append(chevron)
        self.set_child(line)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"{title or 'New note'}, {preview}" if preview else (title or "New note")])
        if on_activate is not None:
            self.connect("clicked", lambda _b: on_activate())
