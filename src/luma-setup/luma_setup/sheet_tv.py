# SPDX-License-Identifier: Apache-2.0
"""First-boot setup on a TV: the same four pages, from LumaUI's TV layer, driven by a remote.

The room (the wallpaper, softened, as behind every TV page) and a TV sheet in
the middle of the safe area: the head ("Set up this Luma", the page dots),
the title and line, the page, then Back, Skip where a page may be skipped,
and the key. Lists are TV rows (84 tall); the chosen row is a chip with a
tick, the remote's focus is the key. Text goes in TvFields, which take the
system's TV keyboard; while it is up the sheet stands on it. Sizes are TV
units (one pixel of 1920x1080, scaled to the screen), so a 4K screen is
right from the first frame. Back steps back a page; on Hello it does nothing.

Only loaded in TV posture (sheet.posture()), where the TV layer exists.
"""
from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from luma_appkit import lumaui_tokens as tokens  # noqa: E402
from luma_appkit.tv_focus import TvFocus  # noqa: E402
from luma_appkit.tv_keyboard import watch_tv_keyboard  # noqa: E402
from luma_appkit.tv_media import TvRoom  # noqa: E402
from luma_appkit.tv_parts import TvButton, TvField  # noqa: E402
from luma_appkit.tv_stage import TvStage, add_tv_style, tv_material  # noqa: E402

from . import catalog, system  # noqa: E402
from .model import Setup, host_name, mismatch, strength, username_problem  # noqa: E402
from .sheet import HEAD, OWNER, _taken, _wallpaper_path, prefers_light  # noqa: E402

__all__ = ("SetupTVWindow", "SetupTV")

log = logging.getLogger("luma-setup")
TV = tokens.TV
SHEET = TV["sheet"]
ABOVE_KEYBOARD = TV["keyboard"]["above"]
WIDTH = 1180      # two lists side by side at TV row sizes
LIST_ROWS = 5     # rows a list shows before it scrolls


def _style(opaque: bool) -> str:
    m = lambda name: tv_material(name, opaque=opaque)  # noqa: E731
    return f"""
.su-tv-sheet {{ border-spacing: 0; }}
.su-tv-head {{ border-spacing: 14tv; margin-bottom: 26tv; }}
.su-tv-head .tv-meta {{ color: {m('ink_3')}; }}
.su-tv-dot {{ min-width: 10tv; min-height: 10tv; border-radius: 5tv; background-color: {m('ink_3')}; opacity: .45; }}
.su-tv-dot.on {{ min-width: 30tv; opacity: 1; background-color: {m('ink_2')}; }}
.su-tv-title {{ font-size: 52tv; font-weight: 750; letter-spacing: -1.56tv; }}
.su-tv-lede {{ margin-top: 10tv; margin-bottom: 30tv; }}
.su-tv-lists {{ border-spacing: 24tv; }}
.su-tv-list {{ border-radius: 30tv; padding: 6tv; }}
.su-tv-list > viewport > box, .su-tv-list > box {{ border-spacing: 4tv; }}
.su-tv-lab {{ margin: 0 8tv 12tv; }}
.su-tv-row {{ border-spacing: 16tv; }}
.su-tv-row image {{ -gtk-icon-size: 28tv; }}
.su-tv-row .su-tv-tick {{ opacity: 0; color: {m('accent')}; }}
.su-tv-row.tv-chip .su-tv-tick, .su-tv-row.su-chosen .su-tv-tick {{ opacity: 1; }}
.su-tv-row.tv-key .su-tv-tick {{ color: {m('key_ink')}; }}
.su-tv-row .su-tv-sub {{ font-size: 22tv; font-weight: 600; color: {m('ink_3')}; }}
.su-tv-row.tv-key .su-tv-sub {{ color: alpha({m('key_ink')}, .6); }}
.su-tv-fields {{ border-spacing: 14tv; }}
.su-tv-note {{ font-size: 22tv; font-weight: 600; color: {m('ink_3')}; margin: 6tv 8tv 0; }}
.su-tv-note.bad {{ color: rgba(255, 190, 92, 1); }}
.su-tv-foot {{ border-spacing: 16tv; margin-top: 34tv; }}
.su-tv-av {{ min-width: 120tv; min-height: 120tv; border-radius: 60tv; font-size: 48tv; font-weight: 650; color: #fff;
  background-image: linear-gradient(135deg, oklch(0.7 0.13 250), oklch(0.55 0.14 280)); }}
.su-tv-step {{ min-height: 72tv; padding: 0 24tv; border-radius: 24tv; background-color: {m('chip')}; font-size: 26tv;
  border-spacing: 18tv; color: {m('ink_3')}; }}
.su-tv-step.past {{ color: {m('ink')}; }}
.su-tv-step image {{ -gtk-icon-size: 28tv; color: {m('accent')}; }}
"""


