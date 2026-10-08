#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Forward short FP6 power-key releases to the active Luma Shell session."""

import glob
import os
import stat
import struct
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


EVENT = struct.Struct("llHHi")
EV_KEY = 1
KEY_POWER = 116
SHORT_PRESS_SECONDS = 1.25
DISPLAY_BUS = "org.gnome.Mutter.DisplayConfig"
DISPLAY_PATH = "/org/gnome/Mutter/DisplayConfig"
DISPLAY_INTERFACE = "org.gnome.Mutter.DisplayConfig"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
HANDHELD_BUS = "org.project_luma.Handheld"
HANDHELD_PATH = "/org/project_luma/Handheld"
HANDHELD_INTERFACE = "org.project_luma.Handheld1"


def find_power_key():
    matches = []
    for name_path in glob.glob("/sys/class/input/event*/device/name"):
        with open(name_path, encoding="utf-8") as stream:
            if stream.read().strip() != "pmic_pwrkey":
                continue
        event_name = name_path.split("/")[4]
        device = f"/dev/input/{event_name}"
        mode = os.stat(device).st_mode
        if not stat.S_ISCHR(mode) or os.major(os.stat(device).st_rdev) != 13:
            raise SystemExit(f"refusing unexpected power-key node: {device}")
        matches.append(device)
    if len(matches) != 1:
        raise SystemExit(f"expected one pmic_pwrkey event node, found {len(matches)}")
    return matches[0]


def display_power_mode(connection):
    reply = connection.call_sync(
        DISPLAY_BUS,
        DISPLAY_PATH,
        PROPERTIES_INTERFACE,
        "Get",
        GLib.Variant("(ss)", (DISPLAY_INTERFACE, "PowerSaveMode")),
        GLib.VariantType.new("(v)"),
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )
    return reply.get_child_value(0).get_variant().get_int32()


def set_display_power(connection, mode):
    connection.call_sync(
        DISPLAY_BUS,
        DISPLAY_PATH,
        PROPERTIES_INTERFACE,
        "Set",
        GLib.Variant(
            "(ssv)",
            (DISPLAY_INTERFACE, "PowerSaveMode", GLib.Variant("i", mode)),
        ),
        None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )


def call_handheld(connection, method):
    connection.call_sync(
        HANDHELD_BUS,
        HANDHELD_PATH,
        HANDHELD_INTERFACE,
        method,
        None,
        None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )


def handheld_sleeping(connection):
    reply = connection.call_sync(
        HANDHELD_BUS,
        HANDHELD_PATH,
        PROPERTIES_INTERFACE,
        "Get",
        GLib.Variant("(ss)", (HANDHELD_INTERFACE, "Sleeping")),
        GLib.VariantType.new("(v)"),
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )
    return reply.get_child_value(0).get_variant().get_boolean()


def toggle_display(connection):
    # The compositor's real panel state is authoritative. Shell's posture and
    # sleep bookkeeping can legitimately lag a powered-down connector, but a
    # physical wake key must never depend on either one.
    if display_power_mode(connection) != 0:
        set_display_power(connection, 0)
        try:
            call_handheld(connection, "WakeDisplay")
        except GLib.Error:
            # The synchronous Mutter write above is sufficient to recover a
            # session running an older extension during an in-place update.
            # Reconcile that revision's cached flag only when it confirms it
            # initiated the sleep; an externally blanked panel must not be
            # toggled back off.
            try:
                if handheld_sleeping(connection):
                    call_handheld(connection, "ToggleDisplay")
            except GLib.Error:
                pass
        return

    try:
        call_handheld(connection, "SleepDisplay")
    except GLib.Error:
        # Compatibility with the immediately preceding extension revision.
        call_handheld(connection, "ToggleDisplay")


def main():
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    pressed_at = None
    with open(find_power_key(), "rb", buffering=0) as stream:
        while True:
            data = stream.read(EVENT.size)
            if len(data) != EVENT.size:
                raise SystemExit("short read from pmic_pwrkey")
            _seconds, _useconds, event_type, code, value = EVENT.unpack(data)
            if event_type != EV_KEY or code != KEY_POWER:
                continue
            if value == 1:
                pressed_at = time.monotonic()
            elif value == 0 and pressed_at is not None:
                duration = time.monotonic() - pressed_at
                pressed_at = None
                if duration <= SHORT_PRESS_SECONDS:
                    toggle_display(connection)


if __name__ == "__main__":
    try:
        main()
    except GLib.Error as error:
        raise SystemExit(f"Luma power-key broker: {error.message}") from error
