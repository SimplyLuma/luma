# SPDX-License-Identifier: Apache-2.0
"""Share sound — the audio capture picker.

One question: whose sound may this app record? The answer is a list of the
apps that have sound right now, each with its icon, name and a live level so
the person can see which one is playing, plus "All sound except <app>". The
list comes from luma-audio-devices (GetApplications), the levels too
(WatchLevels, only while the picker is open). Nothing is recorded until
Share.

It follows Luma Share, the screen sharing picker (the Shell's lumaShare.js,
luma-share.css): the asking app's icon with the sharing badge, the question
and what happens, the choices on one rounded block, and an equal Cancel and
Share pair. The rows are LumaUI's: an app icon (AppIcon), the name, the
level as LumaUI's meter (AudioMeter, the one meter part) and LumaUI's
checkbox. The window is as tall as its content, up to most of the screen,
then the list scrolls. On a phone it is the same card, full width.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

LOGGER = logging.getLogger("luma-portal")

AUDIO_NAME = "org.projectluma.AudioDevices"
AUDIO_PATH = "/org/projectluma/AudioDevices"
AUDIO_IFACE = "org.projectluma.AudioDevices1"

# Luma Share's card: 440 wide; the list grows to its rows, then scrolls.
DIALOG_WIDTH = 440
LIST_MAX_SCREEN = 0.5
WHO_ICON = 56
APP_ICON = 28
ALL_SOUND_GLYPH = "speaker"
BADGE_GLYPH = "volume-2"


def _icon(icon_string: str) -> Gio.Icon | None:
    if not icon_string:
        return None
    try:
        return Gio.Icon.new_for_string(icon_string)
    except GLib.Error:
        return None


def _kit():
    """LumaUI's parts, imported when a picker is built (the portal starts without GTK)."""
    import luma_appkit as kit
    from luma_appkit import icons
    return kit, icons


def _app_icon(desktop_id: str, icon, name: str, size: int) -> Gtk.Widget:
    """LumaUI's app icon: the desktop entry's, else the stream's own icon, else a monogram."""
    kit, _icons = _kit()
    if desktop_id:
        app = kit.AppIcon(desktop_id.removesuffix(".desktop"), name=name, size=size)
        if app.paintable is not None:
            return app
    picture = None
    if isinstance(icon, Gio.Icon):
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            picture = theme.lookup_by_gicon(icon, size, 1, Gtk.TextDirection.NONE, 0)
    return kit.AppIcon(None, picture=picture, name=name, size=size)