def _label(text: str, *css: str, xalign: float = 0.0, wrap: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if wrap:
        widget.set_max_width_chars(1)  # wrap to the sheet, never widen it
    for name in css:
        widget.add_css_class(name)
    return widget


def _glyph(name: str, *css: str) -> Gtk.Image:
    image = Gtk.Image(icon_name=f"lumaui-{name}-symbolic")
    for c in css:
        image.add_css_class(c)
    return image


class _Row(Gtk.Box):
    """A TV list row: a lead glyph, the words, a quiet second line at the end, the tick."""

    def __init__(self, text: str, *, lead: str = "", sub: str = "", chosen: bool = False) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.RADIO)
        for css in ("tv-row", "su-tv-row"):
            self.add_css_class(css)
        if lead:
            self.append(_glyph(lead))
        words = Gtk.Label(label=text, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        words.add_css_class("tv-row-title")
        self.append(words)
        if sub:
            self.append(_label(sub, "su-tv-sub"))
        self.append(_glyph("check", "su-tv-tick"))
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])
        self.set_chosen(chosen)

    def set_chosen(self, on: bool) -> None:
        (self.add_css_class if on else self.remove_css_class)("su-chosen")
        (self.add_css_class if on else self.remove_css_class)("tv-chip")

    def set_tv_focus(self, on: bool) -> None:
        (self.add_css_class if on else self.remove_css_class)("tv-key")
        if on:
            self.remove_css_class("tv-chip")
        elif self.has_css_class("su-chosen"):
            self.add_css_class("tv-chip")


