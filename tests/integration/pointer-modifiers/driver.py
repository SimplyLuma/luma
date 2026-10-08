#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Headless end-to-end check: modifiers reach the application under the pointer.

Starts a headless Mutter with a virtual monitor, three probe windows and a
RemoteDesktop input session, then drives the owner's scenario:

  A  "target"    maximized; plays the role of Canvas
  C  "bystander" never focused or hovered after setup
  B  "other"     clicked, so it holds keyboard focus

Each scenario is judged from what the toolkit reports in its event state and
from the Wayland wire log (WAYLAND_DEBUG=client) of each probe. Run it inside a
private D-Bus session (see run.sh). Exit status is the number of failures.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

from gi.repository import Gio, GLib

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
BTN_LEFT = 272
AXIS_VERTICAL = 0

parser = argparse.ArgumentParser()
parser.add_argument("--mutter", default="mutter")
parser.add_argument("--workdir", required=True)
parser.add_argument("--target-toolkit", choices=("gtk4", "gtk3", "gtk3-x11"),
                    default="gtk4",
                    help="gtk3-x11 runs the target as an X11 client under Xwayland")
args = parser.parse_args()

os.makedirs(args.workdir, exist_ok=True)
runtime = os.environ["XDG_RUNTIME_DIR"]
display_name = "luma-pointer-modifiers"
children = []
results = []


def spawn(argv, log_name, extra_env=None):
    env = dict(os.environ)
    env.update(extra_env or {})
    out = open(os.path.join(args.workdir, log_name), "w")
    proc = subprocess.Popen(argv, env=env, stdout=out, stderr=subprocess.STDOUT)
    children.append(proc)
    return proc


def wait_for(predicate, timeout=15.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


# -- compositor ---------------------------------------------------------------
x11_target = args.target_toolkit == "gtk3-x11"
spawn([args.mutter, "--headless", "--wayland",
       *([] if x11_target else ["--no-x11"]),
       "--virtual-monitor", "1024x768", "--wayland-display", display_name],
      "mutter.log")
if not wait_for(lambda: os.path.exists(os.path.join(runtime, display_name)), 30):
    sys.exit("mutter did not create its Wayland socket")


def x11_display():
    with open(os.path.join(args.workdir, "mutter.log"), errors="replace") as handle:
        found = re.search(r"Using public X11 display (:\d+)", handle.read())
    return found.group(1) if found else None


if x11_target and not wait_for(x11_display, 30):
    sys.exit("mutter did not announce an X11 display")

bus = Gio.bus_get_sync(Gio.BusType.SESSION)


def call(path, interface, method, signature=None, values=(), reply=None):
    params = GLib.Variant(signature, values) if signature else None
    return bus.call_sync("org.gnome.Mutter.RemoteDesktop", path, interface,
                         method, params,
                         GLib.VariantType(reply) if reply else None,
                         Gio.DBusCallFlags.NONE, 5000, None)


wait_for(lambda: bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "NameHasOwner",
                               GLib.Variant("(s)", ("org.gnome.Mutter.RemoteDesktop",)),
                               GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE,
                               1000, None).unpack()[0], 30)
session = call("/org/gnome/Mutter/RemoteDesktop", "org.gnome.Mutter.RemoteDesktop",
               "CreateSession", reply="(o)").unpack()[0]
SESSION_IFACE = "org.gnome.Mutter.RemoteDesktop.Session"
call(session, SESSION_IFACE, "Start")


def key(code, pressed):
    call(session, SESSION_IFACE, "NotifyKeyboardKeycode", "(ub)", (code, pressed))
    time.sleep(0.12)


def move(dx, dy):
    call(session, SESSION_IFACE, "NotifyPointerMotionRelative", "(dd)",
         (float(dx), float(dy)))
    time.sleep(0.12)


def move_to(x, y):
    move(-4000, -4000)
    move(x, y)


def scroll(steps=1):
    call(session, SESSION_IFACE, "NotifyPointerAxisDiscrete", "(ui)",
         (AXIS_VERTICAL, steps))
    time.sleep(0.25)


def click():
    call(session, SESSION_IFACE, "NotifyPointerButton", "(ib)", (BTN_LEFT, True))
    call(session, SESSION_IFACE, "NotifyPointerButton", "(ib)", (BTN_LEFT, False))
    time.sleep(0.4)


