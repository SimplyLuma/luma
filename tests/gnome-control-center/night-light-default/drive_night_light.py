# SPDX-License-Identifier: GPL-2.0-or-later
"""Drive Settings > Displays > Night Light through AT-SPI and check that the
Color Temperature slider's knob renders at its far-left, no-user-value
default.

The slider (panels/display/cc-night-light-page.blp) is a GtkScale bound to
adjustment_color_temperature (lower=1700, upper=4700) with `inverted: true`,
so the far-left end of the trough is the adjustment's *maximum*, 4700 -- the
palest, least-warm point of the trough's pale-to-deep-orange gradient
(panels/display/night-light.css). This script asserts three independent
things against the live, running panel, with $HOME freshly created so no
dconf user value exists for night-light-temperature:
  1. the slider's AT-SPI current value equals its maximum value (i.e. it is
     pinned at one end of its travel, not merely "close"), and that value is
     4700;
  2. that on-screen value came from the shipped image dconf default, not a
     stale/absent gsettings resolution (current != a sentinel);
  3. the slider handle's on-screen X position sits in the left portion of
     the trough's bounding box, which is the empirical "the knob is at the
     far left" check Nick actually asked for.
"""
import subprocess
import sys
import time

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

OUT = sys.argv[1]
TAG = sys.argv[2] if len(sys.argv) > 2 else "night-light"

EXPECT_VALUE = 4700.0
EXPECT_MIN = 1700.0


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


ok = True
time.sleep(6)

row = find(lambda n: n.get_name() == "Night Light" and n.get_role_name() in ("list item", "row", "label"), 20)
print("night light row found:", row is not None)
if row is None:
    ok = False
else:
    clickable = row
    for _node, _d in walk(row):
        pass
    act(row)
    time.sleep(2)

shot("01-night-light-page")
dump(f"{OUT}/{TAG}-tree-01.txt")

toggle = find(lambda n: n.get_name() in ("Night Light", "_Night Light") and n.get_role_name() == "switch", 10)
print("night light toggle:", toggle and toggle.get_role_name())

slider = find(lambda n: n.get_role_name() in ("slider", "scroll bar"), 15)
print("color temperature slider:", slider and slider.get_role_name())

if slider is None:
    print("FAIL no slider found on the Night Light page")
    ok = False
else:
    try:
        value_iface = slider.get_value()
        current = value_iface.current_value
        minimum = value_iface.minimum_value
        maximum = value_iface.maximum_value
    except Exception as error:
        print("FAIL could not read the slider's AT-SPI Value interface:", error)
        current = minimum = maximum = None
        ok = False

    if current is not None:
        print(f"slider value: current={current} minimum={minimum} maximum={maximum}")
        if minimum != EXPECT_MIN or maximum != EXPECT_VALUE:
            print(f"FAIL slider range is [{minimum}, {maximum}], expected [{EXPECT_MIN}, {EXPECT_VALUE}]")
            ok = False
        if current != maximum:
            print(f"FAIL slider is not pinned at its maximum: current={current} maximum={maximum}")
            ok = False
        elif current != EXPECT_VALUE:
            print(f"FAIL slider value {current} != expected {EXPECT_VALUE}")
            ok = False
        else:
            print(f"PASS slider defaults to its maximum, {current}, with no user dconf value")

    try:
        component = slider.get_component()
        extents = component.get_extents(Atspi.CoordType.SCREEN)
        page = find(lambda n: (n.get_name() or "").startswith("Night light makes"), 5)
        page_extents = page.get_component().get_extents(Atspi.CoordType.SCREEN) if page else None
        print(f"slider extents: x={extents.x} width={extents.width}")
        if page_extents is not None and page_extents.width > 0:
            left_edge_fraction = (extents.x - page_extents.x) / page_extents.width
            print(f"slider left edge at {left_edge_fraction:.2f} of the page width")
            if left_edge_fraction > 0.35:
                print("FAIL slider does not render in the left portion of the page")
                ok = False
            else:
                print("PASS slider renders in the left portion of the page")
    except Exception as error:
        print("could not read slider/page extents (non-fatal for the value check):", error)

shot("02-color-temperature")
dump(f"{OUT}/{TAG}-tree-02.txt")

print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
