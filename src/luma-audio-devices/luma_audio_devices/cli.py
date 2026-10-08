# SPDX-License-Identifier: MPL-2.0
"""Command line access to org.projectluma.AudioDevices1.

    luma-audio-devices airplay [--browse SECONDS]
    luma-audio-devices airplay connect ID [--password-stdin]
    luma-audio-devices airplay forget ID
    luma-audio-devices outputs
    luma-audio-devices outputs ask|never KEY
    luma-audio-devices outputs forget KEY
"""

from __future__ import annotations

import argparse
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.projectluma.AudioDevices"
OBJECT_PATH = "/org/projectluma/AudioDevices"
INTERFACE = "org.projectluma.AudioDevices1"


def _call(bus, method: str, parameters=None, reply_type: str | None = None, timeout: int = 30000):
    result = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method, parameters,
                           GLib.VariantType.new(reply_type) if reply_type else None,
                           Gio.DBusCallFlags.NONE, timeout, None)
    return result.unpack() if result is not None else None


def _print_receivers(bus) -> None:
    (receivers,) = _call(bus, "GetAirPlayReceivers", None, "(aa{sv})")
    if not receivers:
        print("No AirPlay receivers.")
    for entry in receivers:
        flags = [entry["state"]]
        if entry.get("remembered"):
            flags.append("remembered")
        if entry.get("requires_password"):
            flags.append("password")
        if entry.get("error"):
            flags.append("error=" + entry["error"])
        print(f"{entry['id']}\t{entry['name']}\t{', '.join(flags)}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="luma-audio-devices")
    sub = parser.add_subparsers(dest="command", required=True)
    airplay = sub.add_parser("airplay", help="AirPlay receivers")
    airplay.add_argument("action", nargs="?", choices=("list", "connect", "forget"), default="list")
    airplay.add_argument("id", nargs="?")
    airplay.add_argument("--browse", type=float, default=0.0, metavar="SECONDS",
                         help="look for receivers on the network for this long first")
    airplay.add_argument("--password-stdin", action="store_true")
    outputs = sub.add_parser("outputs", help="output devices and their answers")
    outputs.add_argument("action", nargs="?", choices=("list", "forget"), default="list")
    outputs.add_argument("key", nargs="?")
    args = parser.parse_args(argv)

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        if args.command == "airplay":
            if args.browse > 0:
                _call(bus, "StartAirPlayBrowsing")
                context = GLib.MainContext.default()
                deadline = time.monotonic() + args.browse
                while time.monotonic() < deadline:
                    context.iteration(False)
                    time.sleep(0.05)
            if args.action == "list":
                _print_receivers(bus)
            elif not args.id:
                parser.error("an AirPlay receiver id is required")
            elif args.action == "connect":
                password = sys.stdin.readline().rstrip("\n") if args.password_stdin else ""
                _call(bus, "ConnectAirPlayReceiver", GLib.Variant("(ss)", (args.id, password)), None, 60000)
                print("Connected.")
            elif args.action == "forget":
                _call(bus, "ForgetAirPlayReceiver", GLib.Variant("(s)", (args.id,)))
                print("Forgotten.")
            if args.browse > 0:
                _call(bus, "StopAirPlayBrowsing")
        else:
            if args.action == "list":
                (devices,) = _call(bus, "GetOutputDevices", None, "(aa{sv})")
                for entry in devices:
                    present = "present" if entry.get("present") else "away"
                    automatic = "switches" if entry.get("auto_switch") else "stays"
                    print(f"{entry['key']}\t{entry['name']}\t{automatic}\t{present}")
            elif not args.key:
                parser.error("a device key is required")
            else:
                _call(bus, "ForgetOutputDevice", GLib.Variant("(s)", (args.key,)))
    except GLib.Error as error:
        message = Gio.DBusError.get_remote_error(error) or ""
        text = error.message.split(": ", 1)[-1] if message else error.message
        print(text, file=sys.stderr)
        return 2
    return 0
