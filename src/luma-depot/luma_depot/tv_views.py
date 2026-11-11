# SPDX-License-Identifier: Apache-2.0
"""Depot's TV presentation: the same Depot, at the couch.

Shown inside `DepotShell` while the shell is in TV posture (docs/developer-platform/
tv-mode.md). It draws the redesign's own model: the shell's `World` (what is listed, what
runs on this device, what is installed, what waits to update) and its `Store` (install,
remove, update, Luma's own update), through the same `model.rules` the desktop pages use.
Only the drawing is the TV's: LumaUI's TV parts, sized for the room and driven by the remote.

The places are the desktop's (handoff 2.2), in the shared top bar: Today, Apps, Looks,
Tweaks, Library, Updates, then Search. Someone who knows Depot on a computer, a tablet or a
phone finds the same places here. What is listed is what runs on a TV: the world's device is
this TV (its form is "tv"), so every rule filters to it as it filters to a phone on a phone.

Remote: arrows move to whatever lies that way; moving along the bar opens each place; OK
opens or acts; Back steps out (an item, then the bar), then to the TV home.
"""
from __future__ import annotations

import math
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from luma_appkit import icons  # noqa: E402
from luma_appkit import lumaui_tokens as tokens  # noqa: E402
from luma_appkit.commands import Command, CommandGroup, CommandRegistry  # noqa: E402
from luma_appkit.tv_focus import TvFocus  # noqa: E402
from luma_appkit.tv_media import TvRoom, TvSlide  # noqa: E402
from luma_appkit.tv_menu import TvMenu  # noqa: E402
from luma_appkit.tv_parts import TvButton, TvField, TvSwitchRow, TvTab, TvTopBar  # noqa: E402
from luma_appkit.tv_stage import TvStage, add_tv_style, tv_material  # noqa: E402
from luma_appkit.tv_tiles import (  # noqa: E402
    TvEmptyState, TvPage, TvProgressButton, TvScreenshotViewer, TvShot, TvShelf, TvTileGrid, TvWideTile,
)
from luma_appkit.tv_toast import TvToast  # noqa: E402

from . import tv_facts  # noqa: E402
from .model import rules  # noqa: E402

TV = tokens.TV
#: The desktop's places (shell.PLACES), then Search: (key, title, lead glyph).
TABS = (("today", "Today", None), ("apps", "Apps", None), ("looks", "Looks", None), ("tweaks", "Tweaks", None),
        ("library", "Library", None), ("updates", "Updates", None), ("search", "Search", "search"))
INPUT_ICON = {tv_facts.REMOTE: "tv-minimal", tv_facts.CONTROLLER: "gamepad-2", tv_facts.KEYBOARD: "keyboard"}
PAGE_TOP = 150
#: Updates' Automatic rows: the desktop's (pages/updates.AUTO) less the one about matching other devices.
AUTO = (("apps", "Update apps overnight"), ("luma", "Get Luma updates overnight"))


def _line(size: float) -> int:
    """Figtree's own line box at `size` (ascent .95, descent .25, each to a whole pixel), as the mockups set it."""
    return int(math.floor(0.95 * size + 0.5) + math.floor(0.25 * size + 0.5))


def _lines(css: str) -> str:
    """Every rule that sets a font size and no line height gets Figtree's whole-pixel line box."""
    def fix(match: re.Match) -> str:
        body = match.group(0)
        size = re.search(r"font-size: ([0-9.]+)tv;", body)
        if size is None or "line-height" in body:
            return body
        return body[:-1].rstrip() + f" line-height: {_line(float(size.group(1)))}tv; }}"

    def labels(match: re.Match) -> str:
        selectors, body = match.group(1), match.group(2)
        size = re.search(r"font-size: ([0-9.]+)tv;", body)
        if size is None or "line-height" in body or re.search(r"(^|[ ,])label", selectors):
            return match.group(0)
        inner = ", ".join(f"{part.strip()} label" for part in selectors.split(","))
        return match.group(0) + f"\n{inner} {{ line-height: {_line(float(size.group(1)))}tv; }}"
    css = re.sub(r"([^{}\n][^{}]*?)\s*(\{[^{}]*\})", labels, css)
    return re.sub(r"\{[^{}]*\}", fix, css)


def _sheet(opaque: bool) -> str:
    return _lines(_rules(opaque))


