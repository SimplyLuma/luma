#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""A stand-in MPRIS player: fake_player.py NAME [TITLE [ARTIST]].

Owns org.mpris.MediaPlayer2.NAME, answers Properties, toggles PlaybackStatus
on PlayPause and prints each transport call on its own line. The environment
may set FAKE_DESKTOP (DesktopEntry), FAKE_ART (mpris:artUrl), FAKE_URL
(xesam:url), FAKE_STATUS (Playing or Paused), FAKE_POSITION (microseconds)
and FAKE_CAN (a subset of "play pause next previous")."""

import os
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

NAME = sys.argv[1]
TITLE = sys.argv[2] if len(sys.argv) > 2 else "A song"
ARTIST = sys.argv[3] if len(sys.argv) > 3 else "A band"
PATH = "/org/mpris/MediaPlayer2"
XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="PlayPause"/><method name="Next"/><method name="Previous"/>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Position" type="x" access="read"/>
    <property name="CanControl" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
  </interface>
</node>"""
CAN = os.environ.get("FAKE_CAN", "play pause next").split()
state = {"status": os.environ.get("FAKE_STATUS", "Playing")}


def properties() -> dict:
    return {
        "Identity": GLib.Variant("s", NAME),
        "DesktopEntry": GLib.Variant("s", os.environ.get("FAKE_DESKTOP", "")),
        "CanRaise": GLib.Variant("b", True),
        "PlaybackStatus": GLib.Variant("s", state["status"]),
        "Metadata": GLib.Variant("a{sv}", {
            "xesam:title": GLib.Variant("s", TITLE),
            "xesam:artist": GLib.Variant("as", [ARTIST] if ARTIST else []),
            "xesam:url": GLib.Variant("s", os.environ.get("FAKE_URL", "")),
            "mpris:artUrl": GLib.Variant("s", os.environ.get("FAKE_ART", "file:///usr/share/pixmaps/fedora-logo.png"))}),
        "Position": GLib.Variant("x", int(os.environ.get("FAKE_POSITION", 842_000_000))),
        "CanControl": GLib.Variant("b", True), "CanPlay": GLib.Variant("b", "play" in CAN),
        "CanPause": GLib.Variant("b", "pause" in CAN), "CanGoNext": GLib.Variant("b", "next" in CAN),
        "CanGoPrevious": GLib.Variant("b", "previous" in CAN),
    }


def method(connection, _sender, _path, _interface, name, _parameters, invocation) -> None:
    print(name, flush=True)
    if name == "PlayPause":
        state["status"] = "Paused" if state["status"] == "Playing" else "Playing"
        connection.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged", GLib.Variant(
            "(sa{sv}as)", ("org.mpris.MediaPlayer2.Player", {"PlaybackStatus": properties()["PlaybackStatus"]}, [])))
    invocation.return_value(None)


def acquired(connection, _name) -> None:
    for interface in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
        connection.register_object(PATH, interface, method, lambda *a: properties().get(a[4]), None)


Gio.bus_own_name(Gio.BusType.SESSION, "org.mpris.MediaPlayer2." + NAME, Gio.BusNameOwnerFlags.NONE,
                 acquired, lambda *_: print("ready", flush=True), None)
GLib.MainLoop().run()
