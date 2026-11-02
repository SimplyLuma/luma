# SPDX-License-Identifier: Apache-2.0
"""Depot's TV presentation: the same Depot, at the couch.

Shown inside `DepotWindow` while the shell is in TV posture (docs/developer-platform/
tv-mode.md). The catalogue, the installation, the update agent, jobs, history and
settings are the window's own; this module only draws them with LumaUI's TV parts and
calls the window's own actions (open, update, remove, system update).

Depot's sections sit in the shared top bar as tabs: Discover, Apps, Games, Updates (with
a count) and Search. Only apps that run on this TV are listed anywhere (tv_facts): an app
with no TV facts is not shown, and nothing is listed that has no build for this machine.

What is listed: every app installed here that runs on a TV, in its installed state, plus a
catalogue app that is not installed only when the catalogue says it runs on a TV (its
``tv`` field) and it can be installed here. No catalogue entry carries ``tv`` yet, so for
now every surface lists the installed apps; downloads appear on a TV as soon as the
catalogue publishes ``tv`` for an app, with nothing to change here.

Remote: arrows move to whatever lies that way; moving along the bar opens each tab; OK
opens or acts; Back steps out (the app page, then the bar), then to the TV home.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from luma_appkit import icons  # noqa: E402
from luma_appkit import lumaui_tokens as tokens  # noqa: E402
from luma_appkit.commands import Command, CommandGroup, CommandRegistry  # noqa: E402
from luma_appkit.tv_focus import TvFocus  # noqa: E402
from luma_appkit.tv_media import TvCover, TvRoom, TvSlide  # noqa: E402
from luma_appkit.tv_menu import TvMenu  # noqa: E402
from luma_appkit.tv_parts import TvButton, TvField, TvTab, TvTopBar  # noqa: E402
from luma_appkit.tv_stage import TvStage, add_tv_style, tv_material  # noqa: E402
from luma_appkit.tv_tiles import (  # noqa: E402
    CATEGORY_HUES, TvEmptyState, TvPage, TvProgressButton, TvScreenshotViewer, TvShelf, TvTileGrid, TvWideTile,
)
from luma_appkit.tv_toast import TvToast  # noqa: E402

from . import tv_facts  # noqa: E402
from .home_selection import catalogue_slug, featured_app, luma_shelf_apps  # noqa: E402

TV = tokens.TV
TABS = (("discover", "Discover", None), ("apps", "Apps", None), ("games", "Games", None),
        ("updates", "Updates", None), ("search", "Search", "search"))
INPUT_ICON = {tv_facts.REMOTE: "tv-minimal", tv_facts.CONTROLLER: "gamepad-2", tv_facts.KEYBOARD: "keyboard"}
PAGE_TOP = 150


def _sheet(opaque: bool) -> str:
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    return f"""
.depot-tv-pad {{ padding: 26tv 96tv 140tv; }}
.depot-tv-sec {{ margin-bottom: 54tv; }}
.depot-tv-sec-head {{ border-spacing: 16tv; margin-bottom: 22tv; }}
.depot-tv-small {{ font-size: 20tv; font-weight: 600; color: {m('ink_3')}; }}
.depot-tv-count {{ min-width: 28tv; min-height: 28tv; padding: 0 8tv; margin-left: 10tv; border-radius: 14tv;
  font-size: 17tv; font-weight: 700; color: white; background-color: #e5484d; }}
.depot-tv-feature {{ min-height: 430tv; margin-bottom: 54tv; }}
.depot-tv-feature-text {{ margin-left: 56tv; border-spacing: 14tv; }}
.depot-tv-kicker {{ font-size: 20tv; font-weight: 700; letter-spacing: 1.2tv; color: #e8c15a; }}
.depot-tv-feature-title {{ font-size: 56tv; font-weight: 750; letter-spacing: -1.6tv; }}
.depot-tv-top {{ border-spacing: 60tv; }}
.depot-tv-dev {{ font-size: 24tv; font-weight: 600; color: {m('ink_2')}; margin-top: 10tv; border-spacing: 10tv; }}
.depot-tv-dev image {{ -gtk-icon-size: 24tv; color: #6fa8ff; }}
.depot-tv-summary {{ margin-top: 14tv; }}
.depot-tv-facts {{ margin-top: 22tv; border-spacing: 34tv; font-size: 22tv; font-weight: 600; color: {m('ink_3')}; }}
.depot-tv-stars {{ color: #f2c94c; letter-spacing: 1tv; }}
.depot-tv-input {{ min-height: 52tv; padding: 0 18tv; border-radius: 16tv; border-spacing: 10tv; margin-top: 26tv;
  font-size: 21tv; font-weight: 650; color: {m('ink_2')}; }}
.depot-tv-input image {{ -gtk-icon-size: 24tv; }}
.depot-tv-input.depot-tv-need {{ color: #f0c05a; }}
.depot-tv-acts {{ margin-top: 32tv; border-spacing: 14tv; }}
.depot-tv-two {{ border-spacing: 48tv; }}
.depot-tv-about {{ border-spacing: 16tv; }}
.depot-tv-note {{ font-size: 24tv; color: {m('ink_3')}; }}
.depot-tv-news {{ padding: 28tv; border-radius: 30tv; border-spacing: 14tv; margin-bottom: 34tv; }}
.depot-tv-shots {{ border-spacing: 28tv; }}
.depot-tv-head {{ border-spacing: 8tv; margin-bottom: 8tv; }}
.depot-tv-group {{ margin-top: 34tv; }}
.depot-tv-group-title {{ margin: 0 0 12tv 26tv; font-size: 20tv; font-weight: 650; letter-spacing: 1tv; color: {m('ink_3')}; }}
.depot-tv-card {{ padding: 8tv; border-radius: 30tv; }}
.depot-tv-row {{ min-height: 84tv; padding: 12tv 22tv; border-radius: 22tv; border-spacing: 20tv; }}
.depot-tv-row .depot-tv-lead {{ min-width: 48tv; min-height: 48tv; border-radius: 14tv; background-color: alpha(white, .08); color: {m('ink_2')}; }}
.depot-tv-row .depot-tv-lead image {{ -gtk-icon-size: 26tv; }}
.depot-tv-row .depot-tv-lead.depot-tv-app image {{ -gtk-icon-size: 48tv; }}
.depot-tv-row .depot-tv-lead.depot-tv-app {{ background: none; }}
.depot-tv-row-title {{ font-size: 25tv; font-weight: 600; }}
.depot-tv-row-sub {{ font-size: 19tv; font-weight: 500; color: {m('ink_3')}; margin-top: 3tv; }}
.depot-tv-row-value {{ font-size: 23tv; font-weight: 600; color: {m('ink_2')}; border-spacing: 8tv; }}
.depot-tv-row-value image {{ -gtk-icon-size: 24tv; color: {m('ink_3')}; }}
.depot-tv-row.tv-key {{ color: {m('key_ink')}; }}
.depot-tv-row.tv-key .depot-tv-row-sub, .depot-tv-row.tv-key .depot-tv-row-value,
.depot-tv-row.tv-key .depot-tv-row-value image {{ color: alpha({m('key_ink')}, .62); }}
.depot-tv-row.tv-key .depot-tv-lead {{ background-color: alpha(black, .08); color: {m('key_ink')}; }}
.depot-tv-divider {{ min-height: 1.5tv; margin: 0 22tv; background-color: alpha(white, .08); }}
.depot-tv-divider.depot-tv-inset {{ margin-left: 90tv; }}
.depot-tv-search {{ border-spacing: 40tv; }}
.depot-tv-search .tv-field {{ min-width: 900tv; min-height: 88tv; border-radius: 28tv; padding: 0 32tv; font-size: 32tv; }}
.depot-tv-search .tv-field image {{ -gtk-icon-size: 34tv; margin-right: 18tv; }}
.depot-tv-hint {{ font-size: 24tv; color: {m('ink_3')}; }}
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


def _megabytes(value: int) -> str:
    from .window import megabytes
    return megabytes(value)


def _category(item: tv_facts.TvApp) -> str:
    if item.game:
        return "play"
    for name in getattr(item.app, "categories", ()) or ():
        if name.casefold() in CATEGORY_HUES:
            return name.casefold()
    return "tools"


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


def _app_lead(item: tv_facts.TvApp) -> Gtk.Widget:
    lead = Gtk.CenterBox()
    lead.add_css_class("depot-tv-lead")
    if item.icon is not None:
        lead.add_css_class("depot-tv-app")
        lead.set_center_widget(Gtk.Image.new_from_gicon(item.icon))
    else:
        lead.set_center_widget(icons.image("package"))
    return lead


def _glyph_lead(name: str) -> Gtk.Widget:
    lead = Gtk.CenterBox()
    lead.add_css_class("depot-tv-lead")
    lead.set_center_widget(icons.image(name))
    return lead


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


class DepotTV:
    """The TV presentation of one DepotWindow (see the module docstring)."""

    def __init__(self, window) -> None:
        self.window = window
        add_tv_style(_sheet)
        self.stage = TvStage()
        self.stage.add_css_class("depot-tv")
        self.room = TvRoom()
        self.stage.place(self.room, fill=True)
        self.bar = TvTopBar()
        self.tabs = [TvTab(title, icon=icon) for _key, title, icon in TABS]
        self.count = _label("", "depot-tv-count", xalign=0.5, visible=False)
        self.tabs[3].get_first_child().append(self.count)
        self.bar.set_tabs(self.tabs)
        self.stage.place(self.bar, **TvTopBar.PIN)
        self.focus = TvFocus(self.stage, on_back=self.back, on_change=self._focus_changed)
        for index, (key, _title, _icon) in enumerate(TABS):
            self.focus.add(self.tabs[index], group="tabs", on_focus=lambda k=key: self.open_tab(k),
                           on_activate=lambda k=key: self._enter_page(k))
            self.tabs[index].tv_key = "tab:" + key
        self.focus.set_group_entry("tabs", lambda: self.tabs[self._tab_index()])
        self.tab = "discover"
        self.view = "discover"
        self.app_id = ""
        self.back_to: tuple[str, str] | None = None
        self.query = ""
        self.page: Gtk.Widget | None = None
        self.items: list[tv_facts.TvApp] = []
        self._first: Gtk.Widget | None = None
        self._progress: dict[str, callable] = {}
        self._field: TvField | None = None
        self._results: Gtk.Box | None = None
        self._screens: list[str] = []
        self._closed = False
        self._change_source = 0
        self._signature: tuple = ()
        window.install_listeners.append(self)
        self._apply_count()
        self._signature = self._state()
        self.render()
        self.focus.focus(self.tabs[0])

    # ── the window's state ───────────────────────────────────────────────────

    def close(self) -> None:
        self._closed = True
        if self._change_source:
            GLib.source_remove(self._change_source)
            self._change_source = 0
        if self in self.window.install_listeners:
            self.window.install_listeners.remove(self)

    def changed(self) -> None:
        """The window's data changed (catalogue, installed apps, jobs, updates, media): redraw in
        place, once for a burst of changes, and only when what this TV shows changed."""
        if not self._closed and not self._change_source:
            self._change_source = GLib.timeout_add(120, self._apply_change)

    def _apply_change(self) -> bool:
        self._change_source = 0
        state = self._state()
        if state == self._signature:
            return GLib.SOURCE_REMOVE
        if state[:2] != self._signature[:2]:
            self._apply_count()
        else:
            self._apply_count(items=False)
        self._signature = self._state()
        self.render(keep_focus=True)
        return GLib.SOURCE_REMOVE

    def _state(self) -> tuple:
        """What the TV draws from the window: apps, installed state, jobs, updates, loading, media."""
        window = self.window
        installed = tuple(sorted((key, r.version, r.update_version, r.update_commit, r.managed)
                                 for key, r in window.installed.items()))
        jobs = tuple(sorted((key, job.kind, bool(job.failed)) for key, job in window.jobs.items()))
        system = repr(window.system.state) if window.system is not None else ""
        media = ()
        if self.view == "app" and window.catalogue is not None:
            app = window.catalogue.find(self.app_id)
            media = tuple(self._shot_paths(app)) if app is not None else ()
        elif self.view == "discover" and window.catalogue is not None:
            chosen = featured_app(window.catalogue)
            media = tuple(self._shot_paths(chosen, limit=1)) if chosen is not None else ()
        return (id(window.catalogue), installed, jobs, system, window.loading, window.catalogue_error,
                window.settings.app_updates, media)

    def progress(self, progress) -> None:
        update = self._progress.get(progress.app_id)
        if update is not None:
            update(progress)

    def toast(self, title: str, *, lead: str | None = None) -> None:
        TvToast.show(self.stage, title, lead=lead)

    # install listeners (the window tells every listener about finished jobs)
    def catalogue_loaded(self, _result) -> None:
        pass

    def update_finished(self, app_id: str, result, cancelled: bool) -> None:
        if result.ok and not cancelled:
            self.toast(f"Updated {self._name(app_id)}", lead="check")

    def install_finished(self, app_id: str, result, cancelled: bool) -> None:
        if result.ok and not cancelled:
            self.toast(f"Installed {self._name(app_id)}", lead="check")

    def _name(self, app_id: str) -> str:
        item = self._item(app_id)
        if item is not None:
            return item.app.name
        record = self.window.installed.get(app_id)
        return record.app.name if record is not None and record.app is not None else "the app"

    def _listed(self) -> list[tv_facts.TvApp]:
        """Every app that runs on this TV: installed here, or installable here when the catalogue says
        it runs on a TV (see the module docstring)."""
        window = self.window
        tv_facts.forget()
        pairs: dict[str, tuple] = {}
        catalogue = window.catalogue
        if catalogue is not None:
            for app in catalogue.apps:
                record = window.installed.get(app.app_id)
                if app.unlisted or (record is None and not app.installable):
                    continue
                pairs[app.app_id] = (app, record)
        for record in window.installed.values():
            if record.app_id not in pairs and record.app is not None:
                pairs[record.app_id] = (record.app, record)
        items = []
        for app, record in pairs.values():
            if record is None and not app.installable:
                continue
            item = tv_facts.tv_app(app, record=record)
            if item is None:
                continue
            if record is None and tv_facts.catalogue_entry(app) is None:
                continue  # a download is offered only when the catalogue vouches for it on a TV
            items.append(item)
        return sorted(items, key=lambda item: item.app.name.casefold())

    def _item(self, app_id: str) -> tv_facts.TvApp | None:
        return next((item for item in self.items if item.app.app_id == app_id), None)

    def _record(self, item: tv_facts.TvApp):
        return self.window.installed.get(item.app.app_id)

    def _updates(self) -> list[tv_facts.TvApp]:
        found = []
        for item in self.items:
            record = self._record(item)
            if record is not None and record.has_update:
                found.append(item)
        return found

    def _system_waiting(self) -> bool:
        state = self.window.system.state if self.window.system is not None else None
        return bool(state and (state.available or state.staged or state.restart_required))

    def _apply_count(self, *, items: bool = True) -> None:
        if items:
            self.items = self._listed()
        count = len(self._updates()) + (1 if self._system_waiting() else 0)
        self.count.set_label(str(count))
        self.count.set_visible(count > 0)
        self.tabs[3].update_property([Gtk.AccessibleProperty.LABEL],
                                     [f"Updates, {count} waiting" if count else "Updates"])

    # ── navigation ───────────────────────────────────────────────────────────

    def _tab_index(self) -> int:
        return next(i for i, (key, _t, _i) in enumerate(TABS) if key == self.tab)

    def open_tab(self, key: str) -> None:
        if key == self.tab and self.view == key:
            return
        self.tab = self.view = key
        self.app_id = ""
        self.bar.set_open(self._tab_index())
        self.render()

    def _enter_page(self, key: str) -> None:
        self.open_tab(key)
        target = self._field.text if key == "search" and self._field is not None else self._first
        if target is not None:
            self.focus.focus(target)

    def open_app(self, item: tv_facts.TvApp, from_key: str = "") -> None:
        self.back_to = (self.view, from_key)
        self.view = "app"
        self.app_id = item.app.app_id
        self.render()
        if self._first is not None:
            self.focus.focus(self._first)

    def back(self) -> bool:
        focused = self.focus.focused
        if self.view == "app":
            view, key = self.back_to or (self.tab, "")
            self.view = view
            self.app_id = ""
            self.render()
            target = self._find(key) or self._first or self.tabs[self._tab_index()]
            self.focus.focus(target)
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
        for tab in self.tabs:
            if tab.tv_key == key:
                return tab
        return None

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
        self._registered: list[Gtk.Widget] = []
        self._progress.clear()
        self._first = None
        self._field = None
        self._results = None
        if self.page is not None:
            self.stage.remove(self.page)
            self.page = None
        self.bar.set_open(self._tab_index())
        window = self.window
        if window.catalogue_error and window.catalogue is None and not window.installed:
            self.page = self._failure()
        elif window.loading and window.catalogue is None and not window.installed:
            self.page = self._loading()
        else:
            self.page = {"discover": self._discover, "apps": self._apps, "games": self._games,
                         "updates": self._updates_page, "search": self._search, "app": self._app_page}[self.view]()
        if self.page is not None:
            self.stage.place(self.page, **({"left": 0, "right": 0, "top": PAGE_TOP, "bottom": 0}
                                           if not getattr(self.page, "pin", None) else self.page.pin))
            self.stage.raise_child(self.bar)
        if keep_focus and not in_tabs:
            target = self._find(key)
            if target is not None:
                self.focus.focus(target)
            elif focused is not None and focused not in self.tabs:
                self.focus.focus(self._first or self.tabs[self._tab_index()])
        elif in_tabs:
            self.focus.refresh()

    def _scroll(self, column: Gtk.Widget) -> TvPage:
        return TvPage(column)

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

    def _tile(self, item: tv_facts.TvApp, key: str, *, width: float | None = None, caption: bool = True,
              lift: bool = True) -> TvWideTile:
        app = item.app
        record = self._record(item)
        if record is None:
            meta, ok = app.summary, False
        elif record.has_update:
            meta, ok = "Update ready", True
        else:
            meta, ok = "Installed", True
        component = tv_facts.component_id(app) or (item.info.get_id().removesuffix(".desktop") if item.info else "")
        tile = TvWideTile(app.name, app_id=component or None, icon=item.icon, brand=item.brand or None,
                          category=_category(item), width=width, caption=caption, meta=meta if caption else "",
                          meta_ok=ok, lift=lift)
        tile.tv_tile = tile
        tile.tv_item = item
        return tile

    def _app_tile(self, item: tv_facts.TvApp, key: str, **kwargs) -> TvWideTile:
        tile = self._tile(item, key, **kwargs)
        return self._add(tile, key, lambda i=item, k=key: self.open_app(i, k))

    def _shelf(self, items, prefix: str) -> TvShelf:
        return TvShelf([self._app_tile(item, f"{prefix}:{item.app.app_id}") for item in items])

    def _empty(self, title: str, text: str = "", *, icon: str = "info", actions=()) -> Gtk.Widget:
        state = TvEmptyState(title, text, icon=icon, actions=actions)
        for index, (button, callback) in enumerate(state.buttons):
            self._add(button, f"empty:{index}", callback)
        return state

    def _wrap(self, widget: Gtk.Widget) -> TvPage:
        column = _box(True, "depot-tv-pad")
        column.append(widget)
        return TvPage(column)

    def _loading(self) -> Gtk.Widget:
        column = _box(True, "depot-tv-pad")
        column.append(_label("Loading apps…", "tv-body", xalign=0.5))
        return TvPage(column)

    def _failure(self) -> Gtk.Widget:
        return self._wrap(self._empty("Depot cannot reach the catalogue", self.window.catalogue_error,
                                      icon="circle-alert", actions=(("Try again", self.window.reload),)))

    # Discover
    def _discover(self) -> Gtk.Widget:
        column = _box(True, "depot-tv-pad")
        page = TvPage(column)
        if not self.items:
            column.append(self._empty("No applications found",
                                      "Installed desktop applications appear here automatically.", icon="package"))
            return page
        catalogue = self.window.catalogue
        listed = {item.app.app_id: item for item in self.items}
        chosen = featured_app(catalogue) if catalogue is not None else None
        feature = listed.get(chosen.app_id) if chosen is not None else None
        if feature is not None:
            column.append(page.add_section(self._feature(feature)))
        if catalogue is not None:
            luma = [listed[app.app_id] for app in luma_shelf_apps(catalogue, limit=len(catalogue.apps))
                    if app.app_id in listed]
        else:
            luma = []
        if luma:
            self._section(page, column, "From Luma", "Free, private, and they work together", self._shelf(luma, "luma"))
        from .lumaui_views import STORIES
        by_slug = {catalogue_slug(item.app): item for item in self.items}
        for key, _eyebrow, title, summary, app_ids in STORIES:
            story = [by_slug[slug] for slug in app_ids if slug in by_slug]
            if story:
                self._section(page, column, title, summary, self._shelf(story, f"story-{key}"))
        media = [item for item in self.items if "Media" in (getattr(item.app, "categories", ()) or ())]
        if media:
            self._section(page, column, "Watch and listen", "", self._shelf(media, "media"))
        shown = {item.app.app_id for item in luma + media} | ({feature.app.app_id} if feature else set())
        rest = [item for item in self.items if item.app.app_id not in shown]
        if rest and not (luma or media):
            self._section(page, column, "On this TV", "", self._shelf(rest, "rest"))
        return page

    def _feature(self, item: tv_facts.TvApp) -> Gtk.Widget:
        app = item.app
        text = _box(True, "depot-tv-feature-text")
        eyebrow = "Made for Luma" if getattr(app, "tier", "") == "luma" else "Featured app"
        text.append(_label(eyebrow.upper(), "depot-tv-kicker"))
        text.append(_label(app.name, "depot-tv-feature-title", wrap=True, lines=2))
        blurb = getattr(app, "tagline", "") or app.summary
        if blurb:
            text.append(_label(blurb, "tv-body", wrap=True, lines=3))
        shots = self._shot_paths(app, limit=1)
        tile = None if shots else self._tile(item, "", width=560, caption=False, lift=False)
        feature = _Feature(text, shots[0] if shots else None, tile)
        feature.tv_tile = tile or self._tile(item, "", caption=False, lift=False)
        if self.room._key is None:
            self._room(feature.tv_tile)
        feature.update_property([Gtk.AccessibleProperty.LABEL], [f"{eyebrow}: {app.name}"])
        return self._add(feature, f"feature:{app.app_id}", lambda: self.open_app(item, f"feature:{app.app_id}"))

    # Apps and Games
    def _grid_page(self, items, title: str, small: str, empty: tuple[str, str]) -> Gtk.Widget:
        column = _box(True, "depot-tv-pad")
        page = TvPage(column)
        if not items:
            column.append(self._empty(*empty, icon="package"))
            return page
        head = _box(False, "depot-tv-sec-head")
        head.append(_label(title, "tv-h2", valign=Gtk.Align.BASELINE))
        head.append(_label(small, "depot-tv-small", valign=Gtk.Align.BASELINE))
        column.append(head)
        column.append(TvTileGrid([self._app_tile(item, f"grid:{item.app.app_id}") for item in items]))
        return page

    def _apps(self) -> Gtk.Widget:
        items = [item for item in self.items if not item.game]
        return self._grid_page(items, "Apps", f"{len(items)} that run on this TV",
                               ("No apps on this TV yet", "Apps that run on a TV appear here once they are installed."))

    def _games(self) -> Gtk.Widget:
        items = [item for item in self.items if item.game]
        controllers = sum(item.facts.input == tv_facts.CONTROLLER for item in items)
        small = "Most need a controller" if items and controllers * 2 > len(items) else f"{len(items)} that run on this TV"
        return self._grid_page(items, "Games", small,
                               ("No games on this TV yet", "Games that run on a TV appear here once they are installed."))

    # The app page
    def _shot_paths(self, app, limit: int = 12) -> list[str]:
        paths = []
        for shot in getattr(app, "screenshots", ())[:limit]:
            if shot.url.startswith("file://"):
                path = shot.url.removeprefix("file://")
                if GLib.file_test(path, GLib.FileTest.EXISTS):
                    paths.append(path)
            else:
                path = self.window.media.path(shot.url, shot.sha256)
                if path:
                    paths.append(path)
        return paths

    def _app_page(self) -> Gtk.Widget:
        item = self._item(self.app_id)
        if item is None:
            self.view = self.tab
            return {"discover": self._discover, "apps": self._apps, "games": self._games,
                    "updates": self._updates_page, "search": self._search}[self.tab]()
        app, record = item.app, self._record(item)
        catalogued = app.app_id.startswith("catalog:")
        column = _box(True, "depot-tv-pad")
        page = TvPage(column)
        top = _box(False, "depot-tv-top")
        tile = self._tile(item, "", width=640, caption=False, lift=False)
        tile.set_valign(Gtk.Align.CENTER)
        top.append(tile)
        self._room(tile)
        info = _box(True, hexpand=True, valign=Gtk.Align.CENTER)
        info.append(_label(app.name, "tv-h1", wrap=True, lines=2))
        developer = app.developer if catalogued else ""
        if developer:
            dev = _box(False, "depot-tv-dev")
            dev.append(_label(developer))
            if getattr(app, "developer_verified", "") or getattr(app, "source_verified", False):
                dev.append(icons.image("badge-check"))
            info.append(dev)
        summary = (getattr(app, "tagline", "") or app.summary) if catalogued else (
            item.info.get_description() if item.info is not None else "")
        if summary:
            info.append(_label(summary, "tv-body", "depot-tv-summary", wrap=True, lines=2))
        facts = _box(False, "depot-tv-facts")
        if catalogued and app.rating_count > 0 and app.rating > 0:
            stars = round(app.rating)
            line = _box(False, spacing=0)
            line.append(_label("★" * stars + "☆" * (5 - stars) + " ", "depot-tv-stars"))
            line.append(_label(f"{app.rating:g} · {app.rating_count:,} ratings"))
            facts.append(line)
        size = record.installed_bytes if record is not None else app.installed_bytes
        if size:
            facts.append(_label(_megabytes(size)))
        version = (record.version if record is not None else "") or app.version
        if version:
            facts.append(_label(f"Version {version}"))
        source = getattr(app, "source_label", "") or (app.source_title and f"From {app.source_title}") or ""
        if catalogued and source:
            facts.append(_label(source))
        if facts.get_first_child() is not None:
            info.append(facts)
        how = _box(False, "depot-tv-input", "tv-well", halign=Gtk.Align.START)
        how.append(icons.image(INPUT_ICON.get(item.facts.input, "tv-minimal")))
        how.append(_label(item.facts.input_text or tv_facts.INPUT_TEXT[tv_facts.REMOTE]))
        if item.facts.input != tv_facts.REMOTE:
            how.add_css_class("depot-tv-need")
        info.append(how)
        acts = _box(False, "depot-tv-acts")
        main = self._main_button(item, record)
        acts.append(main)
        if record is not None and record.managed and not app.channel_kind and not app.system_state:
            remove = TvButton("Remove", icon="trash-2")
            acts.append(self._add(remove, "remove", lambda i=item, b=remove: self._ask_remove(i, b)))
        info.append(acts)
        top.append(info)
        column.append(page.add_section(top))
        shots = self._shot_paths(app)
        if shots:
            row = []
            for index, path in enumerate(shots):
                cover = TvCover(shape="landscape", width=512, artwork=path, radius=22,
                                accessible=f"Screenshot {index + 1} of {len(shots)}")
                row.append(self._add(cover, f"shot:{index}", lambda i=index, p=shots: self._open_shot(p, i)))
            shelf = TvShelf(row)
            shelf.row.remove_css_class("tv-shelf-row")
            shelf.row.add_css_class("depot-tv-shots")
            section = self._section(page, column, "Screenshots", "", shelf)
            section.set_margin_top(0)
        two = _box(False, "depot-tv-two", homogeneous=False)
        about = _box(True, "depot-tv-about", hexpand=True)
        about.append(_label("About", "tv-h2"))
        description = app.description if catalogued else ""
        if description.strip():
            for paragraph in [p.strip() for p in description.split("\n\n") if p.strip()][:6]:
                about.append(_label(paragraph, "tv-body", wrap=True))
        else:
            about.append(_label(f"{developer or 'The developer'} hasn’t written a description yet.",
                                "depot-tv-note", wrap=True))
        two.append(about)
        side = _box(True, hexpand=True)
        notes = app.releases[0].description.strip() if catalogued and app.releases and app.releases[0].description else ""
        if notes and app.version:
            news = _box(True, "depot-tv-news", "tv-bar")
            news.append(_label(f"What’s new in {app.version}", "tv-h2", wrap=True))
            news.append(_label(notes, "tv-body", wrap=True))
            side.append(news)
        reach = (record.permissions if record is not None and record.permissions else ()) or app.permissions
        if reach:
            side.append(_label("WHAT IT CAN REACH", "depot-tv-group-title"))
            rows = [_Row(permission.title, permission.detail, chevron=False) for permission in reach]
            for index, row in enumerate(rows):
                self._add(row, f"reach:{index}")
            side.append(_card(rows))
        two.append(side)
        about.set_size_request(-1, -1)
        column.append(page.add_section(two))
        return page

    def _main_button(self, item: tv_facts.TvApp, record) -> Gtk.Widget:
        window, app = self.window, item.app
        job = window.jobs.get(app.app_id)
        if job is not None and job.failed:
            button = TvProgressButton("Try again", icon="refresh-cw")
            action = (lambda: self._update(item)) if job.kind == "update" else (lambda: self._get(item))
        elif job is not None and job.kind in ("update", "install"):
            fraction = job.progress.fraction if job.progress else 0.0
            verb = "Updating" if job.kind == "update" else "Installing"
            button = TvProgressButton(f"{verb} {round(fraction * 100)}%")
            button.set_progress(fraction)
            self._progress[app.app_id] = lambda p, b=button, v=verb: b.set_progress(
                p.fraction, f"{v} {round(p.fraction * 100)}%")
            action = lambda: None  # noqa: E731 - progress is shown, not cancelled, from the remote
        elif job is not None and job.kind == "remove":
            button = TvProgressButton("Removing…")
            action = lambda: None  # noqa: E731
        elif record is not None and record.has_update and record.managed and not window._asks_for_more(record):
            button = TvProgressButton("Update", icon="download")
            action = lambda: self._update(item)
        elif record is not None:
            button = TvProgressButton("Open", icon="play")
            action = lambda: self._open(item)
        else:
            button = TvProgressButton("Get", icon="download")
            action = lambda: self._get(item)
        return self._add(button, "main", action, first=True)

    def _open_shot(self, paths: list[str], index: int) -> None:
        TvScreenshotViewer.open(self.focus, paths, index)

    # actions (the window's own)
    def _open(self, item: tv_facts.TvApp) -> None:
        self.toast(f"Opening {item.app.name}", lead="play")
        try:
            self.window.installer.launch(item.app.app_id, self.window.get_display().get_app_launch_context())
        except Exception:  # noqa: BLE001
            self.toast("Could not open the application", lead="circle-alert")

    def _update(self, item: tv_facts.TvApp) -> None:
        record = self._record(item)
        if record is None or not record.has_update:
            return
        if not self.window._update(item.app.app_id, approve=False, expected_commit=record.update_commit,
                                   expected_installed_commit=record.commit):
            self.render(keep_focus=True)

    def _get(self, item: tv_facts.TvApp) -> None:
        self.window._confirm_install(item.app)

    def _ask_remove(self, item: tv_facts.TvApp, anchor: Gtk.Widget) -> None:
        app = item.app
        registry = CommandRegistry((CommandGroup("", (
            Command("depot.tv.keep", "Keep its data", lambda: self.window._remove_app(app, False), "trash-2"),
            Command("depot.tv.data", "Also remove its data", lambda: self.window._remove_app(app, True), "trash-2",
                    destructive=True),
            Command("depot.tv.cancel", "Cancel", lambda: None, "x"),
        )),))
        TvMenu.open(self.focus, anchor, registry, title=f"Uninstall {app.name}?", first=2)

    # Updates
    def _updates_page(self) -> Gtk.Widget:
        window = self.window
        column = _box(True, "depot-tv-pad")
        page = TvPage(column)
        head = _box(True, "depot-tv-head")
        head.append(_label("Updates", "tv-h1"))
        column.append(page.add_section(head))
        self._group(page, column, "Luma", [self._system_row()])
        updates = self._updates()
        if updates:
            self._group(page, column, "Apps", [self._update_row(item) for item in updates])
        else:
            text = window._no_updates_text()
            empty = self._empty("Every app is up to date", "" if text.startswith("Every app") else text, icon="check")
            empty.set_halign(Gtk.Align.FILL)
            column.append(page.add_section(empty))
        from luma_installer import depot_app_history
        from .updates_page import _when
        try:
            entries = depot_app_history.recent(window._history())
        except Exception:  # noqa: BLE001 - history unreadable: say nothing
            entries = ()
        recent = []
        for entry in entries:
            item = self._item(entry.app_id)
            if item is None:
                continue
            change = " → ".join(part for part in (entry.from_version, entry.to_version) if part)
            verb = "Went back" if entry.kind == "revert" else ("Updated automatically" if entry.automatic else "Updated")
            row = _Row(item.app.name, " · ".join(p for p in (verb, change, _when(entry.at)) if p), lead=_app_lead(item))
            self._add(row, f"recent:{entry.app_id}:{entry.at}", lambda i=item: self.open_app(i, f"recent:{i.app.app_id}"))
            recent.append(row)
            if len(recent) == 8:
                break
        if recent:
            self._group(page, column, "Recently updated", recent)
        automatic = _Row("Update apps automatically", "In the background, when it will not cost you data or battery.",
                         "On" if window.settings.app_updates else "Off", chevron=False)
        self._add(automatic, "automatic")
        self._group(page, column, "Automatic updates", [automatic])
        return page

    def _group(self, page: TvPage, column: Gtk.Box, title: str, rows: list[_Row]):
        group = _box(True, "depot-tv-group")
        group.append(_label(title.upper(), "depot-tv-group-title"))
        group.append(_card(rows))
        column.append(page.add_section(group))
        return group

    def _system_row(self) -> _Row:
        from .lumaui_views import system_update_words
        window = self.window
        state = window.system.state if window.system is not None else None
        title, summary, ready = system_update_words(window, state)
        if state is not None and (state.staged or state.restart_required):
            value, action = "Restart now", self._ask_restart
        elif state is not None and state.downloading:
            value, action = f"Downloading {round(state.progress * 100)}%", (lambda: None)
        elif ready and state is not None and state.service and state.managed:
            value, action = "Download", window._begin_system_update
        elif state is not None and state.service and state.managed:
            value, action = "Check again", window._check_again
        else:
            value, action = "", (lambda: None)
        row = _Row(title, summary, value, lead=_glyph_lead("refresh-cw"), chevron=bool(value))
        return self._add(row, "system", action)

    def _ask_restart(self) -> None:
        anchor = self._find("system")
        registry = CommandRegistry((CommandGroup("", (
            Command("depot.tv.restart", "Restart", self.window._restart_to_finish, "refresh-cw"),
            Command("depot.tv.later", "Later", lambda: None, "x"),
        )),))
        TvMenu.open(self.focus, anchor, registry, title="Restart to finish updating?", first=1)

    def _update_row(self, item: tv_facts.TvApp) -> _Row:
        window = self.window
        record = self._record(item)
        job = window.jobs.get(item.app.app_id)
        sub = " → ".join(part for part in (record.version, record.update_version) if part)
        if job is not None and job.failed:
            value = "Try again"
        elif job is not None:
            value = f"Updating {round((job.progress.fraction if job.progress else 0) * 100)}%"
        elif not record.managed:
            value = ""
        elif window._asks_for_more(record):
            value = "Review"
        else:
            value = "Update"
        row = _Row(item.app.name, sub, value, lead=_app_lead(item))
        if job is not None and not job.failed:
            self._progress[item.app.app_id] = lambda p, r=row: r.set_value(f"Updating {round(p.fraction * 100)}%")
        action = (lambda i=item: self.open_app(i, f"update:{i.app.app_id}")) if value in ("Review", "") else (
            lambda i=item: self._update(i))
        return self._add(row, f"update:{item.app.app_id}", action)

    # Search
    def _search(self) -> Gtk.Widget:
        column = _box(True, "depot-tv-search", halign=Gtk.Align.FILL)
        field = TvField(placeholder="Apps, games, or an app you know", action="Search")
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
        query = self.query.strip()
        if not query:
            self._results.append(_label("Search the apps on this TV by name, or by one you know from elsewhere.",
                                        "depot-tv-hint", xalign=0.5, wrap=True))
            return
        hits = [item for item in self.items if tv_facts.matches(item, query)]
        if not hits:
            empty = TvEmptyState("No results", "Try a different word, or browse a category.", icon="search")
            self._results.append(empty)
            return
        folded = query.casefold()
        named = any(folded in (item.app.name + " " + item.app.summary).casefold() for item in hits)
        if not named:
            alias = next((word for item in hits for word in item.keywords if folded in word.casefold()), "")
            if alias:
                self._results.append(_label(f"On Luma, instead of {alias}:", "depot-tv-hint", xalign=0.5))
        tiles = [self._app_tile(item, f"result:{item.app.app_id}") for item in hits[:10]]
        self._results.append(TvTileGrid(tiles))