def _rules(opaque: bool) -> str:
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    return f"""
.depot-tv-pad {{ padding: 26tv 96tv 140tv; }}
.depot-tv-pad.depot-tv-list-pad {{ padding: 28tv 102tv 160tv; }}
.depot-tv-sec {{ margin-bottom: 54tv; }}
.depot-tv-sec-head {{ border-spacing: 16tv; margin-bottom: 22tv; }}
.depot-tv-sec-head.depot-tv-list-head {{ margin-bottom: 54tv; }}
.lumaui-tv-stage .depot-tv-pad label.tv-h1 {{ line-height: 55tv; }}
.lumaui-tv-stage .depot-tv-pad label.tv-h2 {{ line-height: 37tv; }}
.lumaui-tv-stage .depot-tv-pad label.tv-body {{ line-height: 36tv; }}
.lumaui-tv-stage label.depot-tv-small {{ font-size: 20tv; font-weight: 600; letter-spacing: -0.3tv; color: {m('ink_3')}; }}
.lumaui-tv-stage .depot-tv-count {{ min-width: 12tv; min-height: 28tv; padding: 0 8tv;
  border-radius: 14tv; font-size: 17tv; font-weight: 650; color: white; background-color: #e5484d; }}
.depot-tv-feature {{ min-height: 430tv; margin-bottom: 54tv; }}
.depot-tv-feature-text {{ border-spacing: 14tv; }}
.lumaui-tv-stage label.depot-tv-kicker {{ font-size: 20tv; font-weight: 700; letter-spacing: 1.2tv; color: #e8c15a; }}
.depot-tv-feature-title {{ font-size: 56tv; font-weight: 750; letter-spacing: -1.6tv; line-height: 57tv; }}
.depot-tv-top {{ border-spacing: 60tv; }}
.depot-tv-dev {{ font-size: 24tv; font-weight: 600; color: {m('ink_2')}; margin-top: 10tv; border-spacing: 10tv; }}
.depot-tv-dev image {{ -gtk-icon-size: 24tv; color: #63b3ff; }}
.depot-tv-summary {{ margin-top: 14tv; }}
.depot-tv-facts {{ margin-top: 22tv; border-spacing: 34tv; font-size: 22tv; font-weight: 600; color: {m('ink_3')}; }}
.lumaui-tv-stage label.depot-tv-stars {{ color: #fec84b; letter-spacing: 1tv; }}
.lumaui-tv-stage .depot-tv-facts label.depot-tv-fact {{ color: {m('ink')}; font-weight: 650; }}
.depot-tv-input {{ min-height: 52tv; padding: 0 18tv; border-radius: 16tv; border-spacing: 10tv; margin-top: 26tv;
  font-size: 21tv; font-weight: 650; color: {m('ink_2')}; }}
.depot-tv-input image {{ -gtk-icon-size: 24tv; }}
.depot-tv-input.depot-tv-need {{ color: #f0c05a; }}
.depot-tv-acts {{ margin-top: 32tv; border-spacing: 14tv; }}
.depot-tv-about {{ border-spacing: 16tv; }}
.lumaui-tv-stage label.depot-tv-note {{ font-size: 24tv; color: {m('ink_3')}; }}
.depot-tv-news {{ padding: 28tv; border-radius: 30tv; border-spacing: 14tv; margin-bottom: 34tv; }}
.depot-tv-news-items {{ border-spacing: 8tv; margin-bottom: 8tv; }}
.depot-tv-news-item {{ border-spacing: 0; }}
.depot-tv-dot {{ min-width: 6tv; min-height: 6tv; border-radius: 3tv; margin: 15tv 10tv 0 10tv; background-color: {m('ink_2')}; }}
.lumaui-tv-stage .depot-tv-pad label.tv-body {{ color: {m('ink_2')}; }}
.lumaui-tv-stage .depot-tv-feature-text label.tv-body {{ margin: 26tv 0; }}
.lumaui-tv-stage .depot-tv-facts label {{ font-size: 22tv; line-height: 27tv; }}
.depot-tv-shots {{ border-spacing: 28tv; }}
.depot-tv-sec.depot-tv-shots-sec {{ margin-top: 60tv; }}
.depot-tv-after-top {{ margin-top: 60tv; }}
.depot-tv-head {{ border-spacing: 8tv; }}
.depot-tv-group {{ margin-top: 34tv; }}
.lumaui-tv-stage label.depot-tv-group-title {{ margin: 0 0 12tv 26tv; font-size: 20tv; font-weight: 650; letter-spacing: 1tv; color: {m('ink_3')}; }}
.depot-tv-card {{ padding: 8tv; border-radius: 30tv; }}
.depot-tv-row {{ min-height: 60tv; padding: 12tv 22tv; border-radius: 22tv; border-spacing: 20tv; }}
.depot-tv-row .depot-tv-lead {{ min-width: 48tv; min-height: 48tv; border-radius: 14tv; background-color: alpha(white, .08); color: {m('ink_2')}; }}
.depot-tv-row .depot-tv-lead image {{ -gtk-icon-size: 26tv; }}
.depot-tv-row .depot-tv-lead.depot-tv-app image {{ -gtk-icon-size: 48tv; }}
.depot-tv-row .depot-tv-lead.depot-tv-app {{ background: none; }}
.depot-tv-row .depot-tv-lead.depot-tv-mark image {{ -gtk-icon-size: 29tv; }}
.depot-tv-row-title {{ font-size: 25tv; font-weight: 600; }}
.lumaui-tv-stage label.depot-tv-row-sub {{ font-size: 19tv; font-weight: 500; color: {m('ink_3')}; margin-top: 3tv; }}
.depot-tv-row-value {{ font-size: 23tv; font-weight: 600; color: {m('ink_2')}; border-spacing: 8tv; }}
.depot-tv-row-value image {{ -gtk-icon-size: 24tv; color: {m('ink_3')}; }}
.depot-tv-row.tv-key {{ color: {m('key_ink')}; }}
.depot-tv-row.tv-key .depot-tv-row-sub, .depot-tv-row.tv-key .depot-tv-row-value,
.depot-tv-row.tv-key .depot-tv-row-value image {{ color: alpha({m('key_ink')}, .62); }}
.depot-tv-row.tv-key .depot-tv-lead {{ background-color: alpha(black, .08); color: {m('key_ink')}; }}
.depot-tv-divider {{ min-height: 1.5tv; margin: 0 22tv; background-color: alpha(white, .08); }}
.depot-tv-divider.depot-tv-inset {{ margin-left: 90tv; }}
.depot-tv-search {{ border-spacing: 40tv; }}
.depot-tv-search .tv-field {{ min-width: 836tv; min-height: 88tv; border-radius: 28tv; padding: 0 32tv; font-size: 32tv; }}
.depot-tv-search .tv-field text {{ font-size: 32tv; }}
.depot-tv-search .tv-field {{ border-spacing: 18tv; }}
.depot-tv-search .tv-field image {{ -gtk-icon-size: 28tv; margin-right: 0; color: {m('ink_3')}; }}
.lumaui-tv-stage label.depot-tv-hint {{ font-size: 24tv; color: {m('ink_3')}; margin: 24tv 0; }}
"""


def _box(vertical: bool = False, *css: str, **props) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, **props)
    for name in css:
        box.add_css_class(name)
    return box


def _label(text: str, *css: str, wrap: bool = False, lines: int = 0, **props) -> Gtk.Label:
    ellipsize = props.pop("ellipsize", Pango.EllipsizeMode.END)
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
        label.set_ellipsize(ellipsize)
        label.set_single_line_mode(True)
    return label


def _fact(value: str, after: str = "", *, before: str = "", style: str = "depot-tv-fact") -> Gtk.Widget:
    """One of an app page's facts: its value in ink between quieter words ("Version 1.0.4")."""
    line = Gtk.Box()
    if before:
        line.append(_label(before, ellipsize=Pango.EllipsizeMode.NONE))
    line.append(_label(value, style, ellipsize=Pango.EllipsizeMode.NONE))
    if after:
        line.append(_label(after, ellipsize=Pango.EllipsizeMode.NONE))
    return line



class _Row(Gtk.Box):
    """A row in a card (Updates): lead, title and line, value with a chevron; the key when focused."""

    def __init__(self, title: str, sub: str = "", value: str = "", *, lead: Gtk.Widget | None = None,
                 chevron: bool = True) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.BUTTON)
        self.add_css_class("depot-tv-row")
        if lead is not None:
            lead.set_valign(Gtk.Align.CENTER)
            self.append(lead)
        words = _box(True, valign=Gtk.Align.CENTER, hexpand=True)
        words.append(_label(title, "depot-tv-row-title"))
        if sub:
            words.append(_label(sub, "depot-tv-row-sub"))
        self.append(words)
        self.value_box = _box(False, "depot-tv-row-value", valign=Gtk.Align.CENTER)
        self.value = _label(value, xalign=1)
        self.value.set_visible(bool(value))
        self.value_box.append(self.value)
        if chevron:
            self.value_box.append(icons.image("chevron-right"))
        self.append(self.value_box)
        self.has_lead = lead is not None
        self.dividers: list[Gtk.Widget] = []
        self.update_property([Gtk.AccessibleProperty.LABEL], [" · ".join(x for x in (title, sub, value) if x)])

    def set_value(self, text: str) -> None:
        self.value.set_label(text)
        self.value.set_visible(bool(text))

    def set_tv_focus(self, on: bool) -> None:
        (self.add_css_class if on else self.remove_css_class)("tv-key")
        for divider in self.dividers:
            divider.set_opacity(0.0 if on else 1.0)  # no line touches the focused row


def _card(rows: list[_Row]) -> Gtk.Box:
    card = _box(True, "depot-tv-card", "tv-bar")
    for index, row in enumerate(rows):
        if index:
            divider = Gtk.Box()
            divider.add_css_class("depot-tv-divider")
            if row.has_lead and rows[index - 1].has_lead:
                divider.add_css_class("depot-tv-inset")
            row.dividers.append(divider)
            rows[index - 1].dividers.append(divider)
            card.append(divider)
        card.append(row)
    return card


_brands: dict[str, str] = {}
_brand_css = Gtk.CssProvider()


