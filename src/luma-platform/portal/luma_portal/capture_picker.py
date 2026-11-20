# SPDX-License-Identifier: Apache-2.0
"""Share sound — the audio capture picker.

One question: whose sound may this app record? The answer is a list of the
apps that have sound right now, each with its icon, name and a live level so
the person can see which one is playing, plus "All sound except <app>". The
list comes from luma-audio-devices (GetApplications), the levels too
(WatchLevels, only while the picker is open). Nothing is recorded until
Share.

Built from the same parts and sheet as the application chooser (open-with.css):
head with the subject and a close key, a scrolling body of rows, a foot with
Cancel and the primary action. On a phone it is the same card, full width.
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

DIALOG_WIDTH = 520
DIALOG_HEIGHT = 520
APP_TILE = 26
SYSTEM_SOUND_ICON = "lumaui-speaker-symbolic"


def _icon(icon_string: str) -> Gio.Icon | None:
    if not icon_string:
        return None
    try:
        return Gio.Icon.new_for_string(icon_string)
    except GLib.Error:
        return None


class SourceRow(Gtk.ListBoxRow):
    """[tile] name / level ....................... [check]"""

    def __init__(self, key: str, name: str, icon, detail: str, group=None) -> None:
        super().__init__()
        self.key = key
        self.name = name
        self.add_css_class("ow-row")
        self.add_css_class("ac-row")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)

        image = Gtk.Image(pixel_size=APP_TILE)
        image.add_css_class("ow-app-tile")
        if isinstance(icon, Gio.Icon):
            image.set_from_gicon(icon)
        else:
            image.set_from_icon_name(icon or "application-x-executable")
        box.append(image)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True,
                         valign=Gtk.Align.CENTER, spacing=4)
        label = Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        label.add_css_class("ow-name")
        column.append(label)
        if detail:
            text = Gtk.Label(label=detail, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            text.add_css_class("ow-detail")
            column.append(text)
        self.meter = Gtk.LevelBar(min_value=0.0, max_value=1.0, value=0.0,
                                  mode=Gtk.LevelBarMode.CONTINUOUS)
        self.meter.add_css_class("ac-meter")
        # The level is decoration for the eye; the name is what is read out.
        self.meter.set_accessible_role(Gtk.AccessibleRole.PRESENTATION)
        # One colour: the shared progress fill, not the low/high/full scale.
        for offset in ("low", "high", "full"):
            self.meter.remove_offset_value(offset)
        column.append(self.meter)
        box.append(column)

        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        if group is not None:
            self.check.set_group(group)
        self.check.add_css_class("ac-check")
        self.check.update_property([Gtk.AccessibleProperty.LABEL], [name])
        box.append(self.check)
        self.set_child(box)

    def set_level(self, value: float) -> None:
        self.meter.set_value(max(0.0, min(1.0, value)))


class CapturePicker(Adw.Window):
    def __init__(self, *, caller_id: str, caller_name: str, types: int, multiple: bool,
                 on_done) -> None:
        super().__init__(modal=True, title="Share sound")
        self.add_css_class("ow-dialog")
        self.add_css_class("ac-dialog")
        self.set_default_size(DIALOG_WIDTH, DIALOG_HEIGHT)
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

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_head())
        root.append(self._build_body())
        root.append(self._build_foot())
        self.set_content(root)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_a: (self._deliver(None), False)[1])
        self.connect("map", lambda *_a: self._fit())
        self._load()

    # -- Parts ------------------------------------------------------------

    def _build_head(self) -> Gtk.Widget:
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.add_css_class("ow-head")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(label=f"Share sound with {self._caller_name}", xalign=0,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("ow-file-name")
        column.append(title)
        hint = "Choose the apps it can hear" if self._multiple else "Choose the app it can hear"
        detail = Gtk.Label(label=hint, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        detail.add_css_class("ow-file-kind")
        column.append(detail)
        head.append(column)
        close = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER)
        close.add_css_class("ow-close")
        close.set_tooltip_text("Cancel")
        close.connect("clicked", lambda *_a: self._deliver(None))
        head.append(close)
        return head

    def _build_body(self) -> Gtk.Widget:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.add_css_class("ow-body")
        self._everything_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._everything_list.add_css_class("ow-list")
        body.append(self._everything_list)
        caption = Gtk.Label(label="PLAYING NOW", xalign=0)
        caption.add_css_class("ow-caption")
        body.append(caption)
        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("ow-list")
        body.append(self._list)
        self._empty = Gtk.Label(label="No app is playing sound. Start the app you want to share, "
                                      "and it appears here.", wrap=True, xalign=0)
        self._empty.add_css_class("ow-empty")
        self._empty.set_visible(False)
        body.append(self._empty)
        for listbox in (self._everything_list, self._list):
            listbox.connect("row-activated", lambda _l, row: row.check.set_active(
                not row.check.get_active() or not self._multiple))
        return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=body)

    def _build_foot(self) -> Gtk.Widget:
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        foot.add_css_class("ow-foot")
        foot.append(Gtk.Box(hexpand=True))
        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("ow-secondary")
        cancel.connect("clicked", lambda *_a: self._deliver(None))
        foot.append(cancel)
        self._share = Gtk.Button(label="Share")
        self._share.add_css_class("ow-primary")
        self._share.connect("clicked", lambda *_a: self._finish())
        self._share.set_sensitive(False)
        foot.append(self._share)
        self.set_default_widget(self._share)
        return foot

    def _fit(self) -> None:
        display = self.get_display()
        surface = self.get_surface()
        monitor = display.get_monitor_at_surface(surface) if surface is not None else None
        if monitor is None:
            return
        geometry = monitor.get_geometry()
        self.set_default_size(min(DIALOG_WIDTH, geometry.width), min(DIALOG_HEIGHT, int(geometry.height * 0.82)))

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
        group = None
        if self._types & 2 and "@all" not in self._rows:
            everything = SourceRow("@all", f"All sound except {self._caller_name}",
                                   SYSTEM_SOUND_ICON, "Every app, now and later")
            self._rows["@all"] = everything
            self._everything_list.append(everything)
            everything.check.connect("toggled", self._toggled)
        if not self._multiple:
            group = self._rows["@all"].check if "@all" in self._rows else None
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
                source = SourceRow(key, row.get("name") or key, _icon(row.get("icon") or ""), "",
                                   group if not self._multiple else None)
                if not self._multiple and group is None:
                    group = source.check
                source.check.connect("toggled", self._toggled)
                self._rows[key] = source
                self._list.append(source)
            for key in [k for k in self._rows if k != "@all" and k not in present]:
                row = self._rows.pop(key)
                if not row.check.get_active():
                    self._list.remove(row)
                else:
                    self._rows[key] = row  # keep a chosen app; it may play again
        self._empty.set_visible(not any(k != "@all" for k in self._rows))
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

    def _toggled(self, check) -> None:
        if self._multiple and check.get_active():
            everything = self._rows.get("@all")
            if everything is not None:
                if check is everything.check:
                    for key, row in self._rows.items():
                        if key != "@all":
                            row.check.set_active(False)
                else:
                    everything.check.set_active(False)
        self._share.set_sensitive(bool(self.choice()))

    def choice(self) -> dict | None:
        chosen = [row for row in self._rows.values() if row.check.get_active()]
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