class SourceRow(Gtk.ListBoxRow):
    """[icon] name / detail ............ [level] [check] — the row is the control."""

    def __init__(self, key: str, name: str, lead: Gtk.Widget, detail: str, metered: bool) -> None:
        super().__init__()
        _kit_, _icons = _kit()
        self.key = key
        self.name = name
        self.add_css_class("ac-row")
        self.update_property([Gtk.AccessibleProperty.LABEL], [name])
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lead.set_valign(Gtk.Align.CENTER)
        box.append(lead)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        label = Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        label.add_css_class("ac-name")
        column.append(label)
        if detail:
            line = Gtk.Label(label=detail, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            line.add_css_class("ac-line")
            column.append(line)
        box.append(column)

        # The level is for the eye (which app is the loud one); the name is
        # what is read out. All sound has no single level, so no meter.
        self.meter = None
        if metered:
            self.meter = _kit_.AudioMeter(1, horizontal=True)
            self.meter.set_valign(Gtk.Align.CENTER)
            self.meter.add_css_class("ac-meter")
            self.meter.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
            box.append(self.meter)

        self.check = _kit_.Checkbox(checked=False)
        self.check.add_css_class("ac-check")
        box.append(self.check)
        self.set_child(box)
        self._sync_state()

    @property
    def active(self) -> bool:
        return self.check.checked

    def set_active(self, active: bool) -> None:
        self.check.set_checked(active)
        self._sync_state()

    def _sync_state(self) -> None:
        on = self.check.checked
        if on:
            self.add_css_class("chosen")
        else:
            self.remove_css_class("chosen")
        from gi.repository import GObject
        self.update_state([Gtk.AccessibleState.CHECKED],
                          [GObject.Value(GObject.TYPE_INT, int(Gtk.AccessibleTristate.TRUE if on
                                                              else Gtk.AccessibleTristate.FALSE))])

    def set_level(self, value: float) -> None:
        if self.meter is None:
            return
        # The service's positions run from −60 dB (0) to full scale (1).
        value = max(0.0, min(1.0, value))
        db = -60.0 if value <= 0 else min(-60.0 + 60.0 * value, -0.01)
        self.meter.set_levels_db(db, db)


class CapturePicker(Adw.Window):
    def __init__(self, *, caller_id: str, caller_name: str, types: int, multiple: bool,
                 on_done) -> None:
        super().__init__(modal=True, title="Share sound", resizable=False)
        self.add_css_class("ac-dialog")
        self.set_default_size(DIALOG_WIDTH, -1)
        self._caller_id = caller_id
        self._caller_name = caller_name
        self._types = types
        self._multiple = multiple
        self._on_done = on_done
        self._answered = False
        self._rows: dict[str, SourceRow] = {}
        self._watch = 0
        self._signal = 0
        self._levels_signal = 0
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("ac-card")
        card.append(self._build_who())
        card.append(self._build_words())
        card.append(self._build_body())
        card.append(self._build_actions())
        self.set_content(card)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_a: (self._deliver(None), False)[1])
        self.connect("map", lambda *_a: self._fit())
        self._load()

    # -- Parts ------------------------------------------------------------

    def _build_who(self) -> Gtk.Widget:
        """The asking app's icon with the sharing badge on its corner."""
        _kit_, icons = _kit()
        who = Gtk.Overlay(halign=Gtk.Align.CENTER)
        who.add_css_class("ac-who")
        who.set_child(_app_icon(self._caller_id, None, self._caller_name, WHO_ICON))
        badge = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END)
        badge.add_css_class("ac-badge")
        glyph = icons.image(BADGE_GLYPH)
        glyph.set_hexpand(True)
        glyph.set_halign(Gtk.Align.CENTER)
        badge.append(glyph)
        badge.set_can_target(False)
        who.add_overlay(badge)
        return who

    def _build_words(self) -> Gtk.Widget:
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title = Gtk.Label(label=f"{self._caller_name} wants to share your sound", wrap=True,
                          justify=Gtk.Justification.CENTER, max_width_chars=1, hexpand=True)
        title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title.add_css_class("ac-title")
        words.append(title)
        what = "the apps" if self._multiple else "the app"
        body = Gtk.Label(label=f"Choose {what} {self._caller_name} can hear until you stop sharing.",
                         wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=1, hexpand=True)
        body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.add_css_class("ac-body")
        words.append(body)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                             [title.get_label(), body.get_label()])
        return words

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("ac-sources")
        self._everything_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._everything_list.add_css_class("ac-list")
        body.append(self._everything_list)
        self._caption = Gtk.Label(label="Playing now", xalign=0)
        self._caption.add_css_class("ac-caption")
        body.append(self._caption)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("ac-list")
        body.append(self._list)
        self._empty = Gtk.Label(label="No app is playing sound. Start the app you want to share, "
                                      "and it appears here.", wrap=True, xalign=0, max_width_chars=1)
        self._empty.add_css_class("ac-empty")
        self._empty.set_visible(False)
        body.append(self._empty)
        for listbox in (self._everything_list, self._list):
            listbox.connect("row-activated", lambda _l, row: self._choose(row))
        self._scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True, child=body)
        self._scroll.add_css_class("ac-scroll")
        return self._scroll

    def _build_actions(self) -> Gtk.Widget:
        # Luma Share's pair: two equal buttons, Cancel then Share.
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        actions.add_css_class("ac-actions")
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("ac-cancel")
        cancel.connect("clicked", lambda *_a: self._deliver(None))
        actions.append(cancel)
        self._share = Gtk.Button(label="Share")
        self._share.add_css_class("ac-share")
        self._share.connect("clicked", lambda *_a: self._finish())
        self._share.set_sensitive(False)
        actions.append(self._share)
        self.set_default_widget(self._share)
        return actions

    def _fit(self) -> None:
        """As wide as Luma Share (or the screen), the list as tall as its rows up to half the screen."""
        display = self.get_display()
        surface = self.get_surface()
        monitor = display.get_monitor_at_surface(surface) if surface is not None else None
        if monitor is None:
            return
        geometry = monitor.get_geometry()
        self.set_default_size(min(DIALOG_WIDTH, geometry.width), -1)
        self._scroll.set_max_content_height(max(160, int(geometry.height * LIST_MAX_SCREEN)))

    # -- Data -------------------------------------------------------------

    def _load(self) -> None:
        self._bus.call(AUDIO_NAME, AUDIO_PATH, AUDIO_IFACE, "GetApplications", None,
                       GLib.VariantType("(aa{sv})"), Gio.DBusCallFlags.NONE, 5000, None,
                       self._loaded, None)
        self._signal = self._bus.signal_subscribe(
            AUDIO_NAME, AUDIO_IFACE, "ApplicationsChanged", AUDIO_PATH, None,
            Gio.DBusSignalFlags.NONE, lambda *a: self._populate(a[-2].unpack()[0]), None)
        self._levels_signal = self._bus.signal_subscribe(
            AUDIO_NAME, AUDIO_IFACE, "Levels", AUDIO_PATH, None,
            Gio.DBusSignalFlags.NONE, self._levels, None)

    def _loaded(self, source, result, _data) -> None:
        try:
            (rows,) = source.call_finish(result).unpack()
        except GLib.Error as error:
            LOGGER.warning("cannot list the apps playing sound: %s", error.message)
            rows = []
        self._populate(rows)

    def _populate(self, rows) -> None:
        _kit_, icons = _kit()
        if self._types & 2 and "@all" not in self._rows:
            tile = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            tile.add_css_class("ac-all-tile")
            tile.set_hexpand(False)  # said, so its centred glyph does not widen the row's lead
            glyph = icons.image(ALL_SOUND_GLYPH)
            glyph.set_hexpand(True)
            glyph.set_halign(Gtk.Align.CENTER)
            tile.append(glyph)
            everything = SourceRow("@all", f"All sound except {self._caller_name}", tile,
                                   "Every app, now and later", metered=False)
            self._rows["@all"] = everything
            self._everything_list.append(everything)
        present = set()
        if self._types & 1:
            for row in rows:
                key = row.get("id")
                if not key or key == self._caller_id:
                    continue
                if row.get("kind") == "system" and not row.get("streams"):
                    continue
                present.add(key)
                if key in self._rows:
                    continue
                name = row.get("name") or key
                lead = _app_icon(row.get("desktop_id") or "", _icon(row.get("icon") or ""), name, APP_ICON)
                source = SourceRow(key, name, lead, "", metered=True)
                self._rows[key] = source
                self._list.append(source)
            for key in [k for k in self._rows if k != "@all" and k not in present]:
                row = self._rows.pop(key)
                if not row.active:
                    self._list.remove(row)
                else:
                    self._rows[key] = row  # keep a chosen app; it may play again
        apps = any(k != "@all" for k in self._rows)
        self._caption.set_visible(bool(self._types & 1))
        self._list.set_visible(apps)
        self._empty.set_visible(bool(self._types & 1) and not apps)
        self._everything_list.set_visible("@all" in self._rows)
        self._watch_levels()

    def _watch_levels(self) -> None:
        targets = [k for k in self._rows if k != "@all"]
        if self._watch:
            self._unwatch()
        if not targets:
            return
        try:
            (self._watch,) = self._bus.call_sync(
                AUDIO_NAME, AUDIO_PATH, AUDIO_IFACE, "WatchLevels",
                GLib.Variant("(as)", (targets,)), GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE, 2000, None).unpack()
        except GLib.Error as error:
            LOGGER.debug("no levels: %s", error.message)

    def _unwatch(self) -> None:
        if self._watch:
            self._bus.call(AUDIO_NAME, AUDIO_PATH, AUDIO_IFACE, "UnwatchLevels",
                           GLib.Variant("(u)", (self._watch,)), None,
                           Gio.DBusCallFlags.NONE, -1, None, None, None)
            self._watch = 0

    def _levels(self, _c, _s, _p, _i, _n, parameters, _data) -> None:
        watch, levels = parameters.unpack()
        if watch != self._watch:
            return
        for key, value in levels.items():
            row = self._rows.get(key)
            if row is not None:
                row.set_level(value)

    # -- Answer -----------------------------------------------------------

    def _choose(self, row: SourceRow) -> None:
        """A row is the control. One app (or All sound) replaces the choice;
        with several, apps toggle and All sound stands alone."""
        on = not row.active if self._multiple else True
        if on:
            for other in self._rows.values():
                if other is row:
                    continue
                if not self._multiple or row.key == "@all" or other.key == "@all":
                    other.set_active(False)
        row.set_active(on)
        self._share.set_sensitive(bool(self.choice()))

    def choice(self) -> dict | None:
        chosen = [row for row in self._rows.values() if row.active]
        if not chosen:
            return None
        if any(row.key == "@all" for row in chosen):
            return {"mode": "system", "apps": [], "names": []}
        return {"mode": "apps", "apps": [row.key for row in chosen],
                "names": [row.name for row in chosen]}

    def _finish(self) -> None:
        choice = self.choice()
        if choice:
            self._deliver(choice)

    def _key(self, _controller, keyval, _code, _state) -> bool:
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape:
            self._deliver(None)
            return True
        return False

    def _deliver(self, choice) -> None:
        if self._answered:
            return
        self._answered = True
        self._unwatch()
        for subscription in (self._signal, self._levels_signal):
            if subscription:
                self._bus.signal_unsubscribe(subscription)
        self._signal = self._levels_signal = 0
        if self._on_done is not None:
            self._on_done(choice)
        self.close()