def _brand_class(colour: str) -> str | None:
    """A style class painting a lead in an app's brand colour (a CSS colour from its AppStream)."""
    rgba = Gdk.RGBA()
    if not colour or not rgba.parse(colour):
        return None
    if colour not in _brands:
        _brands[colour] = f"depot-tv-brand-{len(_brands)}"
        _brand_css.load_from_string("".join(f".depot-tv-lead.{name} {{ background-color: {value}; }}\n"
                                            for value, name in _brands.items()))
        display = Gdk.Display.get_default()
        if display is not None and len(_brands) == 1:
            Gtk.StyleContext.add_provider_for_display(display, _brand_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
    return _brands[colour]


def _glyph_lead(name: str) -> Gtk.Widget:
    lead = Gtk.CenterBox()
    lead.add_css_class("depot-tv-lead")
    lead.set_center_widget(icons.image(name))
    return lead


class _Split(Gtk.Widget):
    """The app page's lower half: About and the side column, 1.2 : 1 with 48 between."""

    __gtype_name__ = "DepotTvSplit"
    GAP, SHARE = 48, 1.2 / 2.2

    def __init__(self, first: Gtk.Widget, second: Gtk.Widget) -> None:
        super().__init__(hexpand=True)
        self.first, self.second = first, second
        first.set_parent(self)
        second.set_parent(self)

    def _unit(self) -> float:
        node = self.get_parent()
        while node is not None and node.find_property("unit") is None:
            node = node.get_parent()
        return float(node.get_property("unit")) if node is not None else 1.0

    def _widths(self, width: int) -> tuple[int, int, int]:
        gap = round(self.GAP * self._unit())
        first = round((width - gap) * self.SHARE)
        return first, gap, max(0, width - gap - first)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            mins = [c.measure(orientation, -1)[0] for c in (self.first, self.second)]
            return sum(mins), sum(mins), -1, -1
        a, _gap, b = self._widths(for_size) if for_size > 0 else (-1, 0, -1)
        heights = [self.first.measure(orientation, a)[1], self.second.measure(orientation, b)[1]]
        return max(heights), max(heights), -1, -1

    def do_size_allocate(self, width, height, _baseline):
        a, gap, b = self._widths(width)
        self.first.allocate(a, height, -1, None)
        self.second.allocate(b, height, -1, Gsk.Transform.new().translate(Graphene.Point().init(a + gap, 0)))

    def do_dispose(self) -> None:
        for child in (self.first, self.second):
            if child.get_parent() is self:
                child.unparent()


class _Feature(Gtk.Widget):
    """Discover's pick: a bar card, the app's screenshot fading in from the right (or its tile)."""

    __gtype_name__ = "DepotTvFeature"

    def __init__(self, text: Gtk.Widget, shot: str | None, tile: Gtk.Widget | None) -> None:
        super().__init__(overflow=Gtk.Overflow.VISIBLE, accessible_role=Gtk.AccessibleRole.BUTTON)
        self.add_css_class("depot-tv-feature")
        self.text, self.tile = text, tile
        self._texture = None
        self._focused = False
        text.set_parent(self)
        if tile is not None:
            tile.set_parent(self)
        if shot:
            from luma_appkit.media_style import TextureLoader

            def arrived(texture):
                if texture is not None:
                    self._texture = texture
                    self.queue_draw()
            self._request = TextureLoader.shared().request(shot, 720, arrived)

    def _unit(self) -> float:
        node = self.get_parent()
        while node is not None and node.find_property("unit") is None:
            node = node.get_parent()
        return float(node.get_property("unit")) if node is not None else 1.0

    def set_tv_focus(self, on: bool) -> None:
        self._focused = on
        self.queue_draw()

    def do_dispose(self) -> None:
        for child in (self.text, self.tile):
            if child is not None and child.get_parent() is self:
                child.unparent()
        self.text = self.tile = None

    def do_measure(self, orientation, for_size):
        u = self._unit()
        if orientation == Gtk.Orientation.VERTICAL:
            h = int(430 * u)
            return h, h, -1, -1
        return 0, int(1728 * u), -1, -1

    def do_size_allocate(self, width, height, _baseline):
        u = self._unit()
        text_w = int(width * 0.46)
        nat = self.text.measure(Gtk.Orientation.VERTICAL, text_w)[1]
        self.text.allocate(text_w, nat, -1, Gsk.Transform.new().translate(
            Graphene.Point().init(56 * u, max(0, (height - nat) / 2))))
        if self.tile is not None:
            tw = self.tile.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            th = self.tile.measure(Gtk.Orientation.VERTICAL, tw)[1]
            self.tile.allocate(tw, th, -1, Gsk.Transform.new().translate(
                Graphene.Point().init(width - tw - 56 * u, (height - th) / 2)))

    def do_snapshot(self, snapshot):
        u = self._unit()
        w, h = self.get_width(), self.get_height()
        rect = Gsk.RoundedRect()
        rect.init_from_rect(Graphene.Rect().init(0, 0, w, h), 40 * u)
        bar = Gdk.RGBA()
        bar.parse(TV["materials"]["bar"])
        drop = Gdk.RGBA()
        drop.parse(TV["materials"]["bar_drop"])
        snapshot.append_outset_shadow(rect, drop, 0, 18 * u, -10 * u, 44 * u)
        if self._focused:
            ring = Gdk.RGBA()
            ring.parse("white")
            snapshot.append_outset_shadow(rect, ring, 0, 0, 5 * u, 0)
        snapshot.push_rounded_clip(rect)
        snapshot.append_color(bar, Graphene.Rect().init(0, 0, w, h))
        if self._texture is not None:
            from luma_appkit.media_style import cover_rect
            area = Graphene.Rect().init(w * 0.38, 0, w * 0.62, h)
            snapshot.push_mask(Gsk.MaskMode.ALPHA)
            clear, solid = Gdk.RGBA(), Gdk.RGBA()
            clear.parse("rgba(0,0,0,0)")
            solid.parse("black")
            stops = []
            for offset, colour in ((0.0, clear), (0.35, solid)):
                stop = Gsk.ColorStop()
                stop.offset, stop.color = offset, colour
                stops.append(stop)
            snapshot.append_linear_gradient(area, Graphene.Point().init(w * 0.38, 0), Graphene.Point().init(w, 0), stops)
            snapshot.pop()
            snapshot.push_clip(area)
            snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.TRILINEAR,
                                           cover_rect(self._texture.get_width(), self._texture.get_height(), area))
            snapshot.pop()
            snapshot.pop()
        snapshot.pop()
        self.snapshot_child(self.text, snapshot)
        if self.tile is not None:
            self.snapshot_child(self.tile, snapshot)




# ── an item as the TV draws it ─────────────────────────────────────────────────

def _white_mark(path: str) -> str:
    """A one-colour brand mark (Simple Icons: paths with no fill, so black) in white, for its brand
    colour behind it, as the mockup's marks are drawn; a copy in the cache, the original untouched."""
    import hashlib
    import os
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return path
    if "fill=" in text.split(">", 1)[0] or "<svg" not in text:
        return path
    folder = os.path.join(GLib.get_user_cache_dir(), "luma-depot", "tv-marks")
    target = os.path.join(folder, hashlib.sha256(text.encode()).hexdigest()[:20] + ".svg")
    if not os.path.isfile(target):
        os.makedirs(folder, exist_ok=True)
        with open(target, "w", encoding="utf-8") as stream:
            stream.write(text.replace("<svg", f'<svg fill="{tv_material("accent_ink")}"', 1))
    return target


def _art(world, item) -> dict:
    """What a wide tile is drawn from: the app's own tile or icon, a mark on its colour, a Look's
    picture, else the name on its hue (TvWideTile's fallbacks)."""
    from .parts.face import _mark_path, icon_path
    from .assets import wallpaper
    if item.kind == "app" and item.app is not None:
        app = item.app
        mark = _mark_path(world, app.mark) if app.mark else None
        if mark and app.mark_colour:
            return {"app_id": app.flatpak_id or None, "icon": _white_mark(mark), "brand": app.mark_colour,
                    "category": item.category}
        icon = icon_path(world, item) or (app.icon_name if app.icon_name and "-symbolic" not in app.icon_name else None)
        return {"app_id": app.flatpak_id or app.app_id.removesuffix(".desktop") or None, "icon": icon,
                "category": item.category}
    if item.kind == "look" and item.look is not None and item.look.art:
        art = item.look.art
        path = world.media_path(art) if "://" in art else wallpaper(art, world.assets / "art" if world.assets else None)
        if path:
            return {"art": path}
    return {"hue": item.hue}


