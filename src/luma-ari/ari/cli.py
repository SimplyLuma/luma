# SPDX-License-Identifier: Apache-2.0
"""`ari`: talk to Ari from a terminal, through the same daemon as the overlay."""
from __future__ import annotations

import argparse
import json
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from . import BUS_NAME, OBJECT_PATH

IFACE = "org.projectluma.Ari1"


def _proxy() -> Gio.DBusProxy:
    return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                                          BUS_NAME, OBJECT_PATH, IFACE, None)


def _call(proxy, method, signature="()", *args, timeout=30000):
    value = proxy.call_sync(method, GLib.Variant(signature, args) if args else None,
                            Gio.DBusCallFlags.NONE, timeout, None)
    return value.unpack() if value is not None else ()


def _follow(proxy, request: str, *, quiet: bool = False) -> int:
    loop = GLib.MainLoop()
    status = [0]

    def on_signal(_p, _sender, name, parameters):
        if name != "Event":
            return
        req, raw = parameters.unpack()
        if req != request:
            return
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "text" and not quiet:
            sys.stdout.write(event["text"])
            sys.stdout.flush()
        elif kind == "discard_text" and not quiet:
            sys.stdout.write("\r\033[K")
        elif kind == "tool":
            sys.stderr.write(f"\n· {event['label']}…\n")
        elif kind == "step":
            sys.stderr.write(f"\n✓ {event['summary']}  (undo: ari undo {event['step']})\n")
        elif kind == "working" and event.get("tokens_per_second"):
            sys.stderr.write(f"\r  {event['label']} · {event['tokens_per_second']} tokens/s")
        elif kind == "download":
            sys.stderr.write(f"\r  {event['done'] / 1e9:.2f} of {event['total'] / 1e9:.2f} GB")
        elif kind in ("installed",):
            sys.stderr.write(f"\nInstalled {event['model']}\n")
            loop.quit()
        elif kind == "done":
            sys.stdout.write("\r\033[K" + event["text"] + "\n")
            for source in event.get("sources") or []:
                sys.stdout.write(f"  Source: {source.get('name')} {source.get('url', '')}\n")
            loop.quit()
        elif kind == "error":
            sys.stderr.write(f"\n{event['message']}\n")
            status[0] = 1
            if event.get("fatal", True):
                loop.quit()
    proxy.connect("g-signal", on_signal)
    try:
        loop.run()
    except KeyboardInterrupt:
        _call(proxy, "Stop", "(s)", request)
        return 130
    return status[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ari", description="Ask Ari, Luma's assistant.")
    sub = parser.add_subparsers(dest="command")
    ask = sub.add_parser("ask", help="Ask or tell Ari something")
    ask.add_argument("text", nargs="+")
    ask.add_argument("--conversation", default="")
    sub.add_parser("models", help="Models on this machine")
    install = sub.add_parser("install", help="Download a model (you agree to the download)")
    install.add_argument("model")
    use = sub.add_parser("use", help="Use an installed model")
    use.add_argument("model")
    undo = sub.add_parser("undo", help="Undo a step")
    undo.add_argument("step")
    keep = sub.add_parser("keep", help="Keep a change that would otherwise revert")
    keep.add_argument("step")
    sub.add_parser("activity", help="Recent activity")
    args, rest = parser.parse_known_args(argv)
    proxy = _proxy()
    if args.command is None:
        if not rest:
            parser.print_help()
            return 2
        args.command, args.text, args.conversation = "ask", rest, ""
    if args.command == "ask":
        _conversation, request = _call(proxy, "Ask", "(ss)", args.conversation, " ".join(args.text))
        return _follow(proxy, request)
    if args.command == "models":
        data = json.loads(_call(proxy, "Models")[0])
        print(f"This machine: {data['summary']} · tier {data['hardware']['tier']}")
        for model in data["models"]:
            mark = "●" if model["id"] == data["active"] else ("✓" if model["installed"] else " ")
            rec = "  (recommended)" if model["id"] == data["recommended"] else ""
            print(f" {mark} {model['id']:16} {model['name']} {model['quantisation']} · "
                  f"{model['size'] / 1e9:.1f} GB · {model['licence']}{rec}")
        return 0
    if args.command == "install":
        return _follow(proxy, _call(proxy, "InstallModel", "(s)", args.model)[0])
    if args.command == "use":
        return 0 if _call(proxy, "SetActiveModel", "(s)", args.model)[0] else 1
    if args.command == "undo":
        return 0 if _call(proxy, "Undo", "(s)", args.step)[0] else 1
    if args.command == "keep":
        return 0 if _call(proxy, "KeepStep", "(s)", args.step)[0] else 1
    if args.command == "activity":
        for entry in json.loads(_call(proxy, "Activity", "(u)", 30)[0]):
            print(f"{entry['time']}  {entry.get('decision', ''):8} {entry.get('tool', '')}  {entry.get('summary', entry.get('reason', ''))}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
