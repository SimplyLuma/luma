# SPDX-License-Identifier: GPL-2.0-or-later
"""Drive Settings › Mouse & Touchpad through AT-SPI: open the TrackPoint tab,
check its strings and capture it."""
import subprocess
import sys
import time

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

OUT, TAG = sys.argv[1], sys.argv[2]
DESCRIPTION = "The small pointer control in the middle of your keyboard."


def walk(node, depth=0):
    try:
        yield node, depth
        for i in range(node.get_child_count()):
            child = node.get_child_at_index(i)
            if child is not None:
                yield from walk(child, depth + 1)
    except Exception:
        return


def nodes():
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        yield from walk(desktop.get_child_at_index(i))


def find(predicate, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        for node, _ in nodes():
            try:
                if predicate(node):
                    return node
            except Exception:
                pass
        time.sleep(0.5)
    return None


def act(node):
    for i in range(node.get_n_actions()):
        if node.get_action_name(i) in ("activate", "click", "press"):
            return node.do_action(i)
    return node.get_n_actions() and node.do_action(0)


def texts():
    seen = []
    for node, _ in nodes():
        try:
            seen += [node.get_name() or "", node.get_description() or ""]
        except Exception:
            pass
    return seen


def dump(path):
    with open(path, "w") as stream:
        for node, depth in nodes():
            try:
                stream.write("  " * depth + f"{node.get_role_name()} '{node.get_name()}' '{node.get_description()}'\n")
            except Exception:
                pass


time.sleep(6)
failures = 0


def check(label, ok):
    global failures
    failures += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label)


tab = find(lambda n: n.get_name() == "TrackPoint" and n.get_role_name() in ("toggle button", "radio button", "button", "page tab"), 20)
check("TrackPoint tab is shown when a pointing stick is present", tab is not None)
if tab is not None:
    act(tab)
time.sleep(2)
found = texts()
check("page title reads TrackPoint", found.count("TrackPoint") >= 2)
check("page explains what a TrackPoint is", any(DESCRIPTION in t for t in found))
check("acceleration row reads Acceleration", "Acceleration" in found)
check("pointer speed row is kept", "Pointer Speed" in found)
check("no Pointing Stick text remains", not any("Pointing Stick" in t or "pointing stick" in t for t in found))
subprocess.run(["import", "-window", "root", f"{OUT}/{TAG}-full.png"], check=False)
dump(f"{OUT}/{TAG}-tree.txt")
print(f"{TAG}: {failures} failure(s)")
sys.exit(1 if failures else 0)
