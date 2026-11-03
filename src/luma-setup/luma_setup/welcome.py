# SPDX-License-Identifier: Apache-2.0
"""After the first sign-in: the same sheet carries on, "Welcome, Nick".

luma-setup --welcome runs once in the first session of an account that first
boot (or the installer) made: /var/lib/luma-setup/welcome/<user> says so, and
~/.local/state/luma-setup/welcome-done says it is over. The cover the setup
session drew lifts off the desktop as the sheet appears, in the same place
with the same head, so the two read as one motion.

    Make it yours   (desktop only) windows stacking or tiling, the dash style
    Luma Connect    sign in or create an account (Connect's own sign-in)
    Add apps        Depot's collections, installed in the background by Depot

Every page has "I don't want to do this.", which ends it for good with no
confirmation; Settings has each choice. Esc does nothing; Return presses the key.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import system  # noqa: E402
from .model import first_name  # noqa: E402
from .sheet import ScaleBin, box, button, icon, label, prefers_light, snapshot, softened_wallpaper  # noqa: E402

__all__ = ("WelcomeWindow", "pending", "mark_done", "DASH_PRESETS", "apply_dash", "set_tiling")

log = logging.getLogger("luma-setup")

from .session import (  # noqa: E402,F401 - the first session's helpers, without GTK
    SYSTEM_WELCOME, LEAVE, TILING_UUID, SHELL_STATE, DASH_PRESETS, _state_dir, pending, mark_done, carry_accessibility, _shell_settings, current_dash, apply_dash, tiling_on, set_tiling, collections, start_installs, open_connect, connect_account, page_names, first_session, _set_tiling_setting, _notify)

class WelcomeWindow(Gtk.ApplicationWindow):
    """The post-login sheet over their desktop."""

    __gtype_name__ = "LumaSetupWelcomeWindow"

    def __init__(self, application: Gtk.Application, welcome: dict, *, phone: bool = False) -> None:
        super().__init__(application=application, title="Welcome", decorated=False)
        self.add_css_class("su-window")
        self.add_css_class("su-over")
        self.phone = phone
        self.light = prefers_light()
        self.name = first_name(welcome.get("full_name") or GLib.get_real_name() or "")
        self.groups = collections()
        self.pages = page_names(phone, self.groups)
        # First boot's pages, already behind them: one count across both stages.
        self.before = max(0, int(welcome.get("pages_before", 0) or 0))
        self.index = 0
        self.tiling = tiling_on()
        self.dash = current_dash()
        self.chosen: set[str] = set()

        root = Gtk.Overlay()
        for css in ("su", "su-in") + (("su-light",) if self.light else ()) + (("su-ph",) if phone else ()):
            root.add_css_class(css)
        self.root = root
        scrim = Gtk.Box(hexpand=True, vexpand=True)
        scrim.add_css_class("su-scrim")
        root.set_child(scrim)
        # The cover the setup session drew, lifted off the desktop as this appears.
        self.cover = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.COVER, hexpand=True, vexpand=True,
                                 can_target=False)
        texture = softened_wallpaper(self.light)
        if texture is not None and welcome.get("lift", True):
            self.cover.set_paintable(texture)
            root.add_overlay(self.cover)
            # The cover lifts once this window is on screen: the shell held a matching cover until then.
            self.connect("map", lambda *_a: GLib.timeout_add(350, self._lift))
        self.sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, overflow=Gtk.Overflow.HIDDEN)
        self.sheet.add_css_class("su-sheet")
        self.sheet.set_name("su-sheet")
        if phone:
            self.sheet.set_halign(Gtk.Align.FILL)
            self.sheet.set_valign(Gtk.Align.END)
            grab = Gtk.Box(halign=Gtk.Align.CENTER)
            grab.add_css_class("su-grab")
            self.sheet.append(grab)
        else:
            self.sheet.set_halign(Gtk.Align.CENTER)
            self.sheet.set_valign(Gtk.Align.CENTER)
            self.sheet.set_size_request(720, -1)
        self.sheet.append(self._head())
        self.body = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=220,
                               vhomogeneous=False, interpolate_size=True)
        self.body.set_child(self.stack)
        self.sheet.append(self.body)
        self.foot = Gtk.Box(spacing=8)
        self.foot.add_css_class("su-foot")
        self.foot.set_name("su-foot")
        self.sheet.append(self.foot)
        root.add_overlay(self.sheet)
        self.scaler = ScaleBin(root)
        self.scaler.on_resize = self._fit
        self.set_child(self.scaler)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.draw()

    def _lift(self) -> bool:
        target = Adw.CallbackAnimationTarget.new(lambda value: self.cover.set_opacity(value))
        animation = Adw.TimedAnimation.new(self.cover, 1.0, 0.0, 1100, target)
        animation.connect("done", lambda *_a: self.cover.set_visible(False))
        animation.play()
        return False

    def _fit(self) -> None:
        height = self.get_height() / self.scaler.factor
        if height > 0:
            self.body.set_max_content_height(max(200, round(height) - (48 if self.phone else 40) - 60 - 72))

    # -- the frame ----------------------------------------------------------------
    def _head(self) -> Gtk.Widget:
        from .sheet import _logo
        sep = Gtk.Box(valign=Gtk.Align.CENTER)
        sep.add_css_class("su-sep")
        self.dots = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
        self.dots.add_css_class("su-dots")
        head = box(_logo(self.light), sep, label(f"Welcome, {self.name}" if self.name else "Welcome"),
                   Gtk.Box(hexpand=True), self.dots, spacing=10, css=("su-head",))
        head.set_name("su-head")
        return head

    @property
    def page(self) -> str:
        return self.pages[self.index]

    def draw(self) -> None:
        while (child := self.dots.get_first_child()) is not None:
            self.dots.remove(child)
        here = self.before + self.index
        for i in range(self.before + len(self.pages)):
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("su-dot")
            dot.add_css_class("su-on" if i == here else "su-past" if i < here else "su-next")
            self.dots.append(dot)
        title, lede, body, app_icon = {"desk": self._desk, "connect": self._connect, "apps": self._apps}[self.page]()
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.add_css_class("su-body")
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading.add_css_class("su-hg")
        if app_icon:
            picture = Gtk.Image(icon_name=app_icon, pixel_size=60 if not self.phone else 52)
            picture.add_css_class("su-app")
            heading.append(picture)
        heading.append(label(title, "su-title", xalign=0.5, wrap=True))
        heading.append(label(lede, "su-lede", xalign=0.5, wrap=True))
        pane.append(heading)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.add_css_class("su-main")
        main.append(body)
        pane.append(main)
        old = self.stack.get_visible_child()
        self.stack.add_child(pane)
        self.stack.set_visible_child(pane)
        if old is not None:
            GLib.timeout_add(300, lambda: (old.get_parent() is self.stack and self.stack.remove(old), False)[1])
        self._foot()
        self._shots = getattr(self, "_shots", 0) + 1
        snapshot(self, f"w{self._shots:02d}-{self.page}")

    def _foot(self) -> None:
        while (child := self.foot.get_first_child()) is not None:
            self.foot.remove(child)
        leave = button(LEAVE, "su-leave", on_click=self.leave)
        back = button("Back", "su-bt", "su-fill", on_click=lambda: self.go(-1)) if self.index > 0 else None
        skip = None
        last = self.index == len(self.pages) - 1
        if self.page == "connect" and not connect_account():
            skip = button("Skip", "su-bt", "su-quiet", on_click=lambda: self.go(1))
            key = button("Sign in", "su-bt", "su-key", on_click=open_connect)
        elif last:
            key = button("Start using Luma", "su-bt", "su-key", on_click=self.finish)
        else:
            key = button("Continue", "su-bt", "su-key", on_click=lambda: self.go(1))
        key.set_name("su-key")
        self.key = key
        GLib.idle_add(lambda: (key.get_mapped() and key.grab_focus(), False)[1])  # Return goes on
        if self.phone:
            self.foot.set_orientation(Gtk.Orientation.VERTICAL)
            self.foot.set_spacing(4)
            self.foot.append(key)
            if back or skip:
                pair = box(back, skip, homogeneous=True)
                self.foot.append(pair)
            self.foot.append(leave)
        else:
            for part in (leave, Gtk.Box(hexpand=True), back, skip, key):
                if part is not None:
                    self.foot.append(part)

    def go(self, step: int) -> None:
        if self.page == "desk":
            set_tiling(self.tiling)
            apply_dash(self.dash)
        self.index = max(0, min(len(self.pages) - 1, self.index + step))
        self.draw()

    def leave(self) -> None:
        """For good, no questions: Settings has each choice."""
        mark_done("left")
        self.close()

    def finish(self) -> None:
        if self.page == "desk":
            set_tiling(self.tiling)
            apply_dash(self.dash)
        start_installs(sorted(self.chosen))
        mark_done("finished")
        self.close()

    def _key(self, _controller, keyval: int, _code: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            focus = self.get_focus()
            if isinstance(focus, Gtk.Button) and focus is not self.key:
                return False
            self.key.emit("clicked")
            return True
        return False

    # -- Make it yours -------------------------------------------------------------
    def _option(self, group: str, key: str, name: str, line: str, on: bool) -> Gtk.Button:
        glyph = Gtk.Box(valign=Gtk.Align.CENTER)
        glyph.add_css_class("su-gl")
        glyph.add_css_class(f"g-{key}")
        for _i in range(3 if key == "luma" else 2 if key in ("stack", "tile") else 1):
            part = Gtk.Box()
            glyph.append(part)
        words = box(label(name, "su-optn"), label(line, "su-optl"), vertical=True, valign=Gtk.Align.CENTER)
        option = Gtk.Button(child=box(glyph, words, spacing=11), hexpand=True)
        option.add_css_class("su-opt")
        if on:
            option.add_css_class("su-on")
        option.update_property([Gtk.AccessibleProperty.LABEL], [f"{name}, {line}"])
        option.connect("clicked", lambda _b: self._choose(group, key))
        return option

    def _choose(self, group: str, key: str) -> None:
        if group == "win":
            self.tiling = key == "tile"
        else:
            self.dash = key
        self.draw_in_place()

    def draw_in_place(self) -> None:
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.draw()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

    def _desk(self):
        preview = MiniDesktop(self.tiling, self.dash, self.light)
        preview.set_halign(Gtk.Align.CENTER)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        rows.set_margin_top(26)
        for title, group, options in (
                ("Windows", "win", (("stack", "Stacking", "Overlap freely", not self.tiling),
                                    ("tile", "Tiling", "Side by side", self.tiling))),
                ("Dash", "dash", (("luma", "Luma", "Islands", self.dash == "luma"),
                                  ("cupertino", "Cupertino", "One dock", self.dash == "cupertino"),
                                  ("redmond", "Redmond", "Taskbar", self.dash == "redmond")))):
            seg = Gtk.Box(spacing=3, homogeneous=True, hexpand=True)
            seg.add_css_class("su-seg")
            for key, name, line, on in options:
                seg.append(self._option(group, key, name, line, on))
            heading = label(title, "su-rowb")
            heading.set_size_request(76, -1)
            rows.append(box(heading, seg, spacing=12, css=("su-row",)))
        return "Make it yours.", "How should Luma feel?", box(preview, rows, vertical=True), ""

    # -- Luma Connect ---------------------------------------------------------------
    def _connect(self):
        account = connect_account()
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
        card.add_css_class("su-card")
        card.set_size_request(360 if not self.phone else -1, -1)
        if account:
            av = label(account[:1].upper(), "su-av", "su-mid", xalign=0.5)
            av.set_halign(Gtk.Align.CENTER)
            card.append(av)
            card.append(label(f"@{account}", "su-meb", xalign=0.5))
            card.append(label("Your Luma Connect account", "su-mee", xalign=0.5))
        else:
            card.append(label("Sign in with your Luma Connect account, or make one. It opens in your browser.",
                              "su-note", "su-c", xalign=0.5, wrap=True))
            card.append(button("Create an account", "su-link", on_click=open_connect, halign=Gtk.Align.CENTER))
        return ("Luma Connect", "Your files, photos and messages, on every device.", card,
                "org.projectluma.Connect")

    # -- Add apps ------------------------------------------------------------------------
    def _apps(self):
        groups = Gtk.Box(spacing=12, homogeneous=False)
        if self.phone:
            groups.set_orientation(Gtk.Orientation.VERTICAL)
        groups.add_css_class("su-groups")
        for group in self.groups:
            ids = [a["id"] for a in group["apps"]]
            every = all(i in self.chosen for i in ids)
            head = box(box(label(group["name"], "su-gn"),
                           label(f"{len(ids)} app{'s' if len(ids) != 1 else ''}", "su-ge"), vertical=True,
                           hexpand=True),
                       button("Clear" if every else "Add all", "su-all", valign=Gtk.Align.CENTER,
                              on_click=lambda ids=ids, every=every: self._all(ids, every)),
                       spacing=10, css=("su-ghead",))
            tiles = Gtk.Box(spacing=4, homogeneous=True)
            tiles.add_css_class("su-tiles")
            for app in group["apps"]:
                tile = Gtk.Button(child=box(Gtk.Image(icon_name=app["icon"], pixel_size=48),
                                            label(app["name"], "su-tn", xalign=0.5), vertical=True, spacing=3))
                tile.add_css_class("su-tile")
                if app["id"] in self.chosen:
                    tile.add_css_class("su-on")
                tile.update_property([Gtk.AccessibleProperty.LABEL], [app["name"]])
                tile.connect("clicked", lambda _b, i=app["id"]: self._toggle(i))
                tiles.append(tile)
            card = box(head, tiles, vertical=True, css=("su-group",), hexpand=True)
            groups.append(card)
        count = len(self.chosen)
        summary = label(f"{count} app{'s' if count != 1 else ''}" if count else "", "su-sum", xalign=0.5)
        summary.set_visible(bool(count))
        return "Add apps", "Free tools for your next project.", box(groups, summary, vertical=True), \
            "org.projectluma.Depot"

    def _toggle(self, app_id: str) -> None:
        self.chosen.symmetric_difference_update({app_id})
        self.draw_in_place()

    def _all(self, ids: list[str], every: bool) -> None:
        if every:
            self.chosen.difference_update(ids)
        else:
            self.chosen.update(ids)
        self.draw_in_place()


class MiniDesktop(Gtk.Widget):
    """The miniature desktop: the softened wallpaper, two windows, the dash, in the chosen style."""

    __gtype_name__ = "LumaSetupMiniDesktop"

    def __init__(self, tiling: bool, dash: str, light: bool) -> None:
        super().__init__(overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("su-mini")
        self.set_size_request(432, 243)
        self.tiling, self.dash = tiling, dash
        self.wall = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.COVER)
        texture = softened_wallpaper(light)
        if texture is not None:
            self.wall.set_paintable(texture)
        self.wall.set_parent(self)
        self.windows = []
        for name in ("su-wa", "su-wb"):
            win = Gtk.Box()
            win.add_css_class("su-mw")
            win.add_css_class(name)
            inner = Gtk.Box(hexpand=True, vexpand=True)
            inner.add_css_class("su-mi")
            win.append(inner)
            win.set_parent(self)
            self.windows.append(win)
        self.dashbar = Gtk.Box(spacing=4)
        self.dashbar.add_css_class("su-dash")
        self.dashbar.add_css_class(f"su-d-{dash}")
        for part in ("su-ap", "su-fd", "su-st"):
            island = Gtk.Box()
            island.add_css_class("su-isl")
            island.add_css_class(part)
            self.dashbar.append(island)
        self.dashbar.set_parent(self)

    def do_dispose(self) -> None:
        for child in [self.wall, *self.windows, self.dashbar]:
            child.unparent()
        Gtk.Widget.do_dispose(self)

    def do_measure(self, orientation, _for_size):
        return (432, 432, -1, -1) if orientation == Gtk.Orientation.HORIZONTAL else (243, 243, -1, -1)

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        def put(child, x, y, w, h):
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = round(x), round(y), max(1, round(w)), max(1, round(h))
            child.size_allocate(rect, -1)
        put(self.wall, 0, 0, width, height)
        bar = 0.095 if self.dash == "redmond" else 0.135
        if self.tiling:
            h = height * (1 - bar - 0.045)
            put(self.windows[0], width * 0.014, height * 0.025, width * 0.481, h)
            put(self.windows[1], width * 0.505, height * 0.025, width * 0.481, h)
        else:
            put(self.windows[0], width * 0.07, height * 0.07, width * 0.5, height * 0.62)
            put(self.windows[1], width * 0.43, height * 0.19, width * 0.49, height * 0.60)
        if self.dash == "redmond":
            put(self.dashbar, 0, height * (1 - 0.095), width, height * 0.095)
        else:
            dash_w = width * (0.52 if self.dash == "luma" else 0.5)
            put(self.dashbar, (width - dash_w) / 2, height * (1 - 0.024 - 0.086), dash_w, height * 0.086)
