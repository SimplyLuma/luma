# SPDX-License-Identifier: Apache-2.0
"""Updates (handoff 11): one summary line, the release channel at the top right, Luma's own card,
a card per app update, then Automatic updates and Recently updated folded away."""
from __future__ import annotations

from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import (BarWidget, Button, Changelog, Confirm, Disclosure, Inset, RichMenuItem, Switch,
                         Tooltip, icons)
from luma_appkit.menus import bar_menu

from ..assets import data_file, wallpaper
from ..model import rules
from ..parts.face import Backdrop, face, paint_gradient
from ..parts.grid import Grid
from .base import Page, intro, label, section
from .common import opener

AUTO = (("apps", "Update apps overnight", "On every device, while it charges"),
        ("luma", "Get Luma updates overnight", "Downloads in the background; you choose when to restart"),
        ("matched", "Keep matched apps on the same version", "A device that wakes catches up before you use it"))


class UpdatesPage(Page):
    place = "updates"

    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.checked = ""
        self.open = {"changelog": False, "auto": False, "recent": False}

    @property
    def channel(self) -> str:
        return self.shell.store.channel

    @property
    def system_state(self) -> str:
        return self.shell.store.system_phase

    def snapshot(self) -> dict:
        return {"open": dict(self.open)}

    def restore(self, state: dict) -> None:
        self.open.update(state.get("open", {}))

    def _system(self):
        """Luma's update for the channel this computer follows, and whether it is up to date."""
        system = self.world.system
        if system is None:
            return None, True
        from ..model.store import SampleStore
        if isinstance(self.shell.store, SampleStore):
            # the sample's other channels (the mockup's chanRel): Nightly has the waiting build
            if self.channel == "beta":
                return replace(system, name="Luma 1.1 · beta October 20", size="286 MB", channel="Beta"), False
            if self.channel == "stable":
                return replace(system, name="Luma 1.0", size="", channel="Stable"), True
        return system, self.system_state == "current"

    def build(self) -> Gtk.Widget:
        shell, world = self.shell, self.world
        system, up_to_date = self._system()
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("p-updates")
        compact = shell.is_compact()
        head = Gtk.Overlay()
        lead = intro("Updates", rules.updates_lead(world, system_waiting=not up_to_date,
                                                   checked=self.checked or (system.checked if system else "")))
        channel = Button(dict((k, n) for k, n, _f in rules.CHANNELS)[self.channel], icon="git-branch", menu=True)
        channel.set_name("dq-chanmenu")
        channel.set_halign(Gtk.Align.START if compact else Gtk.Align.END)
        channel.set_valign(Gtk.Align.START)
        channel.add_css_class("dq-chan")
        channel.update_property([Gtk.AccessibleProperty.LABEL], ["Release channel"])
        channel.connect("clicked", lambda b: self._channel_menu(b))
        self.channel_button = channel
        if compact:  # .dq-compact .dq-chan: static, at the start, above the title
            page.append(channel)
            page.append(lead)
        else:
            head.set_child(lead)
            head.add_overlay(channel)
            page.append(head)
        if system is not None:
            page.append(self._luma_card(system, up_to_date))
        updates = sorted(world.pending, key=lambda p: world.here not in p.devices)
        if updates:
            cards = Grid(lambda w: 1 if shell.is_compact() or w < 680 else 2, column_gap=12, row_gap=12, css="dq-ucards")
            for pending in updates:
                cards.append(self._app_card(pending))
            page.append(section("Apps", cards, note=f"{len(updates)} update{'s' if len(updates) != 1 else ''}"))
        else:
            ok = Gtk.Box()
            ok.add_css_class("dq-uok")
            ok.append(icons.image("circle-check"))
            ok.append(label(f"Every app is up to date on all {len(world.devices)} devices.", "dq-uok-text"))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.add_css_class("dq-sec")
            box.append(ok)
            page.append(box)
        page.append(self._fold("auto", "Automatic updates", "clock", self._auto_rows()))
        page.append(self._fold("recent", "Recently updated", "history", self._recent_rows()))
        return page

    # ── Luma's own card (11.2) ────────────────────────────────────────────

    def _luma_card(self, system, up_to_date: bool) -> Gtk.Widget:
        compact = self.shell.is_compact()
        # .dq-compact .dq-luma: the picture 110 tall across the top, the words under it
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        card.add_css_class("dq-luma")
        if compact:
            card.add_css_class("compact")
        if up_to_date:
            card.add_css_class("ok")
        art = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN)
        art.add_css_class("dq-lart")
        ground = Backdrop(wallpaper("prism", self.world.assets / "art" if self.world.assets else None))
        ground.add_css_class("dq-lart-bg")
        art.set_child(ground)
        logo_path = data_file("luma-logo-white.svg")
        if logo_path:
            logo = Gtk.Image.new_from_file(logo_path)
            logo.set_pixel_size(40 if up_to_date else 64)
            logo.add_css_class("dq-llogo")
            art.add_overlay(logo)
        if compact:
            art.set_size_request(-1, 110)
        else:
            art.set_size_request(120 if up_to_date else 240, -1)
            art.set_hexpand(False)
        card.append(art)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        body.add_css_class("dq-lbody")
        if up_to_date:
            body.append(label("LUMA", "dq-kick"))
            body.append(label("Luma is up to date", "dq-ltitle"))
            body.append(label(system.name, "dq-lsize"))
            card.append(body)
            return card
        body.append(label(f"LUMA · {system.channel.upper()}", "dq-kick"))
        body.append(label(system.name.replace("Luma 1.0 · ", ""), "dq-ltitle"))
        if system.size:
            body.append(label(system.size, "dq-lsize"))
        if self.system_state == "downloading":
            progress = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            progress.add_css_class("dq-lprog")
            words = Gtk.Box()
            words.append(label("Downloading in the background…", "dq-lprog-title"))
            later = label("You can keep working", "dq-lprog-note")
            later.set_hexpand(True)
            later.set_xalign(1)
            words.append(later)
            progress.append(words)
            bar = Gtk.ProgressBar(fraction=0.42)
            bar.add_css_class("dq-lbar")
            progress.append(bar)
            body.append(progress)
        elif self.system_state == "downloaded":
            ready = Gtk.Box()
            ready.add_css_class("dq-lready")
            ready.append(icons.image("circle-check"))
            ready.append(label("Ready. Restarting takes about a minute; your apps come back where you left them.",
                               "dq-lready-text", wrap=True))
            body.append(ready)
        buttons = Gtk.Box()
        buttons.add_css_class("dq-lbtns")
        if self.system_state == "downloaded":
            buttons.append(Button("Restart now", variant=Button.KEY, size=Button.LG, icon="rotate-ccw",
                                  on_click=self._restart))
            buttons.append(Button("Tonight", variant=Button.FILL, size=Button.LG, on_click=self._tonight))
        elif self.system_state == "downloading":
            buttons.append(Button("Downloading…", variant=Button.FILL, size=Button.LG, sensitive=False))
        else:
            buttons.append(Button("Download", variant=Button.KEY, size=Button.LG, icon="download",
                                  on_click=self._download))
        spacer = Gtk.Box(hexpand=True)
        buttons.append(spacer)
        toggle = Gtk.Button(valign=Gtk.Align.CENTER)
        toggle.add_css_class("dq-link")
        inner = Gtk.Box()
        inner.append(Gtk.Label(label="View changelog"))
        inner.append(icons.image("chevron-up" if self.open["changelog"] else "chevron-down"))
        toggle.set_child(inner)
        toggle.update_state([Gtk.AccessibleState.EXPANDED], [self.open["changelog"]])
        toggle.connect("clicked", lambda _b: self._toggle("changelog"))
        buttons.append(toggle)
        body.append(buttons)
        if self.open["changelog"] and system.notes:
            log = Changelog(list(system.notes))
            log.add_css_class("dq-lclog")
            body.append(log)
        card.append(body)
        return card

    def _toggle(self, key: str) -> None:
        self.open[key] = not self.open[key]
        self.shell.refresh_body()

    def _download(self) -> None:
        self.shell.store.download_system(self._system_changed)

    def _system_changed(self, world, words: str) -> None:
        if self.shell.place == "updates" and self.shell.item is None:
            self.shell.done(world, words)

    def _restart(self) -> None:
        self.shell.store.restart(self.shell.done)

    def _tonight(self) -> None:
        self.shell.toast("Luma restarts tonight")

    # ── the release channel (11.3) ────────────────────────────────────────

    def _channel_menu(self, button: Gtk.Widget) -> None:
        rows: list = ["Get Luma updates from"]
        for key, name, fact in rules.CHANNELS:
            rows.append(RichMenuItem(name, subtitle=fact, radio="channel", checked=key == self.channel,
                                     on_activate=lambda k=key: self._ask_channel(k)))
        bar_menu(button, rows, label="Release channel")

    def _ask_channel(self, target: str) -> None:
        if target == self.channel:
            return
        system = self.world.system
        decision = rules.channel_decision(self.channel, target, system.name.replace("Luma 1.0 · ", "") if system else "")

        def confirmed(_checked: bool) -> None:
            self.shell.store.set_channel(target, lambda world, _w: self.shell.done(world, decision.toast))

        Confirm.ask(self.shell, title=decision.title, body=decision.body, action=decision.action,
                    icon="git-branch", destructive=False, on_confirm=confirmed,
                    on_cancel=lambda: self.shell.refresh_body())

    # ── apps (11.4) ───────────────────────────────────────────────────────

    def _app_card(self, pending) -> Gtk.Widget:
        shell, world = self.shell, self.world
        item = world.item("app", pending.app)
        here = world.here in pending.devices
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("dq-ucard")
        head = Gtk.Box()
        head.add_css_class("dq-ucard-head")
        head.append(face(world, item, "sm"))
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        words.append(label(item.name, "dq-ucard-name"))
        meta = Gtk.Box()
        meta.add_css_class("dq-ucard-meta")
        version = Gtk.Box(valign=Gtk.Align.CENTER)
        version.add_css_class("dq-ver")
        version.append(label(f"{pending.from_version} → {pending.to_version}"))
        meta.append(version)
        meta.append(label(pending.size, "dq-ucard-size"))
        words.append(meta)
        head.append(words)

        def update() -> None:
            if shell.store.held(item.id):
                opener(shell, "app", item.id)()  # it asks for more: review it on its page
                return
            shell.store.update(item, (world.here,) if here else pending.devices, shell.done)

        head.append(Button("Update", variant=Button.FILL if here else Button.PLAIN, size=Button.SM, on_click=update))
        card.append(head)
        notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        notes.add_css_class("dq-unotes")
        for note in pending.notes:
            line = Gtk.Box()
            dot = Gtk.Box(valign=Gtk.Align.START, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            dot.add_css_class("dq-unote-dot")  # the list's disc, drawn, not a character
            line.append(dot)
            line.append(label(note, "dq-unote", wrap=True))
            notes.append(line)
        card.append(notes)
        where = Gtk.Box()
        where.add_css_class("dq-uwhere")
        where.append(icons.image("monitor-smartphone"))
        where.append(label(rules.devices_line(world, pending.devices), "dq-uwhere-text", wrap=True))
        card.append(where)
        open_item = Gtk.GestureClick()
        open_item.connect("released", lambda *_a: opener(shell, "app", item.id)())
        words.add_controller(open_item)
        return card

    # ── folds (11.7) ──────────────────────────────────────────────────────

    def _fold(self, key: str, title: str, icon: str, content: Gtk.Widget) -> Gtk.Widget:
        fold = Disclosure(title, icon=icon, content=content, form="card", expanded=self.open[key],
                          on_toggled=lambda on, k=key: self.open.__setitem__(k, on))
        fold.add_css_class("dq-fold")
        return fold

    def _auto_rows(self) -> Gtk.Widget:
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rows.add_css_class("dq-auto")
        auto = self.world.auto
        for key, title, line in AUTO:
            row = Gtk.Box()
            row.add_css_class("dq-autorow")
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            words.append(label(title, "dq-autorow-title"))
            words.append(label(line, "dq-autorow-line", wrap=True))
            row.append(words)
            row.append(Switch(active=bool(auto.get(key)), label=title,
                              on_toggle=lambda on, k=key: self.shell.store.set_automatic(k, on)))
            rows.append(row)
        return rows

    def _recent_rows(self) -> Gtk.Widget:
        world = self.world
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rows.add_css_class("dq-done")
        for done in world.done:
            item = None if done.app == "luma" else world.item("app", done.app)
            row = Gtk.Box()
            row.add_css_class("dq-donerow")
            if item is not None:
                row.append(face(world, item, "xs"))
            else:
                glyph = Gtk.Box()
                glyph.add_css_class("dq-face")
                glyph.add_css_class("f-xs")
                glyph.add_css_class("glyph")
                paint_gradient(glyph, (0.72, 0.13, 290), (0.5, 0.13, 320))
                glyph.set_size_request(26, 26)
                mark = icons.image("sparkles")
                mark.set_hexpand(True)
                glyph.append(mark)
                row.append(glyph)
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            words.append(label(item.name if item else "Luma", "dq-done-name"))
            words.append(label(f"{done.what} · {done.where}", "dq-done-line", ellipsize=True))
            row.append(words)
            when = label(done.when, "dq-done-when", xalign=1)
            when.set_size_request(64, -1)
            if self.shell.is_compact():
                when.set_visible(False)
            row.append(when)
            action = Gtk.Box(halign=Gtk.Align.END)
            action.set_size_request(104, -1)
            if item is None:
                action.append(Button("Go back", variant=Button.FILL, size=Button.SM, icon="undo-2",
                                     on_click=lambda d=done: self._ask_go_back(d.what)))
            row.append(action)
            rows.append(row)
        return rows

    def _ask_go_back(self, version: str) -> None:
        Confirm.ask(self.shell, title=f"Go back to {version}?", icon="undo-2", destructive=False, action="Go back",
                    body="Luma restarts into it.",
                    on_confirm=lambda _c: self.shell.store.go_back(self.shell.done))

    # ── the bar (11.5) ────────────────────────────────────────────────────

    def check_now(self) -> None:
        self.checked = "just now"
        self.shell.store.check(self.shell.done)

    def update_all(self) -> None:
        self.shell.store.update_all(self.shell.done)

    def bar_items(self) -> list:
        items = [BarWidget(Button("Check now", icon="refresh-cw", on_click=self.check_now))]
        _system, up_to_date = self._system()
        if self.world.pending or not up_to_date:
            items.append(BarWidget(Button("Update all", variant=Button.KEY, icon="download", on_click=self.update_all)))
        return items

    # screenshot states
    def demo_changelog(self) -> None:
        self._toggle("changelog")

    def demo_folds(self) -> None:
        self.open["auto"] = self.open["recent"] = True
        self.shell.refresh_body()

    def demo_channel(self) -> None:
        self._channel_menu(self.channel_button)

    def demo_confirm(self) -> None:
        self._ask_channel("beta")

    def demo_ready(self) -> None:
        self.shell.store._phase = "downloaded"
        self.shell.refresh_body()