def _input(item) -> str:
    """How it is driven on a TV: the catalogue's word, then the app's own (tv_facts)."""
    app = item.app
    if app is None:
        return tv_facts.REMOTE
    given = getattr(app, "tv_input", "")
    if given in tv_facts.INPUT_TEXT:
        return given
    facts = tv_facts.resolve(None, tv_facts.desktop_info(app), tv_facts.appstream(tv_facts.component_id(app)))
    return facts.input if facts is not None and facts.runs and facts.input else tv_facts.REMOTE


def _lead(world, item) -> Gtk.Widget:
    """A row's lead: the item's icon (or mark on its colour), else its glyph."""
    found = _art(world, item)
    lead = Gtk.CenterBox()
    lead.add_css_class("depot-tv-lead")
    icon = found.get("icon")
    if icon and str(icon).startswith("/"):
        brand = _brand_class(found["brand"]) if found.get("brand") else None
        if brand is not None:
            lead.add_css_class(brand)
            lead.add_css_class("depot-tv-mark")
        else:
            lead.add_css_class("depot-tv-app")
        lead.set_center_widget(Gtk.Image.new_from_file(icon))
    else:
        glyph = item.tweak.icon if item.kind == "tweak" and item.tweak is not None else "package"
        lead.set_center_widget(icons.image(glyph))
    return lead


