# SPDX-License-Identifier: Apache-2.0
"""The item page (handoff 8): one layout for an app, a Look and a Tweak.

A glowing header (icon, name, one line); then the reading column (gallery, About, What it changes,
What's new, Ratings) beside the side column (facts, Installed on, Technical info), or one column in
the 8.1 order when narrow. Every action is in the bar (8.7); Install on… and Uninstall grow it.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from luma_appkit import BarWidget, Button, Changelog, IconOnlyButton, Tooltip

from ..model import rules
from ..model.world import World
from ..parts.columns import Columns
from ..parts.face import face, face_colour
from ..parts.facts_card import facts_card
from ..parts.gallery import Gallery, Lightbox
from ..parts.glow import Glow
from ..parts.grow_panels import install_on_panel, uninstall_panel
from ..parts.installed_on import installed_on
from ..parts.ratings import ratings
from ..parts.technical_info import technical_info
from ..pictures import pictures_for
from .base import Page, label

#: The side column and the gap beside it (8.1).
SIDE, GAP = 280, 36
TWO_COLUMNS_FROM = 760


class ItemPage(Page):
    place = "item"
    searchable = False

    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.reset()
        self._lightbox: Lightbox | None = None

    def reset(self) -> None:
        self.tech_open = False
        self.picture = 0
        if getattr(self, "trying", False):
            self.shell.try_look(None)
        self.trying = False

    def snapshot(self) -> dict:
        return {"tech": self.tech_open, "picture": self.picture}

    def restore(self, state: dict) -> None:
        self.tech_open = state.get("tech", False)
        self.picture = state.get("picture", 0)

    @property
    def item(self):
        kind, item_id = self.shell.item
        return self.world.item(kind, item_id)

    # ── the page ──────────────────────────────────────────────────────────

    def build(self) -> Gtk.Widget:
        world, item = self.world, self.item
        width = self.shell.scroller.get_width() or 952
        two = width - 64 >= TWO_COLUMNS_FROM and not self.shell.is_phone()
        # The glow sits behind the whole top of the page, in the icon's colour (8.2).
        page = Glow(face_colour(world, item), centre_x=300, centre_y=70)
        page.add_css_class("p-item")
        page.append(self._header(world, item))
        pictures = pictures_for(world, item)
        self.pictures = pictures
        gallery = None
        if pictures:
            self.picture = min(self.picture, len(pictures) - 1)
            gallery = Gallery(pictures, chosen=self.picture, compact=not two,
                              on_choose=lambda i: setattr(self, "picture", i), on_open=self.open_lightbox)
        reading = [w for w in (self._about(item), self._changes(item), self._news(item), self._ratings(item, two))
                   if w is not None]
        side = [facts_card(world, item, on_site=self.open_site),
                installed_on(world, item, on_install=self.install_on_device, on_uninstall=self.ask_uninstall,
                             touch=self.shell.is_phone())]
        store = self.shell.store
        changes = store.permission_changes(item.id) if item.kind == "app" else ()
        if store.asks_for_more(item.id) and world.pending_for(item.id) is not None:
            self.tech_open = True  # an update that asks for more shows what it asks for before Update
        tech = technical_info(item, expanded=self.tech_open, on_toggled=lambda o: setattr(self, "tech_open", o),
                              on_site=self.open_site, changes=changes)
        if tech is not None:
            side.append(tech)
        for index, section in enumerate(reading):
            section.add_css_class("dq-isec")
            if index:
                section.add_css_class("ruled")
        if two:
            main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            main.add_css_class("dq-imain")
            if gallery is not None:
                main.append(gallery)
            for section in reading:
                main.append(section)
            aside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
            aside.add_css_class("dq-iside")
            for card in side:
                aside.append(card)
            page.append(Columns(main, aside, side_width=SIDE, gap=GAP))
        else:
            # 8.1 on a phone: name, gallery, facts, Installed on, Technical info, About, What's new, Ratings
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            column.add_css_class("dq-icol")
            for widget in ([gallery] if gallery is not None else []) + side + reading:
                column.append(widget)
            page.append(column)
        return page

    def _header(self, world: World, item) -> Gtk.Widget:
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.add_css_class("dq-top")
        head = Gtk.Box()
        head.add_css_class("dq-ihead")
        icon = face(world, item, "lg")
        icon.add_css_class("lifted")
        head.append(icon)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        words.append(label(item.name, "dq-iname", wrap=True))
        words.append(label(item.line, "dq-iline", wrap=True))
        head.append(words)
        top.append(head)
        return top

    def _section(self, title: str, body: Gtk.Widget, note: str = "") -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box()
        head.add_css_class("dq-isec-head")
        head.append(label(title, "dq-isec-title"))
        if note:
            head.append(label(note, "dq-isec-note"))
        box.append(head)
        box.append(body)
        return box

    def _about(self, item) -> Gtk.Widget | None:
        text = item.app.description if item.app is not None else (item.look or item.tweak).about
        if not text:
            return None
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for paragraph in text.split("\n\n"):
            para = label(paragraph, "dq-para", wrap=True)
            para.set_max_width_chars(62)
            body.append(para)
        return self._section("About", body)

    def _changes(self, item) -> Gtk.Widget | None:
        tweak = item.tweak
        if tweak is None or not tweak.changes:
            return None
        parts = list(tweak.changes)
        things = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        text = f"{things}." + (" Turning it on or off needs a restart." if tweak.restart else "")
        text += f" Turn it off any time and {'they go' if len(parts) > 1 else 'it goes'} back exactly as before."
        para = label(text, "dq-para", wrap=True)
        para.set_max_width_chars(62)
        return self._section("What it changes", para)

    def _news(self, item) -> Gtk.Widget | None:
        app = item.app
        if app is None or not app.releases or not app.releases[0].notes:
            return None
        release = app.releases[0]
        note = " · ".join(v for v in (release.version, rules.short_date(release.date)) if v)
        return self._section("What's new", Changelog(list(release.notes)), note)

    def _ratings(self, item, wide: bool) -> Gtk.Widget | None:
        block = ratings(item, arrows=wide)
        return None if block is None else self._section("Ratings and reviews", block)

    # ── the bar (8.7) ─────────────────────────────────────────────────────

    def bar_items(self) -> list:
        items = []
        actions = {"open": self.open_it, "install": self.install_here, "update": self.update_here,
                   "update-on": self.update_elsewhere, "try": self.try_it, "install-on": self.grow_install_on,
                   "share": self.share, "uninstall": lambda: self.ask_uninstall(self.world.here)}
        self.bar_buttons = {}
        for button in rules.bar_for(self.item, self.world, trying=self.trying).buttons:
            if button.variant == "quiet":
                words = label(button.label, "dq-bar-none")
                items.append(BarWidget(words))
                continue
            if not button.label:
                widget = IconOnlyButton(button.icon, button.tooltip, on_click=actions[button.key])
                Tooltip.attach(widget, button.tooltip.split(" from ")[0] if button.key == "uninstall" else button.tooltip,
                               label=False)
            else:
                busy = button.variant == "key" and self.item.id in self.shell.store.busy
                widget = Button("Installing…" if busy and button.key == "install" else
                                "Updating…" if busy and button.key == "update" else button.label,
                                variant=Button.KEY if button.variant == "key" else Button.FILL,
                                icon=None if busy else button.icon, tooltip=button.tooltip or None,
                                on_click=actions[button.key], sensitive=not busy)
            if button.key == "try":
                widget.set_chosen(self.trying)  # raised while this window wears the Look
                widget.update_property([Gtk.AccessibleProperty.LABEL], ["Try in this window"])
            self.bar_buttons[button.key] = widget
            items.append(BarWidget(widget))
        return items

    def open_it(self) -> None:
        """Open launches the app (a Look in use or an installed Tweak has nothing more to do)."""
        item = self.item
        if item.kind != "app" or item.app is None:
            return
        from gi.repository import Gio
        try:
            info = Gio.DesktopAppInfo.new(f"{item.app.flatpak_id or item.app.app_id}.desktop")
        except TypeError:
            info = None
        if info is not None:
            info.launch([], None)

    def install_here(self) -> None:
        self.shell.store.install(self.item, [self.world.here], self.shell.done)

    def install_on_device(self, device_id: str) -> None:
        self.shell.store.install(self.item, [device_id], self.shell.done)

    def update_here(self) -> None:
        # Here, under the permissions it lists, Update is the person's approval of what it asks for more.
        store = self.shell.store
        self.shell.store.update(self.item, (self.world.here,), self.shell.done,
                                approve=store.asks_for_more(self.item.id))

    def update_elsewhere(self) -> None:
        pending = self.world.pending_for(self.item.id)
        away = tuple(d for d in pending.devices if d != self.world.here) if pending else ()
        self.shell.store.update(self.item, away, self.shell.done)

    def try_it(self) -> None:
        """Try applies the Look to this window only, until you leave the page (6.3)."""
        self.trying = not self.trying
        look = self.item.look
        self.shell.try_look(look if self.trying else None)
        button = self.bar_buttons.get("try")
        if button is not None:
            button.set_chosen(self.trying)

    def grow_install_on(self) -> None:
        item, world = self.item, self.world

        def confirm(picks: list[str], keep: bool) -> None:
            self.shell.store.install(item, picks, self.shell.done, keep_matched=keep if item.kind == "app" else None)

        self.shell.center.grow("where", lambda: install_on_panel(world, item, on_close=self.shell.center.fold_panel,
                                                                  on_confirm=confirm),
                               anchor=self.bar_buttons.get("install-on"), wide=True)

    def ask_uninstall(self, device_id: str) -> None:
        item, world = self.item, self.world
        device = world.device(device_id)

        def confirm() -> None:
            self.shell.store.uninstall(item, device_id, self.shell.done)

        self.shell.center.grow(f"unin:{device_id}", lambda: uninstall_panel(item, device.name,
                                                                            on_cancel=self.shell.center.fold_panel,
                                                                            on_confirm=confirm),
                               anchor=self.bar_buttons.get("uninstall") if device_id == world.here else None)

    def share(self) -> None:
        item = self.item
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(f"https://simplyluma.com/depot/{item.kind}/{item.id}")
        self.shell.toast(f"Copied a link to {item.name}", kind="copied")

    def open_site(self, site: str) -> None:
        launcher = Gtk.UriLauncher.new(site if "://" in site else f"https://{site}")
        launcher.launch(self.shell, None, None, None)

    # ── the lightbox ──────────────────────────────────────────────────────

    def open_lightbox(self, index: int) -> None:
        if self._lightbox is not None:
            return
        room = (self.shell.overlay.get_width() or 952, self.shell.overlay.get_height() or 686)
        self._lightbox = Lightbox(self.pictures, index, self.close_lightbox, room=room)
        self.shell.overlay.add_overlay(self._lightbox)
        self.shell.center.hide_bar()
        self.shell.back_button.set_visible(False)
        self._lightbox.grab_focus()

    def close_lightbox(self) -> None:
        if self._lightbox is not None:
            self.shell.overlay.remove_overlay(self._lightbox)
            self._lightbox = None
            self.shell.refresh_bar()
            self.shell.back_button.set_visible(not self.shell.is_phone())

    def lightbox_open(self) -> bool:
        return self._lightbox is not None

    # screenshot states (tests/redesign_shot.py --demo)
    def demo_install_on(self) -> None:
        self.grow_install_on()

    def demo_uninstall(self) -> None:
        self.ask_uninstall(self.world.here)

    def demo_lightbox(self) -> None:
        self.open_lightbox(0)

    def demo_tech(self) -> None:
        self.tech_open = True
        self.shell.refresh_body()