class SetupTV(TvStage):
    """The TV pages; the same answers, system calls and Finish as the desktop sheet."""

    __gtype_name__ = "LumaSetupTV"

    def __init__(self, setup: Setup) -> None:
        super().__init__()
        add_tv_style(_style)
        self.setup = setup
        self.network = system.Network(self._network_changed)
        if not setup.network:
            setup.network = "wired" if self.network.wired() else self.network.connected_name()
        self.pages = setup.pages(wired=self.network.wired(), wifi=self.network.has_wifi())
        self.index = 0
        self.open_network = ""
        self.joining = False
        self.join_problem = ""
        self.zone_open = False
        self._zone_asked = False
        self._finishing = False
        self._keyboard_up = False

        self.room = TvRoom()
        wallpaper = _wallpaper_path(prefers_light())
        if wallpaper:
            self.room.set_artwork(wallpaper)
        else:
            self.room.set_artwork(hues=(250, 265, 280))
        self.place(self.room, fill=True)
        self.sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for css in ("tv-sheet", "su-tv-sheet"):
            self.sheet.add_css_class(css)
        self.sheet.set_name("su-tv-sheet")
        self._place_sheet()
        self.focus = TvFocus(self, on_back=self._back)
        self.connect("notify::unit", lambda *_a: self._place_sheet())
        self._stop_keyboard = watch_tv_keyboard(self._keyboard)
        self.connect("unrealize", lambda *_a: self._stop_keyboard and self._stop_keyboard())
        self.draw()

    # -- layout ----------------------------------------------------------------------
    def _place_sheet(self) -> None:
        if self._keyboard_up:
            self.place(self.sheet, width=WIDTH, bottom=ABOVE_KEYBOARD, center_x=True)
        else:
            self.place(self.sheet, width=WIDTH, center_x=True, center_y=True)

    def _keyboard(self, visible: bool, _height: float) -> None:
        if visible != self._keyboard_up:
            self._keyboard_up = visible
            self._place_sheet()

    @property
    def page(self) -> str:
        return self.pages[self.index]

    # -- drawing ---------------------------------------------------------------------
    def _clear(self) -> None:
        self.focus.clear()
        while (child := self.sheet.get_first_child()) is not None:
            self.sheet.remove(child)

    def _head(self) -> None:
        head = Gtk.Box()
        head.add_css_class("su-tv-head")
        head.append(_label(HEAD, "tv-meta", xalign=0))
        head.append(Gtk.Box(hexpand=True))
        dots = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER)
        dots.add_css_class("su-tv-head")
        if not self._finishing:
            for i, _p in enumerate(self.pages):
                dot = Gtk.Box(valign=Gtk.Align.CENTER)
                dot.add_css_class("su-tv-dot")
                if i == self.index:
                    dot.add_css_class("on")
                dot.set_margin_start(8)
                dots.append(dot)
        head.append(dots)
        self.sheet.append(head)

    def draw(self, focus_first: bool = True) -> None:
        self._clear()
        self._head()
        title, lede, body, first = {"hello": self._hello, "wifi": self._wifi, "you": self._you,
                                    "device": self._device}[self.page]()
        self.sheet.append(_label(title, "su-tv-title", xalign=0.5, wrap=True))
        self.sheet.append(_label(lede, "tv-body", "su-tv-lede", xalign=0.5, wrap=True))
        self.sheet.append(body)
        self._foot()
        if focus_first and first is not None:
            GLib.idle_add(lambda: (self.focus.focus(first), False)[1])
        if self.page == "wifi":
            self.network.start_scanning()
        else:
            self.network.stop_scanning()
        if self.page == "device" and self.setup.online and not self._zone_asked:
            self._ask_zone()

    def _list(self, rows: list[tuple[str, _Row]], on_pick, group: str, max_rows: int = LIST_ROWS) -> Gtk.Widget:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                    vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        scroll.set_max_content_height(round(self.px(max_rows * (SHEET["row_height"] + 4) + 6)))
        for css in ("tv-well", "su-tv-list"):
            scroll.add_css_class(css)
        scroll.set_child(column)
        chosen = None
        for key, row in rows:
            column.append(row)
            self.focus.add(row, group=group, on_activate=lambda k=key: on_pick(k),
                           on_focus=lambda r=row, s=scroll: self._reveal(r, s))
            if row.has_css_class("su-chosen"):
                chosen = row
        self.focus.set_group_entry(group, lambda c=chosen, r=rows: c or (r[0][1] if r else None))
        scroll.chosen = chosen or (rows[0][1] if rows else None)
        if chosen is not None:
            GLib.idle_add(lambda: (self._reveal(chosen, scroll), False)[1])
        return scroll

    @staticmethod
    def _reveal(row: Gtk.Widget, scroll: Gtk.ScrolledWindow) -> None:
        ok, bounds = row.compute_bounds(scroll.get_child())
        if not ok:
            return
        adjustment = scroll.get_vadjustment()
        top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
        if top < adjustment.get_value():
            adjustment.set_value(top)
        elif bottom > adjustment.get_value() + adjustment.get_page_size():
            adjustment.set_value(bottom - adjustment.get_page_size())

    def _field(self, placeholder: str, value: str = "", *, icon: str | None = None, password: bool = False,
               action: str = "Done", on_changed=None, on_ok=None, purpose=Gtk.InputPurpose.FREE_FORM) -> TvField:
        field = TvField(placeholder=placeholder, icon=icon,
                        purpose=Gtk.InputPurpose.PASSWORD if password else purpose, action=action)
        field.set_value(value)
        if password:
            field.text.set_visibility(False)
        if on_changed is not None:
            field.text.connect("changed", lambda t: on_changed(t.get_text()))
        self.focus.add(field.text, editable=True, on_activate=on_ok or (lambda: None))
        return field

    # Hello ---------------------------------------------------------------------------
    def _hello(self):
        s = self.setup
        languages = [(lang.locale, _Row(lang.name, chosen=lang.locale == s.locale)) for lang in catalog.languages()]
        keyboards = [(kb.layout, _Row(kb.name, lead="keyboard", chosen=kb.layout == s.layout))
                     for kb in catalog.keyboards(s.locale)]
        left = self._list(languages, self._pick_language, "languages")
        right_list = self._list(keyboards, self._pick_keyboard, "keyboards")
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        right.append(_label("Keyboard", "tv-meta", "su-tv-lab"))
        right.append(right_list)
        left.set_size_request(round(self.px(600)), -1)
        left.set_valign(Gtk.Align.START)
        lists = Gtk.Box()
        lists.add_css_class("su-tv-lists")
        lists.append(left)
        lists.append(right)
        return catalog.greeting(s.locale), "Choose your language and keyboard.", lists, left.chosen

    def _pick_language(self, locale: str) -> None:
        self.setup.locale = locale
        self.setup.layout = catalog.keyboards(locale)[0].layout
        system.set_live_layout(self.setup.layout)
        self.draw(focus_first=False)
        # the remote stays on the language just picked
        self.focus.focus(self._entry("languages"))

    def _pick_keyboard(self, layout: str) -> None:
        self.setup.layout = layout
        system.set_live_layout(layout)
        self.draw(focus_first=False)
        self.focus.focus(self._entry("keyboards"))

    def _entry(self, group: str):
        for widget in list(self.focus._items):  # noqa: SLF001 - the chosen row of a rebuilt list
            if self.focus.group_of(widget) == group and widget.has_css_class("su-chosen"):
                return widget
        return None

    # Get online ----------------------------------------------------------------------
    def _wifi(self):
        rows = []
        for net in self.network.networks():
            row = _Row(net.ssid, sub="Connected" if net.connected else "", lead="lock" if net.secured else "wifi",
                       chosen=net.connected or self.open_network == net.ssid)
            rows.append((net.ssid, row))
            row.secured, row.connected = net.secured, net.connected
        other = _Row("Other network", lead="plus", chosen=self.open_network == "*")
        other.secured, other.connected = True, False
        rows.append(("*", other))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        listing = self._list(rows, lambda ssid: self._tap(ssid, dict(rows)[ssid]), "networks", max_rows=4)
        body.append(listing)
        first = listing.chosen
        if self.open_network:
            fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            fields.add_css_class("su-tv-fields")
            fields.set_margin_top(round(self.px(18)))
            if self.open_network == "*":
                self._hidden = self._field("Network name", icon="wifi", action="Next")
                fields.append(self._hidden)
            self._password = self._field(f"Password for {self.open_network}" if self.open_network != "*"
                                         else "Password (leave empty if open)", icon="lock", password=True,
                                         action="Join", on_ok=self._join)
            fields.append(self._password)
            if self.join_problem:
                fields.append(_label(self.join_problem, "su-tv-note", "bad"))
            body.append(fields)
            first = self._hidden.text if self.open_network == "*" else self._password.text
        note = _label("You can do this later in Settings. The time zone is set for you once you’re online.",
                      "su-tv-note", xalign=0.5, wrap=True)
        note.set_margin_top(round(self.px(16)))
        body.append(note)
        return "Get online", "No cable found. Pick a Wi-Fi network.", body, first

    def _tap(self, ssid: str, row: _Row) -> None:
        if row.connected or self.joining:
            return
        self.join_problem = ""
        if not row.secured:
            self._join_network(ssid, "", hidden=False)
            return
        self.open_network = ssid
        self.draw()

    def _join(self) -> None:
        hidden = self.open_network == "*"
        name = self._hidden.get_value().strip() if hidden else self.open_network
        if not name:
            return
        self._join_network(name, self._password.get_value(), hidden=hidden)

    def _join_network(self, name: str, password: str, *, hidden: bool) -> None:
        self.joining = True

        def done(ok: bool, why: str) -> None:
            self.joining = False
            if ok:
                self.setup.network = name
                self.open_network = ""
                self._ask_zone()
            else:
                self.join_problem = why
            if self.page == "wifi":
                self.draw()
        self.network.join(name, password, done, hidden=hidden)
        self._foot()

    def _network_changed(self) -> None:
        if not self.joining:
            self.setup.network = "wired" if self.network.wired() else self.network.connected_name()
        if self.page == "wifi" and not self.joining and not self.open_network and not self._finishing:
            focused_group = self.focus.group_of(self.focus.focused)
            self.draw(focus_first=focused_group != "networks")

    # About you -----------------------------------------------------------------------
    def _you(self):
        s = self.setup
        self._avatar = _label((s.first[:1] or "").upper(), "su-tv-av", xalign=0.5)
        self._avatar.set_valign(Gtk.Align.START)
        fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        fields.add_css_class("su-tv-fields")
        self._name = self._field("Full name", s.name, icon="user", purpose=Gtk.InputPurpose.NAME, action="Next",
                                 on_changed=self._typed_name)
        self._user = self._field("Username", s.username, icon="pencil", action="Next", on_changed=self._typed_user)
        self._pw = self._field("Password", s.password, icon="lock", password=True, action="Next",
                               on_changed=lambda v: self._typed_pw("password", v))
        self._pw2 = self._field("Password again", s.again, icon="lock", password=True, action="Done",
                                on_changed=lambda v: self._typed_pw("again", v))
        self._hint = _label("", "su-tv-note")
        for part in (self._name, self._user, self._pw, self._pw2, self._hint):
            fields.append(part)
        row = Gtk.Box()
        row.set_spacing(round(self.px(36)))
        row.append(self._avatar)
        row.append(fields)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(row)
        owner = _label(OWNER, "su-tv-note", xalign=0.5, wrap=True)
        owner.set_margin_top(round(self.px(20)))
        body.append(owner)
        self._update_you()
        return "About you", "You’ll sign in to this Luma with these.", body, self._name.text

    def _typed_name(self, value: str) -> None:
        self.setup.set_name(value)
        if self._user.get_value() != self.setup.username:
            self._user.set_value(self.setup.username)
            self.setup.username_edited = False
        self._avatar.set_label((self.setup.first[:1] or "").upper())
        self._update_you()

    def _typed_user(self, value: str) -> None:
        if value == self.setup.username:
            return
        cleaned = self.setup.set_username(value)
        if cleaned != value:
            self._user.set_value(cleaned)
        self._update_you()

    def _typed_pw(self, which: str, value: str) -> None:
        setattr(self.setup, which, value)
        self._update_you()

    def _update_you(self) -> None:
        s = self.setup
        _score, hint = strength(s.password)
        miss = mismatch(s.password, s.again)
        problem = username_problem(s.username, _taken) if s.username else ""
        self._hint.set_label(problem or ("The passwords don’t match." if miss else hint or "Pick something only you know."))
        (self._hint.add_css_class if (problem or miss) else self._hint.remove_css_class)("bad")
        self._foot()

    # Name this Luma ------------------------------------------------------------------
    def _device(self):
        s = self.setup
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("su-tv-fields")
        self._dev = self._field("Device name", s.device_name, icon="monitor", action="Done",
                                on_changed=self._typed_device)
        self._host = _label(f"{host_name(s.device_name)}.local", "su-tv-note")
        body.append(self._dev)
        body.append(self._host)
        zone = catalog.zone(s.timezone)
        automatic = s.online and s.timezone_automatic and not self.zone_open
        line = f"{zone.name} · {zone.city}" + (" · set automatically" if automatic else "")
        zone_row = _Row("Time zone", lead="globe", sub=line)
        body.append(zone_row)
        first = self._dev.text
        if s.online and not self.zone_open:
            self.focus.add(zone_row, on_activate=self._open_zones)
        if self.zone_open or not s.online:
            self._zq = self._field("Search for a city", icon="search", action="Search", on_changed=self._typed_zone)
            body.append(self._zq)
            self._zones_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            body.append(self._zones_box)
            self._fill_zones("")
        return "Name this Luma", "Your other devices and your network will see it by this name.", body, first

    def _fill_zones(self, query: str) -> None:
        self.focus.clear("zones")
        while (child := self._zones_box.get_first_child()) is not None:
            self._zones_box.remove(child)
        rows = [(z.id, _Row(z.city, sub=z.name, chosen=z.id == self.setup.timezone))
                for z in catalog.search_zones(query, limit=24)]
        if rows:
            self._zones_box.append(self._list(rows, self._pick_zone, "zones", max_rows=3))
        else:
            self._zones_box.append(_label("No city by that name.", "su-tv-note", xalign=0.5))

    def _typed_zone(self, value: str) -> None:
        self._fill_zones(value)

    def _open_zones(self) -> None:
        self.zone_open = True
        self.draw()

    def _pick_zone(self, zone_id: str) -> None:
        self.setup.timezone = zone_id
        self.setup.timezone_automatic = False
        self.zone_open = False
        self.draw()

    def _typed_device(self, value: str) -> None:
        self.setup.set_device(value)
        self._host.set_label(f"{host_name(value)}.local")
        self._foot()

    def _ask_zone(self) -> None:
        self._zone_asked = True

        def found(zone: str | None) -> None:
            if zone and not self.zone_open:
                self.setup.timezone = zone
                self.setup.timezone_automatic = True
                if self.page == "device" and not self._finishing:
                    self.draw(focus_first=False)
        system.detect_timezone(found)

    # -- the foot and the remote ------------------------------------------------------
    def _foot(self) -> None:
        old = getattr(self, "_foot_box", None)
        if old is not None:
            for child in list(_children(old)):
                self.focus.remove(child)
            if old.get_parent() is self.sheet:
                self.sheet.remove(old)
        if self._finishing:
            return
        foot = Gtk.Box(halign=Gtk.Align.END)
        foot.add_css_class("su-tv-foot")
        if self.index > 0:
            back = TvButton("Back")
            foot.append(back)
            self.focus.add(back, on_activate=lambda: self.go(-1))
        if self.page == "wifi" and not self.setup.online:
            skip = TvButton("Skip")
            foot.append(skip)
            self.focus.add(skip, on_activate=lambda: self.go(1))
        key = TvButton("Finish" if self.page == "device" else "Continue", accent=True)
        ready = self.setup.ready(self.page, _taken) and not self.joining
        key.set_sensitive(ready)
        foot.append(key)
        if ready:
            self.focus.add(key, on_activate=self._press_key)
        self.key = key
        self._foot_box = foot
        self.sheet.append(foot)

    def _press_key(self) -> None:
        if self.page == "device":
            self.finish()
        else:
            self.go(1)

    def go(self, step: int) -> None:
        self.index = max(0, min(len(self.pages) - 1, self.index + step))
        self.open_network = ""
        self.zone_open = False
        self.draw()

    def _back(self) -> bool:
        if self._finishing:
            return True
        if self.open_network or self.zone_open:
            self.open_network = ""
            self.zone_open = False
            self.draw()
        elif self.index > 0:
            self.go(-1)
        return True   # nothing can be left for good: Back on Hello does nothing

    # -- Finish ------------------------------------------------------------------------
    def finish(self) -> None:
        s = self.setup
        self._finishing = True
        self.network.stop_scanning()
        self._clear()
        self._head()
        avatar = _label((s.first[:1] or "").upper(), "su-tv-av", xalign=0.5)
        avatar.set_halign(Gtk.Align.CENTER)
        self.sheet.append(avatar)
        title = _label(f"Welcome, {s.first}.", "su-tv-title", xalign=0.5)
        title.set_margin_top(round(self.px(18)))
        self.sheet.append(title)
        self.sheet.append(_label("Getting Luma ready for you.", "tv-body", "su-tv-lede", xalign=0.5))
        self._steps = {}
        steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, spacing=round(self.px(8)))
        steps.set_size_request(round(self.px(640)), -1)
        for key, text, detail in (("account", "Creating your account", f"@{s.username}"),
                                  ("language", "Language and keyboard", catalog.language(s.locale).name),
                                  ("timezone", "Time zone", catalog.zone(s.timezone).city),
                                  ("name", "Naming this Luma", s.device_name)):
            spinner = Gtk.Spinner(spinning=True)
            row = Gtk.Box()
            row.add_css_class("su-tv-step")
            row.append(spinner)
            row.append(_label(text, hexpand=True))
            row.append(_label(detail, "su-tv-sub"))
            row.mark = spinner
            steps.append(row)
            self._steps[key] = row
        self.sheet.append(steps)
        payload = s.payload()
        s.password = s.again = ""
        password = payload["password"]
        system.Applier(self._step_done, lambda ok, why: self._applied(ok, why, payload["username"], password)
                       ).run(payload)

    def _step_done(self, key: str, ok: bool) -> None:
        row = self._steps.get(key)
        if row is None:
            return
        glyph = _glyph("check" if ok else "x")
        row.insert_child_after(glyph, row.mark)
        row.remove(row.mark)
        row.mark = glyph
        row.add_css_class("past")

    def _applied(self, ok: bool, why: str, username: str, password: str) -> None:
        if not ok:
            self._finishing = False
            self.index = self.pages.index("you") if "you" in self.pages else 0
            self.draw()
            self._hint.set_label(why)
            self._hint.add_css_class("bad")
            return
        GLib.timeout_add(650, lambda: (system.sign_in(username, password, lambda m: log.warning("sign-in: %s", m)),
                                       False)[1])


def _children(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


class SetupTVWindow(Gtk.ApplicationWindow):
    """The TV's window: full screen, undecorated, the TV stage its only child."""

    __gtype_name__ = "LumaSetupTVWindow"

    def __init__(self, application: Gtk.Application, setup: Setup) -> None:
        super().__init__(application=application, title=HEAD, decorated=False)
        self.stage = SetupTV(setup)
        self.set_child(self.stage)

    @property
    def pages(self) -> list[str]:
        return self.stage.pages

    @property
    def index(self) -> int:
        return self.stage.index

    @index.setter
    def index(self, value: int) -> None:
        self.stage.index = value

    def draw(self) -> None:
        self.stage.draw()