class DepotTV:
    """The TV presentation of one DepotShell (see the module docstring)."""

    def __init__(self, shell) -> None:
        self.shell = shell
        add_tv_style(_sheet)
        self.stage = TvStage()
        self.stage.add_css_class("depot-tv")
        self.room = TvRoom()
        self.stage.place(self.room, fill=True)
        self.bar = TvTopBar()
        self.tabs = [TvTab(title, icon=icon) for _key, title, icon in TABS]
        # the count's pill: 28 round, growing with more digits; the label inside is only its digits
        self.count_pill = Gtk.CenterBox(visible=False, valign=Gtk.Align.CENTER)
        self.count_pill.add_css_class("depot-tv-count")
        self.count = _label("", ellipsize=Pango.EllipsizeMode.NONE)
        self.count_pill.set_center_widget(self.count)
        self._updates_tab.get_first_child().append(self.count_pill)
        self.bar.set_tabs(self.tabs)
        self.stage.place(self.bar, **TvTopBar.PIN)
        self.focus = TvFocus(self.stage, on_back=self.back, on_change=self._focus_changed)
        for index, (key, _title, _icon) in enumerate(TABS):
            self.focus.add(self.tabs[index], group="tabs", on_focus=lambda k=key: self.open_tab(k),
                           on_activate=lambda k=key: self._enter_page(k))
            self.tabs[index].tv_key = "tab:" + key
        self.focus.set_group_entry("tabs", lambda: self.tabs[self._tab_index()])
        self.tab = self.view = "today"
        self.item: tuple[str, str] | None = None
        self.back_to: tuple[str, str] | None = None
        self.query = ""
        self.category = ""
        self.page: Gtk.Widget | None = None
        self._first: Gtk.Widget | None = None
        self._registered: list[Gtk.Widget] = []
        self._field: TvField | None = None
        self._results: Gtk.Box | None = None
        self._closed = False
        self._change_source = 0
        self._signature: tuple = ()
        self._apply_count()
        self._signature = self._state()
        self.render()
        self.focus.focus(self.tabs[0])

    @property
    def world(self):
        return self.shell.world

    @property
    def store(self):
        return self.shell.store

    @property
    def _updates_tab(self) -> TvTab:
        return self.tabs[[key for key, _t, _i in TABS].index("updates")]

    # ── the shell's state ────────────────────────────────────────────────────

    def close(self) -> None:
        self._closed = True
        if self._change_source:
            GLib.source_remove(self._change_source)
            self._change_source = 0

    def changed(self) -> None:
        """The world changed (catalogue, installs, updates, media): redraw in place, once for a burst of
        changes, and only when what this TV shows changed."""
        if not self._closed and not self._change_source:
            self._change_source = GLib.timeout_add(120, self._apply_change)

    def _apply_change(self) -> bool:
        self._change_source = 0
        state = self._state()
        if state != self._signature:
            self._signature = state
            self._apply_count()
            self.render(keep_focus=True)
        return GLib.SOURCE_REMOVE

    def _state(self) -> tuple:
        world, store = self.world, self.store
        return (id(world), tuple(sorted(getattr(store, "busy", ()) or ())), store.system_phase, world.update_count(),
                tuple(sorted(world.installed.get(world.here, ()))), getattr(store, "_failed", ""))

    def toast(self, title: str, *, lead: str | None = "check") -> None:
        TvToast.show(self.stage, title, lead=lead)

    def done(self, world, words: str = "") -> None:
        """What every store action calls back: the shell takes the world, the TV redraws and says so."""
        self.shell.set_world(world)
        self._apply_count()
        self._signature = self._state()
        self.render(keep_focus=True)
        if words:
            self.toast(words)

    def _here_pending(self) -> list:
        world = self.world
        return [p for p in world.pending if world.here in p.devices and world.item("app", p.app) is not None]

    def _system_waiting(self) -> bool:
        system = self.world.system
        return system is not None and self.store.system_phase != "current" and not system.up_to_date

    def _apply_count(self) -> None:
        count = len(self._here_pending()) + (1 if self._system_waiting() else 0)
        self.count.set_label(str(count))
        self.count_pill.set_visible(count > 0)
        self._updates_tab.update_property([Gtk.AccessibleProperty.LABEL],
                                          [f"Updates, {count} waiting" if count else "Updates"])

    # ── what runs here ───────────────────────────────────────────────────────

    def _runs_here(self, item) -> bool:
        return self.world.can_run(item, self.world.here)

    def _mine(self, item) -> bool:
        return self.world.here in self.world.on_devices(item)

    def _store_items(self, kind: str) -> list:
        if kind == "app":
            return [it for it in rules.listed_apps(self.world) if self._runs_here(it)]
        return [it for it in self.world.items(kind) if self._runs_here(it)]

    # ── navigation ───────────────────────────────────────────────────────────

    def _tab_index(self) -> int:
        return next(i for i, (key, _t, _i) in enumerate(TABS) if key == self.tab)

    def open_tab(self, key: str) -> None:
        if key == self.tab and self.view == key:
            return
        self.tab = self.view = key
        self.item = None
        self.bar.set_open(self._tab_index())
        self.render()

    def _enter_page(self, key: str) -> None:
        self.open_tab(key)
        target = self._field.text if key == "search" and self._field is not None else self._first
        if target is not None:
            self.focus.focus(target)

    def open_item(self, kind: str, item_id: str, from_key: str = "") -> None:
        self.back_to = (self.view, from_key)
        self.view = "item"
        self.item = (kind, item_id)
        self.render()
        if self._first is not None:
            self.focus.focus(self._first)

    def show_place(self, place: str) -> None:
        """A place asked for from outside (a link, --view): the shell's place, here."""
        key = place if any(k == place for k, _t, _i in TABS) else "today"
        self.tab = self.view = key
        self.item = None
        self.render()

    def back(self) -> bool:
        focused = self.focus.focused
        if self.view == "item":
            view, key = self.back_to or (self.tab, "")
            self.view, self.item = view, None
            self.render()
            self.focus.focus(self._find(key) or self._first or self.tabs[self._tab_index()])
            return True
        if focused is not None and self.focus.group_of(focused) != "tabs":
            self.focus.focus(self.tabs[self._tab_index()])
            return True
        return False  # the window minimises: back to the TV home

    def _find(self, key: str) -> Gtk.Widget | None:
        if not key:
            return None
        for widget in self._registered:
            if getattr(widget, "tv_key", "") == key:
                return widget
        return next((tab for tab in self.tabs if tab.tv_key == key), None)

    def _focus_changed(self, widget) -> None:
        if widget is None:
            return
        if self.focus.group_of(widget) == "tabs":
            self.bar.set_open(self._tab_index())
        node = widget.get_parent()
        while node is not None and node is not self.stage:
            if isinstance(node, TvSlide):
                node.reveal(widget)
            node = node.get_parent()
        tile = getattr(widget, "tv_tile", None)
        if tile is not None:
            self._room(tile)

    def _room(self, tile: TvWideTile) -> None:
        art, hues = tile.room
        if art:
            self.room.set_artwork(art)
        elif hues:
            self.room.set_artwork(None, hues=hues)

    # ── drawing ──────────────────────────────────────────────────────────────

    def _add(self, widget: Gtk.Widget, key: str, on_activate=None, *, first: bool = False,
             on_key=None, editable: bool = False) -> Gtk.Widget:
        self.focus.add(widget, group="page", on_activate=on_activate, on_key=on_key, editable=editable)
        widget.tv_key = key
        self._registered.append(widget)
        if first or self._first is None:
            self._first = widget
        return widget

    def render(self, *, keep_focus: bool = False) -> None:
        focused = self.focus.focused
        key = getattr(focused, "tv_key", "") if focused is not None else ""
        in_tabs = focused is not None and self.focus.group_of(focused) == "tabs"
        in_field = self._field is not None and focused is self._field.text
        if self.view == "search" and in_field and keep_focus:
            self._render_results()
            return
        self.focus.clear("page")
        self._registered = []
        self._first = self._field = self._results = None
        if self.page is not None:
            self.stage.remove(self.page)
            self.page = None
        self.bar.set_open(self._tab_index())
        pages = {"today": self._today, "apps": self._apps, "looks": self._looks, "tweaks": self._tweaks,
                 "library": self._library, "updates": self._updates, "search": self._search, "item": self._item_page}
        self.page = pages[self.view]()
        self.stage.place(self.page, **(getattr(self.page, "pin", None)
                                       or {"left": 0, "right": 0, "top": PAGE_TOP, "bottom": 0}))
        self.stage.raise_child(self.bar)
        if keep_focus and not in_tabs:
            target = self._find(key)
            if target is not None:
                self.focus.focus(target)
            elif focused is not None and focused not in self.tabs:
                self.focus.focus(self._first or self.tabs[self._tab_index()])
        elif in_tabs:
            self.focus.refresh()

    def _section(self, page: TvPage, column: Gtk.Box, title: str, small: str, body: Gtk.Widget) -> Gtk.Widget:
        section = _box(True, "depot-tv-sec")
        head = _box(False, "depot-tv-sec-head")
        head.append(_label(title, "tv-h2", valign=Gtk.Align.BASELINE))
        if small:
            head.append(_label(small, "depot-tv-small", valign=Gtk.Align.BASELINE))
        section.append(head)
        section.append(body)
        column.append(section)
        page.add_section(section)
        return section

    def _head(self, column: Gtk.Box, title: str, small: str = "", trail: Gtk.Widget | None = None) -> None:
        head = _box(False, "depot-tv-sec-head", "depot-tv-list-head")
        head.append(_label(title, "tv-h1", valign=Gtk.Align.BASELINE))
        if small:
            head.append(_label(small, "depot-tv-small", valign=Gtk.Align.BASELINE, hexpand=trail is not None))
        if trail is not None:
            trail.set_valign(Gtk.Align.CENTER)
            head.append(trail)
        column.append(head)

    def _meta(self, item) -> tuple[str, bool, str | None]:
        """A tile's quiet line: what state it is in here, else what it is."""
        world, store = self.world, self.store
        if item.id in (getattr(store, "busy", ()) or ()):
            return ("Installing…" if not self._mine(item) else "Working…"), False, None
        pending = world.pending_for(item.id) if item.kind == "app" else None
        if self._mine(item) and pending is not None and world.here in pending.devices:
            return "Update ready", True, "refresh-cw"
        if self._mine(item):
            return {"look": "In use", "tweak": "On"}.get(item.kind, "Installed"), True, "check"
        if item.kind == "look":
            return item.look.kind, False, None
        if item.kind == "tweak":
            return item.tweak.kind, False, None
        return item.line, False, None

    def _tile(self, item, *, width: float | None = None, caption: bool = True, lift: bool = True) -> TvWideTile:
        meta, ok, mark = self._meta(item) if caption else ("", False, None)
        tile = TvWideTile(item.name, width=width, caption=caption, meta=meta, meta_ok=ok, meta_icon=mark, lift=lift,
                          **_art(self.world, item))
        tile.tv_tile = tile
        return tile

    def _item_tile(self, item, key: str, **kwargs) -> TvWideTile:
        tile = self._tile(item, **kwargs)
        return self._add(tile, key, lambda i=item, k=key: self.open_item(i.kind, i.id, k))

    def _shelf(self, items, prefix: str) -> TvShelf:
        return TvShelf([self._item_tile(item, f"{prefix}:{item.kind}:{item.id}") for item in items])

    def _grid(self, items, prefix: str) -> TvTileGrid:
        return TvTileGrid([self._item_tile(item, f"{prefix}:{item.kind}:{item.id}") for item in items], captioned=True)

    def _empty(self, title: str, text: str = "", *, icon: str = "info", actions=()) -> Gtk.Widget:
        state = TvEmptyState(title, text, icon=icon, actions=actions)
        for index, (button, callback) in enumerate(state.buttons):
            self._add(button, f"empty:{index}", callback)
        return state

    def _column(self, *, list_page: bool = False) -> tuple[Gtk.Box, TvPage]:
        column = _box(True, "depot-tv-pad", *(("depot-tv-list-pad",) if list_page else ()))
        return column, TvPage(column)

    # Today (handoff 4): the story, Popular, New and updated, then Looks and Tweaks
    def _today(self) -> Gtk.Widget:
        world = self.world
        column, page = self._column()
        story = world.story
        item = world.item("app", story.app) if story is not None else None
        if item is not None and self._runs_here(item):
            column.append(page.add_section(self._feature(item, story)))
        popular = rules.today_popular(world, 12)
        if popular:
            self._section(page, column, rules.popular_title(world, "Popular"), "", self._shelf(popular, "popular"))
        fresh = rules.today_fresh(world, 12)
        if fresh:
            self._section(page, column, "New and updated", "", self._shelf(fresh, "fresh"))
        for kind, title in (("look", "Looks"), ("tweak", "Tweaks")):
            items = sorted(self._store_items(kind), key=lambda it: it.users, reverse=True)[:8]
            if items:
                self._section(page, column, title, "", self._shelf(items, kind))
        if column.get_first_child() is None:
            column.append(self._empty("Nothing for this TV yet",
                                      "Apps, looks and tweaks that run on a TV show up here as soon as they're listed.",
                                      icon="tv-minimal"))
        return page

    def _feature(self, item, story) -> Gtk.Widget:
        text = _box(True, "depot-tv-feature-text")
        text.append(_label((story.kick or "Today").upper(), "depot-tv-kicker"))
        text.append(_label(story.title or item.name, "depot-tv-feature-title", wrap=True, lines=2))
        if story.blurb:
            text.append(_label(story.blurb, "tv-body", wrap=True, lines=3))
        from .pictures.frames import shot_file
        art = (self.world.media_path(story.art) if "://" in story.art else
               str(self.world.assets / "art" / f"{story.art}.webp") if self.world.assets and story.art else None)
        if not art or not GLib.file_test(art, GLib.FileTest.EXISTS):
            art = next((p for p in (shot_file(self.world, k) for k in item.app.shot_keys) if p), None)
        tile = None if art else self._tile(item, width=560, caption=False, lift=False)
        feature = _Feature(text, art, tile)
        feature.tv_tile = tile or self._tile(item, caption=False, lift=False)
        if self.room._key is None:
            self._room(feature.tv_tile)
        feature.update_property([Gtk.AccessibleProperty.LABEL], [f"{story.title or item.name}"])
        return self._add(feature, f"feature:{item.id}", lambda: self.open_item("app", item.id, f"feature:{item.id}"))

    # Apps (handoff 5): every app that runs here, narrowed to one category with the picker
    def _apps(self) -> Gtk.Widget:
        world = self.world
        column, page = self._column(list_page=True)
        everything = rules.narrowed(world, rules.Narrowing())
        counts = {c.key: sum(1 for it in everything if it.category == c.key) for c in world.categories}
        if self.category and not counts.get(self.category):
            self.category = ""
        shown = rules.narrowed(world, rules.Narrowing(category=self.category))
        chosen = world.category(self.category)
        picker = TvButton(chosen.name if chosen else "All apps", icon="chevron-down")
        self._add(picker, "pick", lambda: self._pick_category(picker, counts, len(everything)))
        line = chosen.about if chosen else (f"{len(everything)} that run on this TV" if everything else "")
        self._head(column, "Apps", line, picker if everything else None)
        if shown:
            column.append(self._grid(shown, "apps"))
        else:
            column.append(self._empty("No apps for this TV yet",
                                      "Apps that run on a TV show up here as soon as they're listed.", icon="package"))
        return page

    def _pick_category(self, anchor: Gtk.Widget, counts: dict, total: int) -> None:
        def pick(key: str) -> None:
            self.category = key
            self.render()
            self.focus.focus(self._find("pick") or self._first)
        commands = [Command("depot.tv.cat.all", f"All apps · {total}", lambda: pick(""), "layout-grid")]
        commands += [Command(f"depot.tv.cat.{c.key}", f"{c.name} · {counts[c.key]}", lambda k=c.key: pick(k), c.icon)
                     for c in self.world.categories if counts.get(c.key)]
        first = 0 if not self.category else 1 + [c.key for c in self.world.categories if counts.get(c.key)].index(self.category)
        TvMenu.open(self.focus, anchor, CommandRegistry((CommandGroup("", tuple(commands)),)), title="Show", first=first)

    # Looks (handoff 6): themes, wallpapers, then icons, sounds and type
    def _looks(self) -> Gtk.Widget:
        column, page = self._column(list_page=True)
        self._head(column, "Looks", "Themes, wallpapers, icons, sounds and type")
        shown = self._store_items("look")
        parts = (("Themes", [it for it in shown if it.look.kind == "Theme"]),
                 ("Wallpapers", [it for it in shown if it.look.kind == "Wallpaper"]),
                 ("Icons, sounds and type", [it for it in shown if it.look.kind not in ("Theme", "Wallpaper")]))
        for title, items in parts:
            if items:
                self._section(page, column, title, "", self._shelf(items, f"looks-{title[:5].lower()}"))
        if not shown:
            column.append(self._empty("No looks for this TV yet",
                                      "Themes and wallpapers that work on a TV show up here as soon as they're listed.",
                                      icon="palette"))
        return page

    # Tweaks (handoff 7): small changes to how Luma works; Install, never a switch
    def _tweaks(self) -> Gtk.Widget:
        column, page = self._column(list_page=True)
        self._head(column, "Tweaks", "Small changes to how Luma works. Turn one off and things go back exactly as they were.")
        items = sorted(self._store_items("tweak"), key=lambda it: it.users, reverse=True)
        if items:
            column.append(self._grid(items, "tweaks"))
        else:
            column.append(self._empty("No tweaks for this TV yet",
                                      "Tweaks that work on a TV show up here as soon as they're listed.",
                                      icon="sliders-horizontal"))
        return page

    # Library (handoff 10): everything you have, here first, then what is on your other devices
    def _library(self) -> Gtk.Widget:
        world = self.world
        column, page = self._column(list_page=True)
        mine = sorted((it for it in world.items("app") if not it.is_system and world.on_devices(it)
                       and (it.seal != "luma" or it.id in world.kept)), key=lambda it: it.name.casefold())
        here = [it for it in mine if self._mine(it)]
        away = [it for it in mine if not self._mine(it) and self._runs_here(it)]
        others = [d.name for d in world.devices if d.id != world.here]
        count = self._here_pending()
        trail = None
        if len(count) > 1:
            trail = TvButton("Update all", icon="refresh-cw")
            self._add(trail, "update-all", self._update_all)
        self._head(column, "Library", "What's on this TV", trail)
        if here:
            column.append(page.add_section(self._grid(here, "lib")))
        else:
            column.append(self._empty("Nothing installed on this TV yet",
                                      "What you install from Depot shows up here.", icon="layers"))
        if away and others:
            self._section(page, column, "On your other devices", "Ready to install here", self._grid(away, "away"))
        return page

    # Updates (handoff 11): Luma's own first, each app, Recently updated, Automatic updates
    def _updates(self) -> Gtk.Widget:
        world = self.world
        column, page = self._column(list_page=True)
        pending = self._here_pending()
        system = world.system
        # this TV's own count, as its badge says (the desktop's lead counts every device)
        waiting = len(pending) + (1 if self._system_waiting() else 0)
        checked = f"checked {system.checked}" if system is not None and system.checked else ""
        lead = " · ".join(x for x in ((f"{waiting} update{'' if waiting == 1 else 's'} waiting" if waiting
                                       else "Everything is up to date"), checked) if x)
        self._head(column, "Updates", lead)
        if system is not None:
            self._group(page, column, "Luma", [self._system_row()])
        if pending:
            rows = [self._update_row(p) for p in pending]
            if len(pending) > 1:
                every = _Row("Update all", f"{len(pending)} apps", "", lead=_glyph_lead("refresh-cw"), chevron=False)
                rows.insert(0, self._add(every, "update-all", self._update_all))
            self._group(page, column, "Apps", rows)
        else:
            empty = self._empty("Every app is up to date", "", icon="check")
            empty.set_halign(Gtk.Align.FILL)
            empty.add_css_class("depot-tv-group")  # a group's distance from the one above
            column.append(page.add_section(empty))
        recent = []
        for index, done in enumerate(world.done[:8]):
            item = None if done.app == "luma" else world.item("app", done.app)
            lead = _lead(world, item) if item is not None else _glyph_lead("sparkles")
            row = _Row(item.name if item is not None else "Luma", " · ".join(x for x in (done.what, done.where) if x),
                       done.when, lead=lead, chevron=item is not None)
            if item is not None:
                self._add(row, f"recent:{index}", lambda i=item, n=index: self.open_item("app", i.id, f"recent:{n}"))
            recent.append(row)
        if recent:
            self._group(page, column, "Recently updated", recent)
        switches = []
        for key, title in AUTO:
            switch = TvSwitchRow(title, active=bool(world.auto.get(key, True)),
                                 on_toggle=lambda on, k=key: self.store.set_automatic(k, on))
            switches.append(self._add(switch, f"auto:{key}", lambda s=switch: s.set_active(not s.get_active())))
        group = _box(True, "depot-tv-group")
        group.append(_label("AUTOMATIC UPDATES", "depot-tv-group-title"))
        for switch in switches:
            group.append(switch)
        column.append(page.add_section(group))
        return page

    def _group(self, page: TvPage, column: Gtk.Box, title: str, rows: list[_Row]):
        group = _box(True, "depot-tv-group")
        group.append(_label(title.upper(), "depot-tv-group-title"))
        group.append(_card(rows))
        column.append(page.add_section(group))
        return group

    def _system_row(self) -> _Row:
        """Luma itself, in the desktop's states: Download, Downloading, Restart now, up to date."""
        system, phase = self.world.system, self.store.system_phase
        if phase == "current" or system.up_to_date:
            row = _Row("Luma is up to date", system.name, "", lead=_glyph_lead("sparkles"), chevron=False)
            return self._add(row, "system")
        title = system.name.replace("Luma 1.0 · ", "Luma ")
        sub = " · ".join(x for x in (system.channel, system.size) if x)
        if phase == "downloaded":
            row = _Row(title, "Ready. Restarting takes about a minute; your apps come back where you left them.",
                       "Restart now", lead=_glyph_lead("sparkles"))
            return self._add(row, "system", lambda: self._ask_restart(row))
        if phase == "downloading":
            row = _Row(title, "Downloading in the background. You can keep watching.", "Downloading…",
                       lead=_glyph_lead("sparkles"), chevron=False)
            return self._add(row, "system")
        row = _Row(title, sub, "Download", lead=_glyph_lead("sparkles"))
        return self._add(row, "system", lambda: self.store.download_system(self.done))

    def _ask_restart(self, anchor: Gtk.Widget) -> None:
        TvMenu.confirm(self.focus, anchor, "Restart now?", "This TV restarts to finish updating Luma. It takes about a minute.",
                       "Restart", lambda: self.store.restart(self.done), cancel="Later")

    def _update_row(self, pending) -> _Row:
        item = self.world.item("app", pending.app)
        busy = item.id in (getattr(self.store, "busy", ()) or ())
        held = self.store.held(item.id)
        value = "Updating…" if busy else ("Review" if held else "Update")
        row = _Row(item.name, " → ".join(x for x in (pending.from_version, pending.to_version) if x)
                   + (f" · {pending.size}" if pending.size else ""), value, lead=_lead(self.world, item))
        key = f"update:{item.id}"
        action = (lambda: None) if busy else (lambda: self.open_item("app", item.id, key)) if held else (
            lambda: self.store.update(item, (self.world.here,), self.done))
        return self._add(row, key, action)

    def _update_all(self) -> None:
        self.store.update_all(self.done)

    # Search (handoff 12): apps, looks and tweaks that run here; the names people arrive with
    def _search(self) -> Gtk.Widget:
        column = _box(True, "depot-tv-search", halign=Gtk.Align.FILL)
        field = TvField(placeholder="Apps, looks, tweaks, or an app you know", action="Search")
        field.set_halign(Gtk.Align.CENTER)
        field.set_value(self.query)
        self._field = field
        self._add(field.text, "field", self._search_done, editable=True)
        field.text.connect("changed", self._typed)
        column.append(field)
        self._results = _box(True)
        column.append(self._results)
        self._render_results()
        page = TvPage(column)
        page.pin = {"left": 96, "right": 96, "top": 190, "bottom": 0}
        return page

    def _typed(self, text: Gtk.Text) -> None:
        if text.get_text() != self.query:
            self.query = text.get_text()
            self._render_results()

    def _search_done(self) -> None:
        first = next((w for w in self._registered if getattr(w, "tv_key", "").startswith("result:")), None)
        if first is not None:
            self.focus.focus(first)

    def _render_results(self) -> None:
        if self._results is None:
            return
        for widget in [w for w in self._registered if getattr(w, "tv_key", "").startswith("result:")]:
            self.focus.remove(widget)
            self._registered.remove(widget)
        while child := self._results.get_first_child():
            self._results.remove(child)
        world, query = self.world, self.query.strip()
        if not query:
            self._results.append(_label("Search what runs on this TV by name, or by an app you know from elsewhere.",
                                        "depot-tv-hint", xalign=0.5, wrap=True))
            return
        q = query.casefold()

        def hit(it) -> bool:
            names = (it.app.alternatives or ()) if it.app is not None else ()
            return q in f"{it.name} {it.line} {it.by}".casefold() or any(q in n.casefold() for n in names)

        switch = next(((name, ids) for name, ids in world.switching if name.casefold() in q or q in name.casefold()), None)
        everything = [it for it in rules.listed_apps(world) if hit(it)] + [it for k in ("look", "tweak")
                                                                           for it in world.items(k) if hit(it)]
        if switch is not None:
            ids = [i for i in switch[1] if world.item("app", i) is not None]
            everything = [world.item("app", i) for i in ids] + [it for it in everything if it.id not in ids]
        hits = [it for it in everything if self._runs_here(it)]
        elsewhere = [it for it in everything if not self._runs_here(it)]
        if switch is not None and hits:
            self._results.append(_label(f"{switch[0]} isn't on Luma. These do the same job:", "depot-tv-hint",
                                        xalign=0.5, wrap=True))
        if hits:
            tiles = [self._item_tile(it, f"result:{it.kind}:{it.id}") for it in hits[:15]]
            self._results.append(TvTileGrid(tiles, captioned=True))
        elif not elsewhere:
            self._results.append(TvEmptyState("Nothing called that yet", "Try another word, or browse Apps.",
                                              icon="search"))
        # Made for computers: named, not shown (the platform's rule); it installs where it runs
        for it in elsewhere[:3]:
            where = next((w for f, w in (("desktop", "computer"), ("tablet", "tablet"), ("phone", "phone"))
                          if f in it.runs), "computer")
            self._results.append(_label(f"{it.name} · Made for {where}s — install it on your {where}",
                                        "depot-tv-hint", xalign=0.5, wrap=True))

    # An item's page (handoff 8): the app, look or tweak, what it does here, and what it reaches
    def _item_page(self) -> Gtk.Widget:
        world = self.world
        item = world.item(*self.item) if self.item else None
        if item is None:
            self.view = self.tab
            return {"today": self._today, "apps": self._apps, "looks": self._looks, "tweaks": self._tweaks,
                    "library": self._library, "updates": self._updates, "search": self._search}[self.tab]()
        app = item.app
        column, page = self._column()
        top = _box(False, "depot-tv-top")
        tile = self._tile(item, width=640, caption=False, lift=False)
        tile.set_valign(Gtk.Align.CENTER)
        top.append(tile)
        self._room(tile)
        info = _box(True, hexpand=True, valign=Gtk.Align.CENTER)
        info.append(_label(item.name, "tv-h1", wrap=True, lines=2))
        dev = _box(False, "depot-tv-dev")
        dev.append(_label(item.by))
        if item.seal in ("luma", "verified"):
            dev.append(icons.image("badge-check"))
        info.append(dev)
        about_line = item.line if item.kind == "app" else (item.look.line if item.look else item.tweak.line)
        if about_line:
            info.append(_label(about_line, "tv-body", "depot-tv-summary", wrap=True, lines=2))
        facts = _box(False, "depot-tv-facts")
        if app is not None and app.rating_count and app.rating:
            stars = round(app.rating)
            line = _box(False, spacing=0)
            line.append(_fact("★" * stars + "☆" * (5 - stars) + " ", "", style="depot-tv-stars"))
            line.append(_fact(f"{app.rating:g}", f" · {rules.fmt_count(app.rating_count)} ratings"))
            facts.append(line)
        elif app is not None:
            facts.append(_fact("", "No ratings yet"))
        if app is not None and app.size_label:
            facts.append(_fact(app.size_label))
        if app is not None and app.version:
            facts.append(_fact(app.version, before="Version "))
        if item.kind != "app":
            facts.append(_fact(rules.fmt_count(item.users), " people use it") if item.users else _fact(
                item.look.kind if item.look else item.tweak.kind))
        if facts.get_first_child() is not None:
            info.append(facts)
        if item.kind == "app" and self._runs_here(item):
            need = _input(item)
            how = _box(False, "depot-tv-input", "tv-well", halign=Gtk.Align.START)
            how.append(icons.image(INPUT_ICON.get(need, "tv-minimal")))
            how.append(_label(tv_facts.INPUT_TEXT[need]))
            if need != tv_facts.REMOTE:
                how.add_css_class("depot-tv-need")
            info.append(how)
        info.append(self._actions(item))
        top.append(info)
        column.append(page.add_section(top))
        if app is not None:
            from .pictures.frames import shot_file
            shots = [p for p in (shot_file(world, k) for k in app.shot_keys) if p]
            if shots:
                row = [self._add(TvShot(path, accessible=f"Screenshot {i + 1} of {len(shots)}"), f"shot:{i}",
                                 lambda i=i, p=shots: TvScreenshotViewer.open(self.focus, p, i))
                       for i, path in enumerate(shots)]
                shelf = TvShelf(row)
                shelf.row.remove_css_class("tv-shelf-row")
                shelf.row.add_css_class("depot-tv-shots")
                self._section(page, column, "Screenshots", "", shelf).add_css_class("depot-tv-shots-sec")
        about = _box(True, "depot-tv-about", hexpand=True)
        about.append(_label("About", "tv-h2"))
        text = (app.description if app is not None else (item.look.about if item.look else item.tweak.about)) or ""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()][:6]
        for paragraph in paragraphs:
            about.append(_label(paragraph, "tv-body", wrap=True))
        if not paragraphs:
            about.append(_label(f"{item.by} hasn’t written a description yet.", "depot-tv-note", wrap=True))
        side = _box(True, hexpand=True)
        release = app.releases[0] if app is not None and app.releases else None
        notes = [n for n in (getattr(release, "notes", ()) or ()) if n] if release is not None else []
        if notes:
            news = _box(True, "depot-tv-news", "tv-bar")
            news.append(_label(f"What’s new in {release.version}", "tv-h2", wrap=True))
            lines = _box(True, "depot-tv-news-items")
            for note in notes:
                one = _box(False, "depot-tv-news-item")
                dot = Gtk.Box(valign=Gtk.Align.START)
                dot.add_css_class("depot-tv-dot")
                one.append(dot)
                one.append(_label(note, "tv-body", wrap=True, hexpand=True))
                lines.append(one)
            news.append(lines)
            side.append(news)
        reach = app.reach if app is not None else ()
        if reach:
            side.append(_label("WHAT IT CAN REACH", "depot-tv-group-title"))
            # read, not focused: rows that take the focus must do something
            side.append(_card([_Row(what, why, lead=_glyph_lead(glyph), chevron=False) for glyph, what, why in reach]))
        if item.kind == "tweak" and item.tweak is not None and (item.tweak.on_text or item.tweak.off):
            side.append(_label("WHAT IT CHANGES", "depot-tv-group-title"))
            rows = [_Row("When it's on", item.tweak.on_text, chevron=False)] if item.tweak.on_text else []
            rows += [_Row("When it's off", item.tweak.off, chevron=False)] if item.tweak.off else []
            side.append(_card(rows))
        split = _Split(about, side)
        if column.get_last_child() is top:  # no screenshots: a section's distance below the top
            split.add_css_class("depot-tv-after-top")
        column.append(page.add_section(split))
        return page

    def _actions(self, item) -> Gtk.Widget:
        """The page's buttons, from the desktop's plan (rules.bar_for): its key, then Remove."""
        world, store = self.world, self.store
        acts = _box(False, "depot-tv-acts")
        plan = rules.bar_for(item, world)
        key = plan.key_button
        busy = item.id in (getattr(store, "busy", ()) or ())
        if busy:
            button = TvProgressButton("Installing…" if not self._mine(item) else "Working…")
            acts.append(self._add(button, "main", lambda: None, first=True))
        elif key is not None:
            action = {"install": lambda: store.install(item, [world.here], self.done),
                      "update": lambda: self._update(item),
                      "open": lambda: self._open(item)}.get(key.key, lambda: None)
            icon = {"install": "download", "update": "refresh-cw", "open": "play" if item.kind == "app" else "check"}.get(key.key)
            label = "Get" if key.key == "install" and item.kind == "app" else key.label
            acts.append(self._add(TvProgressButton(label, icon=icon), "main", action, first=True))
        else:
            quiet = next((b for b in plan.buttons if b.variant == "quiet"), None)
            if quiet is not None:
                acts.append(_label(quiet.label, "depot-tv-note", valign=Gtk.Align.CENTER))
        if "open" in plan.keys and key is not None and key.key == "update":
            acts.append(self._add(TvButton("Open", icon="play"), "open", lambda: self._open(item)))
        if "uninstall" in plan.keys and not busy:
            remove = TvButton("Remove" if item.kind == "app" else "Turn off" if item.kind == "tweak" else "Stop using",
                              icon="trash-2" if item.kind == "app" else None)
            acts.append(self._add(remove, "remove", lambda: self._ask_remove(item, remove)))
        return acts

    def _open(self, item) -> None:
        if item.kind != "app" or item.app is None:
            return
        try:
            info = Gio.DesktopAppInfo.new(f"{item.app.flatpak_id or item.app.app_id.removesuffix('.desktop')}.desktop")
        except TypeError:
            info = None
        if info is None:
            self.toast(f"{item.name} isn't on this TV", lead="circle-alert")
            return
        self.toast(f"Opening {item.name}", lead="play")
        try:
            info.launch([], self.stage.get_display().get_app_launch_context())
        except GLib.Error:
            self.toast(f"{item.name} could not open", lead="circle-alert")

    def _update(self, item) -> None:
        if self.store.held(item.id):
            self.toast(f"{item.name} asks for more. Review it on your computer.", lead="shield-alert")
            return
        self.store.update(item, (self.world.here,), self.done)

    def _ask_remove(self, item, anchor: Gtk.Widget) -> None:
        name = "this TV"
        if item.kind == "app":
            title, body, word = f"Remove {item.name}?", f"It comes off {name}. Your other devices keep it.", "Remove"
        else:
            title, body, word = (f"Turn off {item.name}?", "Things go back exactly as they were.", "Turn off") \
                if item.kind == "tweak" else (f"Stop using {item.name}?", "This TV goes back to its look before.", "Stop using")
        TvMenu.confirm(self.focus, anchor, title, body, word,
                       lambda: self.store.uninstall(item, self.world.here, self.done))
