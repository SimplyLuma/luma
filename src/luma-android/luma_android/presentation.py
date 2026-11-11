"""Luma's current presentation and the Android density that follows it."""

from __future__ import annotations

SHELL_STATE_NAME = "org.project_luma.ShellState1"
SHELL_STATE_PATH = "/org/project_luma/ShellState1"
PRESENTATIONS = {"desktop", "tablet", "mobile"}
#: Android TV draws its interface at 320 dpi on a 1080p screen (xhdpi); a TV is
#: read from across the room, so it takes that density whatever the desktop's.
TV_DENSITY = 320
_DEVICE_CLASS_PRESENTATION = {"desktop": "desktop", "tablet": "tablet", "handheld": "mobile"}


def presentation_density(presentation: str, touch_density: str, display_scale: str) -> int:
    """Return Android's density for a host presentation, 0 when unknown.

    Touch presentations take the output's physical density, which the
    composer publishes as luma.touch_density. The desktop takes 180 per
    display scale step, as the composer sets it for a desktop boot. With 0
    Android keeps the density it booted with.
    """
    if presentation == "tv":
        try:
            scale = float(display_scale)
        except ValueError:
            scale = 1.0
        return round(TV_DENSITY * scale) if 0 < scale < 16 else TV_DENSITY
    if presentation == "desktop":
        try:
            scale = float(display_scale)
        except ValueError:
            return 0
        return round(180 * scale) if 0 < scale < 16 else 0
    try:
        density = int(touch_density)
    except ValueError:
        return 0
    return density if 0 < density < 1000 else 0


def current_presentation(device_class: str) -> str:
    """Read the shell's effective presentation, else follow the device class."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def read(name):
            return bus.call_sync(
                SHELL_STATE_NAME, SHELL_STATE_PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (SHELL_STATE_NAME, name)),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None,
            ).unpack()[0]

        # TV posture sits above the presentation: the couch, not the desk.
        try:
            if read("Posture") == "tv":
                return "tv"
        except GLib.Error:
            pass
        value = read("Presentation")
        if value in PRESENTATIONS:
            return value
    except Exception:
        pass
    return _DEVICE_CLASS_PRESENTATION.get(device_class, "desktop")
