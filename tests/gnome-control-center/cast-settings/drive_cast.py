# SPDX-License-Identifier: GPL-2.0-or-later
"""Drive Settings › Displays › Cast through AT-SPI and capture each state."""
import subprocess
import sys
import time

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

OUT = sys.argv[1]
TAG = sys.argv[2] if len(sys.argv) > 2 else "dark"


def shot(name):
    subprocess.run(["import", "-window", "root", f"{OUT}/{TAG}-{name}.png"], check=False)


def walk(node, depth=0):
    try:
        yield node, depth
        for i in range(node.get_child_count()):
            child = node.get_child_at_index(i)
            if child is not None:
                yield from walk(child, depth + 1)
    except Exception:
        return


def find(predicate, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            for node, _ in walk(desktop.get_child_at_index(i)):
                try:
                    if predicate(node):
                        return node
                except Exception:
                    pass
        time.sleep(0.5)
    return None


def act(node):
    try:
        for i in range(node.get_n_actions()):
            if node.get_action_name(i) in ("activate", "click", "press", "toggle"):
                node.do_action(i)
                return True
        if node.get_n_actions():
            node.do_action(0)
            return True
    except Exception as error:
        print("action failed", error)
    return False


def dump(path):
    with open(path, "w") as stream:
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            for node, depth in walk(desktop.get_child_at_index(i)):
                try:
                    stream.write("  " * depth + f"{node.get_role_name()} '{node.get_name()}'\n")
                except Exception:
                    pass


time.sleep(8)
# Opened with `gnome-control-center display cast`: the page should already show.
title = find(lambda n: n.get_name() == "Remembered Screens", 15)
print("cast page shown by parameter:", title is not None)
shot("01-cast-page")
dump(f"{OUT}/{TAG}-tree-01.txt")

forget = find(lambda n: (n.get_name() or "").startswith("Forget Conference Room B"), 5)
print("forget button:", forget and forget.get_role_name())
if forget is not None:
    act(forget)
    time.sleep(2)
    shot("02-forget-confirmation")
    cancel = find(lambda n: n.get_name() in ("Cancel", "_Cancel") and n.get_role_name() in ("button", "push button"), 5)
    if cancel is not None:
        act(cancel)
        time.sleep(1)

back = find(lambda n: n.get_name() == "Back" and n.get_role_name() in ("button", "push button"), 5)
if back is not None:
    act(back)
    time.sleep(2)
    shot("03-displays-cast-row")
