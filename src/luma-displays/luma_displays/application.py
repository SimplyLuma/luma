# SPDX-License-Identifier: Apache-2.0
"""Displays: arrange what is plugged in, and name the arrangements you keep."""
from __future__ import annotations

import math
import sys

import gi

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("LumaUI", "1")
from gi.repository import Adw, Gio, GLib, Gtk, LumaUI

from luma_appkit import (AppWindow, Command, CommandGroup, CommandRegistry, EmptyState, Island, NavigationRow,
                         NavigationSidebar, SectionLabel, Toast)

from . import model, names
from .canvas import ArrangeCanvas, Tile
from .displayconfig import PERSISTENT, TEMPORARY, VERIFY, DisplayConfig
from .settings import open_display_settings

APP_ID = "org.projectluma.Displays"
# A change someone cannot see (a black or sideways screen) undoes itself
# unless they touch the window within this long.
SAFETY_SECONDS = 15
MODES = (("extend", "Extend", "Use each display as its own space"),
         ("mirror", "Mirror", "Show the same thing on every display"),
         ("single", "One Display", "Turn the others off"))


def _rate(mode: model.Mode) -> str:
    return f"{mode.rate:.0f} Hz" if abs(mode.rate - round(mode.rate)) < 0.1 else f"{mode.rate:.2f} Hz"


class ModeTile(Gtk.ToggleButton):
    """A picture of what the choice does, the way the choice is named."""

    def __init__(self, kind: str, title: str, subtitle: str) -> None:
        super().__init__()
        self.kind = kind
        self.add_css_class("flat")
        self.add_css_class("luma-displays-mode")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for setter in (box.set_margin_top, box.set_margin_bottom, box.set_margin_start, box.set_margin_end):
            setter(8)
        self.picture = Gtk.DrawingArea(content_width=150, content_height=84, halign=Gtk.Align.CENTER)
        self.picture.set_draw_func(self._draw)
        box.append(self.picture)
        self.label = Gtk.Label(label=title)
        self.label.add_css_class("heading")
        box.append(self.label)
        self.set_child(box)
        self.set_tooltip_text(subtitle)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [title, subtitle])
        self.connect("notify::active", lambda *_: self.picture.queue_draw())

    def _draw(self, _area, cr, width, height) -> None:
        ink = self.get_color()
        found, accent = self.get_style_context().lookup_color("luma_accent")
        accent = accent if found else ink
        active = self.get_active()

        def screen(x, y, w, h, lit):
            r = 6
            cr.new_sub_path()
            cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
            cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
            cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
            cr.close_path()
            if lit:
                colour = accent if active else ink
                cr.set_source_rgba(colour.red, colour.green, colour.blue, 0.9 if active else 0.35)
            else:
                cr.set_source_rgba(ink.red, ink.green, ink.blue, 0.10)
            cr.fill()

        def window(x, y, w, h):
            cr.set_source_rgba(1, 1, 1, 0.85 if active else 0.6)
            cr.rectangle(x, y, w, h)
            cr.fill()

        if self.kind == "extend":
            screen(8, 16, 70, 46, True)
            screen(82, 10, 60, 58, True)
            window(18, 26, 30, 20)
            window(96, 22, 34, 26)
        elif self.kind == "mirror":
            screen(8, 16, 64, 46, True)
            screen(78, 16, 64, 46, True)
            window(20, 26, 30, 22)
            window(90, 26, 30, 22)
        else:
            screen(8, 16, 70, 46, True)
            screen(82, 10, 60, 58, False)
            window(18, 26, 34, 22)


