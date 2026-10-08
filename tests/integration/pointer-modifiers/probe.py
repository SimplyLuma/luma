#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Toolkit probe window for the pointer-modifiers integration test.

The probe records, as JSON lines, what an ordinary GTK application observes:
keyboard focus, pointer crossing, motion, button and scroll events, each with
the modifier state the toolkit attached to the event. It never infers or
caches modifier state itself, so the log is a direct record of the toolkit.

    probe.py --name NAME --log PATH [--toolkit gtk4|gtk3] [--maximize]
             [--size WxH]
"""

import argparse
import json
import os
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--name", required=True)
parser.add_argument("--log", required=True)
parser.add_argument("--toolkit", choices=("gtk4", "gtk3"), default="gtk4")
parser.add_argument("--maximize", action="store_true")
parser.add_argument("--size", default="240x160")
args = parser.parse_args()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0" if args.toolkit == "gtk4" else "3.0")
gi.require_version("Gdk", "4.0" if args.toolkit == "gtk4" else "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

log_file = open(args.log, "a", buffering=1)
width, height = (int(v) for v in args.size.split("x"))


def record(kind, **fields):
    fields.update(app=args.name, ev=kind, t=time.monotonic())
    log_file.write(json.dumps(fields) + "\n")


def mods(state):
    state = int(state)
    names = []
    if state & int(Gdk.ModifierType.SHIFT_MASK):
        names.append("SHIFT")
    if state & int(Gdk.ModifierType.CONTROL_MASK):
        names.append("CONTROL")
    if state & int(Gdk.ModifierType.ALT_MASK if args.toolkit == "gtk4"
                   else Gdk.ModifierType.MOD1_MASK):
        names.append("ALT")
    return names


if args.toolkit == "gtk4":
    app = Gtk.Application(application_id=f"org.projectluma.PointerProbe.{args.name}")

    def on_activate(application):
        window = Gtk.ApplicationWindow(application=application, title=args.name)
        window.set_default_size(width, height)
        area = Gtk.DrawingArea()
        area.set_hexpand(True)
        area.set_vexpand(True)
        window.set_child(area)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES)

        def on_scroll(controller, dx, dy):
            state = controller.get_current_event_state()
            record("scroll", dx=dx, dy=dy, state=int(state), mods=mods(state))
            return True

        scroll.connect("scroll", on_scroll)
        area.add_controller(scroll)

        motion = Gtk.EventControllerMotion.new()
        last = {"mods": None}

        def on_enter(controller, x, y):
            record("enter", x=x, y=y)

        def on_leave(controller):
            record("leave")
            last["mods"] = None

        def on_motion(controller, x, y):
            state = controller.get_current_event_state()
            names = mods(state)
            if names != last["mods"]:
                record("motion", x=x, y=y, state=int(state), mods=names)
                last["mods"] = names

        motion.connect("enter", on_enter)
        motion.connect("leave", on_leave)
        motion.connect("motion", on_motion)
        area.add_controller(motion)

        click = Gtk.GestureClick.new()
        click.set_button(0)

        def on_pressed(gesture, n_press, x, y):
            state = gesture.get_current_event_state()
            record("button", x=x, y=y, state=int(state), mods=mods(state))

        click.connect("pressed", on_pressed)
        area.add_controller(click)

        def on_active(win, _pspec):
            record("focus", active=win.is_active())

        window.connect("notify::is-active", on_active)
        if args.maximize:
            window.maximize()
        window.present()
        GLib.timeout_add(300, lambda: (record("ready"), False)[1])

    app.connect("activate", on_activate)
    sys.exit(app.run([sys.argv[0]]))
else:
    window = Gtk.Window(title=args.name)
    # No title bar: its icons need an image loader sandbox that minimal
    # containers cannot start, and the probe needs only its content area.
    window.set_decorated(False)
    window.set_default_size(width, height)
    box = Gtk.EventBox()
    box.set_above_child(True)
    box.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK |
                   Gdk.EventMask.POINTER_MOTION_MASK |
                   Gdk.EventMask.BUTTON_PRESS_MASK |
                   Gdk.EventMask.ENTER_NOTIFY_MASK |
                   Gdk.EventMask.LEAVE_NOTIFY_MASK)
    window.add(box)
    last = {"mods": None}

    def on_scroll(_widget, event):
        record("scroll", state=int(event.state), mods=mods(event.state))
        return True

    def on_motion(_widget, event):
        names = mods(event.state)
        if names != last["mods"]:
            record("motion", x=event.x, y=event.y, state=int(event.state),
                   mods=names)
            last["mods"] = names
        return False

    def on_button(_widget, event):
        record("button", state=int(event.state), mods=mods(event.state))
        return False

    def on_enter(_widget, event):
        record("enter", x=event.x, y=event.y)
        return False

    def on_leave(_widget, event):
        record("leave")
        last["mods"] = None
        return False

    box.connect("scroll-event", on_scroll)
    box.connect("motion-notify-event", on_motion)
    box.connect("button-press-event", on_button)
    box.connect("enter-notify-event", on_enter)
    box.connect("leave-notify-event", on_leave)
    window.connect("notify::is-active",
                   lambda win, _p: record("focus", active=win.is_active()))
    window.connect("destroy", Gtk.main_quit)
    if args.maximize:
        window.maximize()
    window.show_all()
    GLib.timeout_add(300, lambda: (record("ready"), False)[1])
    Gtk.main()
