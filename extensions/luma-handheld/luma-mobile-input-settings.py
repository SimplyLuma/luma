#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Luma mobile display-sleep and virtual-keyboard settings."""

import os

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402


TIMEOUTS = ((15, "15 seconds"), (30, "30 seconds"), (60, "1 minute"),
            (120, "2 minutes"), (300, "5 minutes"), (0, "Never"))
ENGINES = (
    ("gnome", "Luma Keyboard", "GNOME Shell native", True, None, (),
     "Ready · current production engine"),
    ("stevia", "Stevia", "Phosh", False, "stevia",
     ("/usr/bin/phoc", "/usr/bin/phosh-osk-stevia"),
     "Native lab · word completion is available in its settings"),
    ("squeekboard", "Squeekboard", "Phosh", False, "squeekboard",
     ("/usr/bin/phoc", "/usr/libexec/luma-keyboard-labs/squeekboard"),
     "Native lab · Fedora build staged without session autostart"),
    ("wvkbd", "wvkbd", "wlroots mobile", False, "wvkbd",
     ("/usr/bin/phoc", "/usr/libexec/luma-keyboard-labs/wvkbd-mobintl"),
     "Native lab · minimal responsiveness baseline"),
    ("plasma", "Plasma Keyboard", "Plasma Mobile", False, "plasma",
     ("/var/lib/luma-keyboard-labs/kde-f44/usr/bin/kwin_wayland",
      "/var/lib/luma-keyboard-labs/kde-f44/usr/bin/plasma-keyboard"),
     "Native lab · Fedora 44 KWin runtime"),
    ("maliit-lomiri", "Maliit Keyboard", "Plasma Mobile",
     False, "maliit-lomiri",
     ("/var/lib/luma-keyboard-labs/kde-f44/usr/bin/kwin_wayland",
      "/var/lib/luma-keyboard-labs/kde-f44/usr/bin/maliit-keyboard"),
     "Second-place feel · reproducibly freezes after sustained typing · retestable"),
    ("lomiri", "Lomiri Keyboard", "Ubuntu Touch", False, None, (),
     "Rejected · 1.1.0 passes upstream tests but reproducibly crashes after physical geometry maps"),
    ("sysboard", "Sysboard", "Generic Wayland", False, None, (),
     "Blocked · nested-compositor test triggered an FP6 Adreno lockup"),
    ("wkeys", "Wkeys", "Wayland / COSMIC", False, None, (),
     "Rejected · physical test rendered but did not commit text"),
)


class MobileInputWindow(Adw.PreferencesWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Mobile Input & Display")
        self.set_default_size(520, 760)
        page = Adw.PreferencesPage(title="Mobile")
        self.add(page)

        display = Adw.PreferencesGroup(
            title="Display sleep",
            description="The phone stays running and connected while the panel is off.")
        page.add(display)
        self._session = Gio.Settings(schema_id="org.gnome.desktop.session")
        timeout_model = Gtk.StringList.new([label for _value, label in TIMEOUTS])
        timeout = Adw.ComboRow(title="Screen turns off after", model=timeout_model)
        current = self._session.get_uint("idle-delay")
        timeout.set_selected(next((index for index, (value, _label) in
                                   enumerate(TIMEOUTS) if value == current), 2))
        timeout.connect("notify::selected", self._timeout_changed)
        display.add(timeout)

        power = Adw.ActionRow(
            title="Power button",
            subtitle="Short press sleeps or wakes the display; apps, network, and notifications continue.")
        power.add_prefix(Gtk.Image.new_from_icon_name("system-shutdown-symbolic"))
        display.add(power)

        keyboards = Adw.PreferencesGroup(
            title="Virtual keyboard",
            description="Choose a tested engine. Unavailable engines remain visible so compatibility work is explicit.")
        page.add(keyboards)
        self._handheld = Gio.Settings(schema_id="org.project_luma.handheld")
        selected_engine = self._handheld.get_string("keyboard-engine")
        first_button = None
        self._labs = []
        self._lab_buttons = []
        for engine_id, name, origin, ready, lab_engine, required, state in ENGINES:
            row = Adw.ActionRow(title=name, subtitle=f"{origin} · {state}")
            row.add_prefix(Gtk.Image.new_from_icon_name("input-keyboard-symbolic"))
            if ready:
                button = Gtk.CheckButton()
                if first_button is None:
                    first_button = button
                else:
                    button.set_group(first_button)
                button.set_active(engine_id == selected_engine)
                button.connect("toggled", self._engine_changed, engine_id)
                row.add_suffix(button)
                row.set_activatable_widget(button)
            elif lab_engine and all(os.access(path, os.X_OK) for path in required):
                button = Gtk.Button(label="Test temporarily")
                button.add_css_class("suggested-action")
                button.connect("clicked", self._launch_lab, lab_engine)
                row.add_suffix(button)
                row.set_activatable_widget(button)
                self._lab_buttons.append(button)
            else:
                badge = Gtk.Label(label="Adapter required")
                badge.add_css_class("dim-label")
                row.add_suffix(badge)
                row.set_sensitive(False)
            keyboards.add(row)

        diagnostics = Adw.PreferencesGroup(
            title="Evaluation policy",
            description="Latency, frame timing, and missed-input counts may be recorded. Typed text, key values, and touch coordinates are never recorded.")
        page.add(diagnostics)

    def _timeout_changed(self, row, _param):
        self._session.set_uint("idle-delay", TIMEOUTS[row.get_selected()][0])

    def _engine_changed(self, button, engine_id):
        if button.get_active():
            self._handheld.set_string("keyboard-engine", engine_id)

    def _launch_lab(self, _button, engine_id):
        try:
            for button in self._lab_buttons:
                button.set_sensitive(False)
            process = Gio.Subprocess.new(
                ["/usr/libexec/luma-keyboard-lab", engine_id],
                Gio.SubprocessFlags.NONE)
            self._labs.append(process)
            process.wait_async(None, self._lab_finished)
        except GLib.Error as error:
            for button in self._lab_buttons:
                button.set_sensitive(True)
            dialog = Adw.AlertDialog(
                heading="Keyboard lab could not start",
                body=error.message)
            dialog.add_response("close", "Close")
            dialog.present(self)

    def _lab_finished(self, process, result):
        try:
            process.wait_finish(result)
        finally:
            if process in self._labs:
                self._labs.remove(process)
            if not self._labs:
                for button in self._lab_buttons:
                    button.set_sensitive(True)


class MobileInputApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.project_luma.MobileInputSettings")

    def do_activate(self):
        window = self.props.active_window or MobileInputWindow(self)
        window.present()


if __name__ == "__main__":
    raise SystemExit(MobileInputApplication().run())