class DisplaysWindow(AppWindow):
    def __init__(self, application: "DisplaysApplication", config: DisplayConfig, *, arrival: bool) -> None:
        commands = CommandRegistry((
            CommandGroup("Displays", (
                Command("displays.settings", "Display Settings…", self.open_settings,
                        icon="preferences-desktop-display-symbolic"),
            )),
        ))
        super().__init__(application=application, app_id=APP_ID, title="Displays", icon_name=APP_ID,
                         commands=commands, default_width=880 if not arrival else 640,
                         default_height=720, minimum_width=360, minimum_height=520)
        self.app = application
        self.config = config
        self.arrival = arrival
        self.state = config.state()
        self.kept = self.state.layout.copy()  # what closing without keeping returns to
        self.layout = self.state.layout.copy()
        self.selected = self._primary_connector(self.layout)
        self.viewing_key = self.state.key
        self._syncing = False
        self._safety = 0
        self._settle = 0
        self._build()
        config.connect("changed", lambda *_: self._monitors_changed())
        self.connect("close-request", self._close_requested)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda *_: self._seen())
        self.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda *_: self._seen() or False)
        self.add_controller(keys)
        self._refresh()

    # ── Structure ────────────────────────────────────────────────────────

    def _build(self) -> None:
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        if not self.arrival:
            body.append(self._build_sidebar())
        editor = Island()
        editor.set_hexpand(True)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for setter in (content.set_margin_top, content.set_margin_start, content.set_margin_end):
            setter(24)
        content.set_margin_bottom(18)
        clamp = Adw.Clamp(maximum_size=620, tightening_threshold=480, child=content)
        scroller.set_child(clamp)
        editor.append(scroller)

        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.title = Gtk.Label(xalign=0, wrap=True)
        self.title.add_css_class("title-2")
        self.subtitle = Gtk.Label(xalign=0, wrap=True)
        self.subtitle.add_css_class("dim-label")
        heading.append(self.title)
        heading.append(self.subtitle)
        content.append(heading)

        self.live = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        tiles = Gtk.Box(spacing=8, homogeneous=True)
        self.mode_tiles = {}
        group = None
        for kind, title, subtitle in MODES:
            tile = ModeTile(kind, title, subtitle)
            if group:
                tile.set_group(group)
            group = group or tile
            tile.connect("toggled", self._mode_toggled)
            self.mode_tiles[kind] = tile
            tiles.append(tile)
        self.live.append(tiles)
        self.canvas = ArrangeCanvas()
        self.canvas.on_select = self._select
        self.canvas.on_moved = self._moved
        self.live.append(self.canvas)
        self.hint = Gtk.Label(label="Drag the displays to match where they sit on your desk.", xalign=0.5, wrap=True)
        self.hint.add_css_class("dim-label")
        self.hint.add_css_class("caption")
        self.live.append(self.hint)

        self.options_label = SectionLabel("Display", variant="content")
        self.live.append(self.options_label)
        self.options = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.options.add_css_class("boxed-list")
        self.use_row = Adw.ComboRow(title="Use")
        self.resolution_row = Adw.ComboRow(title="Resolution")
        self.rate_row = Adw.ComboRow(title="Refresh Rate")
        self.scale_row = Adw.ComboRow(title="Scale")
        self.rotation_row = Adw.ComboRow(title="Rotation")
        self.primary_row = Adw.SwitchRow(title="Main Display", subtitle="The Dash and new windows appear here")
        for row in (self.use_row, self.resolution_row, self.rate_row, self.scale_row, self.rotation_row):
            row.connect("notify::selected", self._option_changed)
            self.options.append(row)
        self.primary_row.connect("notify::active", self._primary_changed)
        self.options.append(self.primary_row)
        self.live.append(self.options)
        content.append(self.live)

        self.saved = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.saved_canvas = ArrangeCanvas(editable=False)
        self.saved.append(self.saved_canvas)
        self.saved_note = Gtk.Label(wrap=True, xalign=0.5)
        self.saved_note.add_css_class("dim-label")
        self.saved.append(self.saved_note)
        content.append(self.saved)

        naming = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        naming.add_css_class("boxed-list")
        self.name_row = Adw.EntryRow(title="Arrangement Name")
        self.name_row.set_show_apply_button(True)
        self.name_row.connect("apply", self._renamed)
        naming.append(self.name_row)
        content.append(naming)

        self.message = Gtk.Label(wrap=True, xalign=0)
        self.message.add_css_class("error")
        self.message.set_visible(False)
        content.append(self.message)

        foot = Gtk.Box(spacing=8)
        for setter in (foot.set_margin_start, foot.set_margin_end, foot.set_margin_bottom):
            setter(18)
        settings = Gtk.Button(label="Display Settings…")
        settings.add_css_class("flat")
        settings.connect("clicked", lambda *_: self.open_settings())
        foot.append(settings)
        foot.append(Gtk.Box(hexpand=True))
        self.secondary = Gtk.Button(label="Not Now" if self.arrival else "Revert")
        self.secondary.connect("clicked", lambda *_: self._secondary())
        foot.append(self.secondary)
        self.keep = Gtk.Button(label="Keep Arrangement")
        self.keep.add_css_class("suggested-action")
        self.keep.connect("clicked", lambda *_: self._keep())
        foot.append(self.keep)
        editor.append(foot)
        self.set_default_widget(self.keep)
        body.append(editor)
        self.set_body(body)

    def _build_sidebar(self) -> Gtk.Widget:
        self.sidebar = NavigationSidebar()
        self.sidebar.set_size_request(220, -1)
        self.arrangement_list = self.sidebar.list
        self.arrangement_list.connect("row-selected", self._arrangement_selected)
        return self.sidebar

    # ── Reading ──────────────────────────────────────────────────────────

    @staticmethod
    def _primary_connector(layout: model.Layout) -> str | None:
        primary = next((p for p in layout.placements if p.primary), None)
        return (primary or (layout.placements[0] if layout.placements else None)).connector \
            if layout.placements else None

    def _names(self) -> dict[str, str]:
        short = {m.spec.connector: model.short_name(m) for m in self.state.monitors}
        names.remember_labels({m.spec: short[m.spec.connector] for m in self.state.monitors})
        return short

    def _current_name(self) -> str:
        short = self._names()
        return names.name_for(self.state.key, model.join_names(
            [short[m.spec.connector] for m in sorted(self.state.monitors, key=lambda m: not m.builtin)]))

    def _kind(self) -> str:
        if self.layout.mirror:
            return "mirror"
        if len(self.layout.placements) < len(self.state.monitors):
            return "single"
        return "extend"

    def _refresh(self) -> None:
        self._syncing = True
        try:
            short = self._names()
            if not self.arrival:
                self._fill_arrangements()
            viewing_current = self.viewing_key == self.state.key
            self.live.set_visible(viewing_current)
            self.saved.set_visible(not viewing_current)
            self.keep.set_visible(viewing_current)
            self.secondary.set_visible(viewing_current)
            if not viewing_current:
                self._show_saved()
                return
            listed = model.join_names([short[m.spec.connector]
                                       for m in sorted(self.state.monitors, key=lambda m: not m.builtin)])
            if self.arrival:
                self.title.set_label("New Display Arrangement")
                self.subtitle.set_label(f"{listed} are connected. Choose how to use them."
                                        if len(self.state.monitors) > 1 else f"{listed} is connected.")
            else:
                self.title.set_label(self._current_name())
                self.subtitle.set_label("Connected now")
            self.name_row.set_text(names.load()["names"].get(model.key_text(self.state.key), ""))
            self.name_row.set_title("Arrangement Name" if self.name_row.get_text() else
                                    "Name This Arrangement, Like Home or Office")
            kind = self._kind()
            mirror_possible = model.mirror_layout(self.state) is not None
            self.mode_tiles["mirror"].set_sensitive(mirror_possible)
            self.mode_tiles["mirror"].set_tooltip_text(
                "Show the same thing on every display" if mirror_possible
                else "These displays have no size in common, so they can't mirror")
            for other in self.mode_tiles.values():
                other.set_visible(len(self.state.monitors) > 1)
            self.mode_tiles[kind].set_active(True)
            self.hint.set_visible(kind == "extend" and len(self.layout.placements) > 1)
            self.canvas.set_tiles(self._tiles(), self.selected)
            self._fill_options()
            self._sync_buttons()
        finally:
            self._syncing = False

    def _tiles(self) -> list[Tile]:
        short = self._names()
        rects = model.rects(self.state, self.layout)
        if self.layout.mirror:
            first = self.layout.placements[0]
            mode = self.state.monitor(first.connector).mode(first.mode_id)
            x, y, w, h = rects[first.connector]
            return [Tile("mirror", model.join_names([short[p.connector] for p in self.layout.placements]),
                         f"{mode.width}×{mode.height}", 0, 0, w, h, True)]
        tiles = []
        for p in self.layout.placements:
            mode = self.state.monitor(p.connector).mode(p.mode_id)
            x, y, w, h = rects[p.connector]
            tiles.append(Tile(p.connector, short[p.connector], f"{mode.width}×{mode.height}", x, y, w, h, p.primary))
        return tiles

    def _fill_options(self) -> None:
        kind = self._kind()
        if kind == "mirror":
            placement = self.layout.placements[0]
            self.options_label.set_label("ALL DISPLAYS")
        else:
            placement = next((p for p in self.layout.placements if p.connector == self.selected), None) \
                or self.layout.placements[0]
            self.selected = placement.connector
            self.options_label.set_label(model.short_name(self.state.monitor(placement.connector)).upper())
        monitor = self.state.monitor(placement.connector)
        mode = monitor.mode(placement.mode_id)
        self.use_row.set_visible(kind == "single")
        self._use_choices = [m.spec.connector for m in self.state.monitors]
        self._set_choices(self.use_row, [model.short_name(m) for m in self.state.monitors],
                          self._use_choices.index(placement.connector))
        self._resolution_choices = [m for m in model.resolutions(monitor)
                                    if kind != "mirror" or self._shared_size(m)]
        self._set_choices(self.resolution_row, [f"{m.width} × {m.height}" for m in self._resolution_choices],
                          next((i for i, m in enumerate(self._resolution_choices)
                                if (m.width, m.height) == (mode.width, mode.height)), 0))
        self._rate_choices = model.refresh_modes(monitor, mode)
        self._set_choices(self.rate_row, [_rate(m) for m in self._rate_choices],
                          next((i for i, m in enumerate(self._rate_choices) if round(m.rate) == round(mode.rate)), 0))
        self.rate_row.set_visible(len(self._rate_choices) > 1 and kind != "mirror")
        self._scale_choices = list(mode.scales)
        self._set_choices(self.scale_row, [f"{round(s * 100)}%" for s in self._scale_choices],
                          min(range(len(self._scale_choices)), key=lambda i: abs(self._scale_choices[i] - placement.scale))
                          if self._scale_choices else 0)
        self.scale_row.set_visible(bool(self._scale_choices) and self.state.layout_mode == 1)
        self._rotation_choices = list(model.TRANSFORMS)
        self._set_choices(self.rotation_row, list(model.TRANSFORMS.values()),
                          self._rotation_choices.index(placement.transform) if placement.transform in self._rotation_choices else 0)
        self.rotation_row.set_visible(kind != "mirror")
        self.primary_row.set_visible(kind == "extend" and len(self.layout.placements) > 1)
        self.primary_row.set_active(placement.primary)
        self.primary_row.set_sensitive(not placement.primary)

    def _shared_size(self, mode: model.Mode) -> bool:
        return all(any((m.width, m.height) == (mode.width, mode.height) and not m.variable for m in other.modes)
                   for other in self.state.monitors)

    @staticmethod
    def _set_choices(row: Adw.ComboRow, labels: list[str], selected: int) -> None:
        row.set_model(Gtk.StringList.new(labels))
        if labels:
            row.set_selected(max(0, min(selected, len(labels) - 1)))

    def _fill_arrangements(self) -> None:
        listing = self.arrangement_list
        self.sidebar.clear()
        self.sidebar.append_section("Arrangements")
        arrangements = model.read_arrangements()
        keys = [self.state.key] + [a.key for a in arrangements if a.key != self.state.key]
        self._arrangements = {a.key: a for a in arrangements}
        for key in keys:
            fallback = model.join_names([names.label(s) for s in
                                         sorted(key, key=lambda s: not s.connector.startswith(("eDP", "LVDS", "DSI")))])
            title = names.name_for(key, fallback)
            row = NavigationRow(title, icon_name="video-display-symbolic",
                                trailing="Now" if key == self.state.key else "")
            row.displays_key = key
            listing.append(row)
            if key == self.viewing_key:
                listing.select_row(row)

    def _show_saved(self) -> None:
        arrangement = self._arrangements.get(self.viewing_key)
        fallback = model.join_names([names.label(s) for s in self.viewing_key])
        self.title.set_label(names.name_for(self.viewing_key, fallback))
        self.subtitle.set_label(f"{len(self.viewing_key)} displays" if len(self.viewing_key) != 1 else "1 display")
        tiles = []
        if arrangement:
            for spec, x, y, width, height, scale, transform, primary in arrangement.placements:
                w, h = round(width / scale), round(height / scale)
                if transform % 2:
                    w, h = h, w
                tiles.append(Tile(spec.connector + spec.serial, names.label(spec),
                                  f"{width}×{height}", x, y, w, h, primary))
        self.saved_canvas.set_tiles(tiles, None)
        self.saved_note.set_label(f"Connect {fallback} to change how they're arranged. "
                                  "Luma uses this arrangement whenever they're plugged in together.")
        self.name_row.set_text(names.load()["names"].get(model.key_text(self.viewing_key), ""))
        self.name_row.set_title("Arrangement Name")

    def _sync_buttons(self) -> None:
        changed = model.apply_arguments(self.state, self.layout.copy()) != model.apply_arguments(self.state, self.kept.copy())
        if not self.arrival:
            self.secondary.set_sensitive(changed)
        self.keep.set_label("Keep Arrangement")

    # ── Changes ──────────────────────────────────────────────────────────

    def _try(self, layout: model.Layout, *, risky: bool) -> bool:
        """Show a layout now; it is remembered only when kept."""
        self.message.set_visible(False)
        try:
            self.config.apply(self.state, layout, VERIFY)
            self.config.apply(self.state, layout, TEMPORARY)
        except GLib.Error as error:
            self.message.set_label("That arrangement can't be used: " + error.message.split(": ")[-1])
            self.message.set_visible(True)
            self._refresh()
            return False
        self.layout = layout
        self._fresh_serial()
        if risky:
            self._arm_safety()
        return True

    def _fresh_serial(self) -> None:
        # Every apply advances Mutter's serial; the next apply must name it.
        try:
            state = self.config.state()
        except GLib.Error:
            return
        if state.key == self.state.key:
            self.state = state

    def _arm_safety(self) -> None:
        if self._safety:
            GLib.source_remove(self._safety)
        self._seen_since = False
        self._safety = GLib.timeout_add_seconds(SAFETY_SECONDS, self._safety_expired)

    def _seen(self) -> None:
        if self._safety:
            GLib.source_remove(self._safety)
            self._safety = 0

    def _safety_expired(self) -> bool:
        self._safety = 0
        self._restore(self.kept)
        self.message.set_label("The previous arrangement is back, because nothing happened for a while. "
                               "If the new one looked right, choose it again and move the pointer.")
        self.message.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _restore(self, layout: model.Layout) -> None:
        try:
            self.config.apply(self.state, layout.copy(), TEMPORARY)
            self.layout = layout.copy()
            self._fresh_serial()
        except GLib.Error:
            pass

    def _mode_toggled(self, tile: ModeTile) -> None:
        if self._syncing or not tile.get_active() or tile.kind == self._kind():
            return
        if tile.kind == "mirror":
            layout = model.mirror_layout(self.state)
        elif tile.kind == "single":
            layout = model.single_layout(self.state, self.selected or self._primary_connector(self.layout))
        else:
            layout = model.extend_layout(model.State(self.state.serial, self.state.monitors, self.layout,
                                                     self.state.layout_mode, self.state.supports_mirroring,
                                                     self.state.can_change_layout_mode))
        if layout is None or not self._try(layout, risky=True):
            self._refresh()

    def _select(self, connector: str) -> None:
        if connector != "mirror":
            self.selected = connector
            self._syncing = True
            try:
                self._fill_options()
            finally:
                self._syncing = False

    def _moved(self, connector: str, x: float, y: float) -> None:
        layout = self.layout.copy()
        model.snap(self.state, layout, connector, x, y)
        self._try(layout, risky=False)
        self._refresh()

    def _option_changed(self, row: Adw.ComboRow, _pspec) -> None:
        if self._syncing:
            return
        index = row.get_selected()
        layout = self.layout.copy()
        kind = self._kind()
        targets = layout.placements if kind == "mirror" else \
            [next(p for p in layout.placements if p.connector == self.selected)]
        risky = True
        if row is self.use_row:
            layout = model.single_layout(self.state, self._use_choices[index])
            self.selected = self._use_choices[index]
        elif row is self.resolution_row:
            wanted = self._resolution_choices[index]
            for p in targets:
                monitor = self.state.monitor(p.connector)
                mode = max((m for m in monitor.modes if (m.width, m.height) == (wanted.width, wanted.height)
                            and not m.variable), key=lambda m: m.rate)
                p.mode_id = mode.id
                if p.scale not in mode.scales:
                    p.scale = mode.preferred_scale if mode.preferred_scale in mode.scales else 1.0
        elif row is self.rate_row:
            targets[0].mode_id = self._rate_choices[index].id
        elif row is self.scale_row:
            for p in targets:
                p.scale = self._scale_choices[index]
        elif row is self.rotation_row:
            targets[0].transform = self._rotation_choices[index]
        if kind == "extend":
            model.reflow_after_resize(self.state, layout, targets[0].connector)
        if not self._try(layout, risky=risky):
            return
        self._refresh()

    def _primary_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        if self._syncing or not row.get_active():
            return
        layout = self.layout.copy()
        for p in layout.placements:
            p.primary = p.connector == self.selected
        self._try(layout, risky=False)
        self._refresh()

    def _renamed(self, row: Adw.EntryRow) -> None:
        names.rename(self.viewing_key, row.get_text())
        self._refresh()

    def _keep(self) -> None:
        if self.name_row.get_text().strip():
            names.rename(self.state.key, self.name_row.get_text())
        try:
            self.config.apply(self.state, self.layout.copy(), PERSISTENT)
        except GLib.Error as error:
            self.message.set_label("The arrangement couldn't be kept: " + error.message.split(": ")[-1])
            self.message.set_visible(True)
            return
        self._seen()
        self._fresh_serial()
        self.kept = self.layout.copy()
        if self.arrival:
            self._finish()
            return
        self._refresh()

    def _secondary(self) -> None:
        if self.arrival:
            names.dismiss(self.state.key)
            self._restore(self.kept)
            self._finish()
            return
        self._seen()
        self._restore(self.kept)
        self._refresh()

    def _finish(self) -> None:
        self._seen()
        if self.app.window is self:
            self.app.window = None
        self.destroy()

    def _close_requested(self, *_args) -> bool:
        if self.app.window is self:
            self.app.window = None
        self._seen()
        if self.arrival:
            names.dismiss(self.state.key)
        if model.apply_arguments(self.state, self.layout.copy()) != model.apply_arguments(self.state, self.kept.copy()):
            self._restore(self.kept)
        return False

    def _arrangement_selected(self, _listing, row) -> None:
        if row is None or self._syncing:
            return
        self.viewing_key = row.displays_key
        self._refresh()

    def _monitors_changed(self) -> None:
        # Our own applies and cables plugged one after another both arrive
        # here; wait for the displays to settle before reading them.
        if self._settle:
            GLib.source_remove(self._settle)
        self._settle = GLib.timeout_add(1500, self._reload)

    def _reload(self) -> bool:
        self._settle = 0
        try:
            state = self.config.state()
        except GLib.Error:
            return GLib.SOURCE_REMOVE
        if state.key != self.state.key:
            if self.arrival and (len(state.monitors) < 2 or model.is_familiar(state, model.read_arrangements())):
                # The second cable arrived and made an arrangement Luma knows.
                self._finish()
                return GLib.SOURCE_REMOVE
            self.state = state
            self.kept = state.layout.copy()
            self.layout = state.layout.copy()
            self.selected = self._primary_connector(self.layout)
            self.viewing_key = state.key
        else:
            self.state = state
        self._refresh()
        return GLib.SOURCE_REMOVE

    def open_settings(self) -> None:
        open_display_settings(lambda: Toast.show(self, 'Display settings are unavailable on this desktop.'))