# -- probes -------------------------------------------------------------------
probe_env = {"WAYLAND_DISPLAY": display_name, "GDK_BACKEND": "wayland",
             "WAYLAND_DEBUG": "client", "GTK_A11Y": "none",
             "NO_AT_BRIDGE": "1"}


def probe_log(name):
    return os.path.join(args.workdir, f"{name}.events")


def launch(name, *extra, toolkit="gtk4"):
    path = probe_log(name)
    open(path, "w").close()
    env = dict(probe_env)
    if toolkit == "gtk3-x11":
        toolkit = "gtk3"
        auth = [f for f in os.listdir(runtime) if f.startswith(".mutter-Xwaylandauth.")]
        env.update(GDK_BACKEND="x11", DISPLAY=x11_display())
        if auth:
            env["XAUTHORITY"] = os.path.join(runtime, auth[0])
    spawn([sys.executable, os.path.join(HERE, "probe.py"), "--name", name,
           "--log", path, "--toolkit", toolkit, *extra],
          f"{name}.wire", env)
    if not wait_for(lambda: any(e["ev"] == "ready" for e in events(name)), 30):
        sys.exit(f"probe {name} did not become ready")


def events(name):
    with open(probe_log(name)) as handle:
        return [json.loads(line) for line in handle if line.strip()]


MODIFIERS_RE = re.compile(r"wl_keyboard#\d+\.modifiers\((\d+), (\d+), (\d+), (\d+), (\d+)\)")


def wire_modifiers(name):
    with open(os.path.join(args.workdir, f"{name}.wire"), errors="replace") as handle:
        return [tuple(int(v) for v in m.groups()) for m in MODIFIERS_RE.finditer(handle.read())]


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  -- ' + detail) if detail else ''}",
          flush=True)


def last_scroll_mods(name, since):
    scrolls = [e for e in events(name)[since:] if e["ev"] == "scroll"]
    return scrolls[-1]["mods"] if scrolls else None


