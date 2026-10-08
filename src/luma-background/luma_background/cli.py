# SPDX-License-Identifier: MPL-2.0
"""`luma-background`: look at background agents, and nudge them while developing."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import BUS_NAME, INTERFACE, OBJECT_PATH  # noqa: E402

STATE_WORDS = {
    "running": "Running",
    "idle": "Waiting",
    "paused": "Paused for Power Saver",
    "failed": "Stopped after repeated crashes",
    "off": "Off",
}


def _call(method: str, parameters=None, reply: str | None = None):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = connection.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method, parameters,
                                  GLib.VariantType.new(reply) if reply else None,
                                  Gio.DBusCallFlags.NONE, 120_000, None)
    return result.unpack() if result is not None else None


def _size(value: int) -> str:
    if value <= 0:
        return "–"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return ""


def _when(usec: int) -> str:
    if not usec:
        return "never"
    return datetime.fromtimestamp(usec / 1e6).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _print_agent(agent: dict) -> None:
    print(f"{agent['name']} ({agent['app-id']})")
    print(f"  {agent['explanation']}")
    print(f"  State:      {STATE_WORDS.get(agent['state'], agent['state'])}")
    decided = {"allow": "allowed", "deny": "not allowed", "unset": f"default ({agent['default']})"}
    print(f"  Decision:   {decided.get(agent['decision'], agent['decision'])}"
          + ("  · Always On" if agent.get("essential") else ""))
    print(f"  Category:   {agent['category']}  · wakes: {', '.join(agent['wake']) or '—'}")
    print(f"  Last run:   {_when(agent['last-run'])}"
          + (f"  · last exit: {agent['last-exit']}" if agent.get("last-exit") else ""))
    print(f"  Memory:     {_size(agent['memory'])} (peak {_size(agent['memory-peak'])}, "
          f"limit {_size(agent['memory-max'])})  · CPU {agent['cpu-percent']:.1f}%")
    if agent.get("schedules"):
        print(f"  Schedules:  {', '.join(agent['schedules'])}")
    if agent.get("unit"):
        print(f"  Unit:       {agent['unit']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-background",
                                     description="Background agents on this session")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="every registered agent")
    listing.add_argument("--json", action="store_true")
    status = commands.add_parser("status", help="one agent in detail")
    status.add_argument("app_id")
    status.add_argument("--json", action="store_true")
    for name, help_text in (("stop", "stop a running agent until its next wake"),
                            ("allow", "let an app work in the background"),
                            ("deny", "stop an app working in the background")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("app_id")
    wake = commands.add_parser("wake", help="deliver a wake, as the system would")
    wake.add_argument("app_id")
    wake.add_argument("reason", choices=("request", "network", "resume", "login"))
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            (agents,) = _call("ListAgents", None, "(aa{sv})")
            if args.json:
                print(json.dumps(agents, indent=2, sort_keys=True))
            elif not agents:
                print("No apps have background agents.")
            for agent in [] if args.json else agents:
                word = STATE_WORDS.get(agent["state"], agent["state"])
                print(f"{agent['name']:<24} {word:<32} {agent['app-id']}")
        elif args.command == "status":
            (agent,) = _call("GetAgent", GLib.Variant("(s)", (args.app_id,)), "(a{sv})")
            print(json.dumps(agent, indent=2, sort_keys=True)) if args.json else _print_agent(agent)
        elif args.command == "stop":
            _call("StopNow", GLib.Variant("(s)", (args.app_id,)))
        elif args.command in {"allow", "deny"}:
            _call("SetAllowed", GLib.Variant("(sb)", (args.app_id, args.command == "allow")))
        elif args.command == "wake":
            _call("Wake", GLib.Variant("(ss)", (args.app_id, args.reason)))
    except GLib.Error as error:
        remote = Gio.DBusError.get_remote_error(error) or ""
        Gio.DBusError.strip_remote_error(error)
        if remote.endswith("NotAuthorized") and args.command in {"allow", "deny", "wake", "stop"}:
            print(f"luma-background: {error.message}\nUse Settings › Apps › Background Activity, "
                  "or the app's menu in the dock.", file=sys.stderr)
        else:
            print(f"luma-background: {error.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
