# SPDX-License-Identifier: GPL-2.0-or-later
"""Drive Settings › About through AT-SPI: capture About, set the Device Name,
read hostnamed back, open System Details and capture it."""
import subprocess
import sys
import time

import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

OUT, TAG = sys.argv[1], sys.argv[2]
DEVICE_NAME = "Nick\u2019s ThinkPad"


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


def dump(path):
    with open(path, "w") as stream:
        for node, depth in nodes():
            try:
                stream.write("  " * depth + f"{node.get_role_name()} '{node.get_name()}' '{node.get_description()}'\n")
            except Exception:
                pass


def hostnamed(prop):
    reply = subprocess.run(["gdbus", "call", "--system", "--dest", "org.freedesktop.hostname1",
                            "--object-path", "/org/freedesktop/hostname1", "--method",
                            "org.freedesktop.DBus.Properties.Get", "org.freedesktop.hostname1", prop],
                           capture_output=True, text=True)
    return (reply.stdout or reply.stderr).strip()


time.sleep(8)
import os
pretty = os.environ.get("EXPECT_NAME") or [l.split("=", 1)[1].strip().strip('"') for l in open("/etc/os-release") if l.startswith("PRETTY_NAME=")][0]
row = find(lambda n: n.get_name() == pretty or pretty in (n.get_description() or ""), 15)
print(f"about os row shows {pretty!r}:", row is not None)
shot("about")
dump(f"{OUT}/{TAG}-tree-about.txt")

def click(node):
    """GTK 4 does not implement AT-SPI grab_focus: click the node's centre."""
    import os
    scale = int(os.environ.get("GDK_SCALE", "1"))
    box = node.get_extents(Atspi.CoordType.WINDOW)
    x, y = (box.x + box.width // 2) * scale, (box.y + box.height // 2) * scale
    subprocess.run(["xdotool", "mousemove", str(x), str(y), "click", "1"], check=False)


def key(*keys):
    subprocess.run(["xdotool", "key", "--clearmodifiers", *keys], check=False)


if os.environ.get("DEVICE_NAME_TEST") == "1":
    entry = find(lambda n: n.get_role_name() in ("text", "entry") and "Device Name" in (n.get_name() or ""), 5)
    print("device name entry:", entry is not None)
    long_name = "The Very Long Name Of A Computer That Keeps On Going Past Sixty Three Bytes"
    for typed in ("ThinkPad", long_name, DEVICE_NAME):
        if entry is None:
            break
        click(entry)
        time.sleep(0.5)
        entry.set_text_contents(typed)
        time.sleep(0.5)
        key("Return")
        time.sleep(3)
        print(f"typed {typed!r}: pretty={hostnamed('PrettyHostname')} static={hostnamed('StaticHostname')}")
    shot("device-name")

details = find(lambda n: n.get_role_name() == "button" and n.get_name() == "System Details", 10)
print("system details row:", details is not None)
if details is not None:
    click(details)
    time.sleep(4)
    shot("details")
    dump(f"{OUT}/{TAG}-tree-details.txt")
    print("details os build row:", find(lambda n: n.get_name() == "20260917.5", 3) is not None)
    print(f"details os name {pretty!r}:", find(lambda n: n.get_role_name() == "label" and n.get_name() == pretty, 3) is not None)
