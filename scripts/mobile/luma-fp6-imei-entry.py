#!/usr/bin/python3
"""Private, one-shot FP6 IMEI 2 entry UI for IMS provisioning.

The value is never printed.  A validated value is handed to the privileged
installer through the calling user's runtime directory and is removed after
installation.
"""

import os
import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


def valid_imei(value: str) -> bool:
    if not re.fullmatch(r"\d{15}", value) or len(set(value)) == 1:
        return False
    total = 0
    for index, char in enumerate(value):
        digit = int(char)
        if index % 2:
            digit *= 2
            digit = digit // 10 + digit % 10
        total += digit
    return total % 10 == 0


class ImeiWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Finish cellular setup")
        self.set_default_size(420, 420)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(38)
        box.set_margin_bottom(38)
        box.set_margin_start(28)
        box.set_margin_end(28)

        heading = Gtk.Label(label="Enter IMEI 2")
        heading.add_css_class("title-1")
        box.append(heading)

        instructions = Gtk.Label(
            label=(
                "Eject the SIM tray at the bottom-left of the phone. "
                "Enter the 15-digit IMEI 2 printed at the bottom, then "
                "reinsert the tray. The value stays private on this phone."
            )
        )
        instructions.set_wrap(True)
        instructions.set_justify(Gtk.Justification.CENTER)
        box.append(instructions)

        self.entry = Gtk.Entry()
        self.entry.set_max_length(15)
        self.entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self.entry.set_placeholder_text("15-digit IMEI 2")
        self.entry.set_visibility(False)
        self.entry.set_invisible_char("•")
        self.entry.connect("activate", self.submit)
        box.append(self.entry)

        self.status = Gtk.Label(label="")
        self.status.set_wrap(True)
        box.append(self.status)

        button = Gtk.Button(label="Use this IMEI 2")
        button.add_css_class("suggested-action")
        button.connect("clicked", self.submit)
        box.append(button)

        self.set_child(box)

    def submit(self, *_args) -> None:
        value = self.entry.get_text().strip()
        if not valid_imei(value):
            self.status.set_label("That is not a valid 15-digit IMEI. Check the tray and try again.")
            self.entry.select_region(0, -1)
            return

        runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        final_path = os.path.join(runtime, "luma-fp6-imei2")
        temp_path = final_path + ".tmp"
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, value.encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp_path, final_path)
        os.chmod(final_path, 0o600)
        self.entry.set_text("")
        self.status.set_label("Accepted. Reinsert the SIM tray; Luma is finishing setup.")
        GLib.timeout_add_seconds(2, self.get_application().quit)


class ImeiApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="net.prairie.LumaFp6ImeiEntry")

    def do_activate(self) -> None:
        window = ImeiWindow(self)
        window.present()


if __name__ == "__main__":
    raise SystemExit(ImeiApp().run())