try:
    # A headless seat has no keyboard until the first key event creates the
    # virtual one; create it before any window maps.
    key(KEY_LEFTSHIFT, True)
    key(KEY_LEFTSHIFT, False)

    launch("target", "--maximize", toolkit=args.target_toolkit)
    launch("bystander", "--size", "200x150")
    launch("other", "--size", "200x150")

    # Find a point over the target that is not covered by the others, and a
    # point over the focused window.
    grid = [(x, y) for y in range(160, 768, 100) for x in range(60, 1024, 120)]
    target_point = other_point = None
    for x, y in grid:
        marks = {n: len(events(n)) for n in ("target", "bystander", "other")}
        move_to(x, y)
        move(1, 1)
        entered = [n for n in marks if any(e["ev"] in ("enter", "motion") for e in events(n)[marks[n]:])]
        if entered == ["target"] and target_point is None:
            target_point = (x, y)
        if "other" in entered and other_point is None:
            other_point = (x, y)
        if target_point and other_point:
            break
    check("setup: located target and focused window", target_point and other_point,
          f"target={target_point} other={other_point}")
    if not (target_point and other_point):
        raise SystemExit(1)

    # The owner's sequence: click into another application, then come back
    # to the target by hovering only.
    def keyboard_owner():
        latest = {}
        for name in ("target", "bystander", "other"):
            with open(os.path.join(args.workdir, f"{name}.wire"), errors="replace") as handle:
                for line in handle:
                    if ".enter(" in line and "wl_keyboard#" in line:
                        latest[name] = ("enter", line)
                    elif ".leave(" in line and "wl_keyboard#" in line:
                        latest[name] = ("leave", line)
        owners = [n for n, (kind, _l) in latest.items() if kind == "enter"]
        return owners if owners == ["other"] else None

    move_to(*other_point)
    click()
    check("setup: clicking the other window gives it keyboard focus",
          wait_for(keyboard_owner, 10))

    bystander_wire_baseline = len(wire_modifiers("bystander"))

    # S1: unfocused, hovered, Ctrl held, scroll
    move_to(*target_point)
    mark = len(events("target"))
    key(KEY_LEFTCTRL, True)
    scroll()
    got = last_scroll_mods("target", mark)
    check("S1 unfocused hovered window: Ctrl+scroll carries CONTROL", got and "CONTROL" in got, f"mods={got}")

    # S4: hover affordance while Ctrl is still held
    mark = len(events("target"))
    move(4, 0)
    motions = [e for e in events("target")[mark:] if e["ev"] == "motion"]
    check("S4 unfocused hovered window: motion carries CONTROL while held",
          motions and "CONTROL" in motions[-1]["mods"], f"motions={[m['mods'] for m in motions]}")
    key(KEY_LEFTCTRL, False)

    # S3: released while hovering, no stale CONTROL on the next scroll
    mark = len(events("target"))
    scroll()
    got = last_scroll_mods("target", mark)
    check("S3 after release: plain scroll carries no modifiers", got == [], f"mods={got}")

    # S2: Shift+scroll
    mark = len(events("target"))
    key(KEY_LEFTSHIFT, True)
    scroll()
    key(KEY_LEFTSHIFT, False)
    got = last_scroll_mods("target", mark)
    check("S2 unfocused hovered window: Shift+scroll carries SHIFT", got and "SHIFT" in got, f"mods={got}")

    # L2/L3: leaving resets, and a window the pointer left learns nothing more
    key(KEY_LEFTCTRL, True)
    scroll()
    wire_before_leave = len(wire_modifiers("target"))
    move_to(*other_point)
    move(1, 1)
    after_leave = wire_modifiers("target")[wire_before_leave:]
    if not x11_target:
        check("L3 pointer leave: the left window receives a released-modifiers reset",
              any(m[1] == 0 for m in after_leave), f"events={after_leave}")
    settled = len(wire_modifiers("target"))
    key(KEY_LEFTCTRL, False)
    key(KEY_LEFTSHIFT, True)
    key(KEY_LEFTSHIFT, False)
    key(KEY_LEFTCTRL, True)
    key(KEY_LEFTCTRL, False)
    if not x11_target:
        check("L2 no leak: a window the pointer left gets no modifier updates",
              len(wire_modifiers("target")) == settled,
              f"extra={wire_modifiers('target')[settled:]}")

    # Back over the target without modifiers: nothing stale
    move_to(*target_point)
    mark = len(events("target"))
    scroll()
    got = last_scroll_mods("target", mark)
    check("S5 re-entering without modifiers: scroll carries no modifiers", got == [], f"mods={got}")

    # L1: the bystander was never hovered nor focused
    check("L1 no leak: a window neither focused nor hovered gets no modifier events",
          len(wire_modifiers("bystander")) == bystander_wire_baseline,
          f"extra={wire_modifiers('bystander')[bystander_wire_baseline:]}")

    # F1: focused behaviour is unchanged
    click()
    wait_for(lambda: [e for e in events("target") if e["ev"] == "focus" and e["active"]], 5)
    mark = len(events("target"))
    key(KEY_LEFTCTRL, True)
    scroll()
    key(KEY_LEFTCTRL, False)
    scroll()
    scrolls = [e["mods"] for e in events("target")[mark:] if e["ev"] == "scroll"]
    check("F1 focused window: Ctrl+scroll then plain scroll", scrolls[-2:] == [["CONTROL"], []],
          f"scrolls={scrolls}")

    # R1: Ctrl released while another window is focused; no stale state on return
    key(KEY_LEFTCTRL, True)
    move_to(*other_point)
    click()
    key(KEY_LEFTCTRL, False)
    move_to(*target_point)
    mark = len(events("target"))
    scroll()
    unfocused = last_scroll_mods("target", mark)
    click()
    mark = len(events("target"))
    scroll()
    refocused = last_scroll_mods("target", mark)
    check("R1 Ctrl released elsewhere: no stale CONTROL hovering or after refocus",
          unfocused == [] and refocused == [], f"hover={unfocused} refocus={refocused}")
finally:
    for proc in reversed(children):
        proc.terminate()
    for proc in children:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

failures = [r for r in results if not r[1]]
with open(os.path.join(args.workdir, "results.json"), "w") as handle:
    json.dump([{"check": l, "pass": ok, "detail": d} for l, ok, d in results], handle, indent=2)
print(f"{len(results) - len(failures)}/{len(results)} checks passed")
sys.exit(len(failures))