ARRIVAL_LOOK_MS = 1000
ARRIVAL_LOOKS = 3


class DisplaysApplication(Adw.Application):
    """Opened from the apps list, or asked by the Shell through the `arrival`
    action once displays have settled into an arrangement it doesn't know."""

    def __init__(self, config_factory=DisplayConfig) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("arrival", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open only if the displays plugged in form an arrangement Luma doesn't know", None)
        self._config_factory = config_factory
        self.config: DisplayConfig | None = None
        self.window: DisplaysWindow | None = None
        self._arrival_source = 0
        self._arrival_looks: list = []

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        LumaUI.init()
        arrival = Gio.SimpleAction.new("arrival", None)
        arrival.connect("activate", lambda *_: self.arrive())
        self.add_action(arrival)

    def do_activate(self) -> None:
        self._open(arrival=False)

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict().end().unpack()
        if options.get("arrival"):
            self.arrive()
        else:
            self.activate()
        return 0

    def arrive(self) -> None:
        """Ask about the displays only once they have stayed an unfamiliar arrangement.

        At login and while cables are going in, Mutter passes through
        arrangements for a moment. Opening on the first look put a window on
        screen that closed itself a second later, a black flash at every
        login. Look several times, and open only if every look agrees.
        """
        if self.window is not None or self._arrival_source:
            return
        key = self._unfamiliar_key()
        if key is None:
            return
        self.hold()
        self._arrival_looks = [key]
        self._arrival_source = GLib.timeout_add(ARRIVAL_LOOK_MS, self._look_at_arrival)

    def _get_config(self):
        if self.config is None:
            self.config = self._config_factory()
        return self.config

    def _unfamiliar_key(self):
        try:
            state = self._get_config().state()
        except GLib.Error:
            return None
        if len(state.monitors) < 2 or model.is_familiar(state, model.read_arrangements()) \
                or names.dismissed(state.key):
            return None
        return state.key

    def _look_at_arrival(self) -> bool:
        key = self._unfamiliar_key()
        if key is not None and key == self._arrival_looks[-1]:
            self._arrival_looks.append(key)
            if len(self._arrival_looks) < ARRIVAL_LOOKS:
                return GLib.SOURCE_CONTINUE
            print(f"Displays: asking about an arrangement not seen before: {key}", file=sys.stderr, flush=True)
            self._open(arrival=True)
        self._arrival_source = 0
        self._arrival_looks = []
        self.release()
        return GLib.SOURCE_REMOVE

    def _open(self, *, arrival: bool) -> None:
        if self.window is None:
            try:
                self.window = DisplaysWindow(self, self._get_config(), arrival=arrival)
            except GLib.Error:
                self.window = AppWindow(application=self, app_id=APP_ID,
                    title='Displays', icon_name=APP_ID, commands=CommandRegistry(()),
                    minimum_width=360, default_width=560, default_height=380)
                def retry(*_args):
                    old = self.window
                    self.window = None
                    self.config = None
                    self._open(arrival=False)
                    old.destroy()
                repair = self.window
                def repair_closed(*_args):
                    if self.window is repair:
                        self.window = None
                    return False
                repair.connect('close-request', repair_closed)
                self.window.set_body(EmptyState('Display controls are unavailable',
                    'This desktop is not providing the display configuration service. Your saved arrangements are preserved.',
                    'video-display-symbolic', primary=('Try again', retry)))
        self.window.present()


def main(argv: list[str] | None = None) -> int:
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Displays")
    return DisplaysApplication().run(sys.argv if argv is None else argv)
