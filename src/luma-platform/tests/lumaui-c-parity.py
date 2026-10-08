#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""LumaUI parity: each C part (LumaUI-1) builds the same widget tree as its
Python twin (luma_appkit), node for node: the same CSS names and classes, so
the one stylesheet styles both identically.

Cases live in lumaui-parity/<part>.py, each defining

    CASES = [("name", lambda C, Gtk: <C widget>, lambda K, Gtk: <Python widget>), ...]

where C is gi.repository.LumaUI and K is luma_appkit. Each pair is compared
fresh, then again after both have been shown in a 1000 px window (parts that
adapt to width change classes when mapped), then at phone width (400 px).

    lumaui-c-parity.py [--list] [--dump NAME] [PART...]

Exit 77 (skip) when the LumaUI typelib or a display is missing.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "appkit"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

try:
    gi.require_version("LumaUI", "1")
    from gi.repository import LumaUI  # noqa: E402
except (ImportError, ValueError) as error:
    print(f"skip: no LumaUI typelib ({error})")
    sys.exit(77)

if not Gtk.init_check():
    print("skip: no display")
    sys.exit(77)

import luma_appkit  # noqa: E402

#: Nodes the walk does not descend into: GTK builds their insides itself,
#: and they differ only by GTK's own internals (text nodes, gizmos).
OPAQUE = {"text", "entry", "textview", "spinner", "image", "picture"}


def tree(widget: Gtk.Widget) -> list:
    """[css name, sorted classes, visible, children...] for `widget` and below."""
    node = [widget.get_css_name(), sorted(widget.get_css_classes()), bool(widget.get_visible())]
    if widget.get_css_name() in OPAQUE:
        return node
    child = widget.get_first_child()
    while child is not None:
        # Popovers and tooltips are parented but float; walked by their own cases.
        if not isinstance(child, (Gtk.Popover,)):
            node.append(tree(child))
        child = child.get_next_sibling()
    return node


def show(text_tree: list, depth: int = 0) -> str:
    name, classes, visible = text_tree[:3]
    line = "  " * depth + name + "".join("." + c for c in classes) + ("" if visible else " [hidden]")
    return "\n".join([line] + [show(child, depth + 1) for child in text_tree[3:]])


def diff(a: list, b: list, path: str = "") -> str | None:
    here = f"{path}/{a[0]}"
    if a[:3] != b[:3]:
        return f"{here}: C {a[0]}.{'.'.join(a[1])}{'' if a[2] else ' [hidden]'}  vs  Python {b[0]}.{'.'.join(b[1])}{'' if b[2] else ' [hidden]'}"
    if len(a) != len(b):
        return f"{here}: C has {len(a) - 3} children, Python {len(b) - 3}"
    for index, (x, y) in enumerate(zip(a[3:], b[3:])):
        found = diff(x, y, f"{here}[{index}]")
        if found:
            return found
    return None


def settle(rounds: int = 30) -> None:
    context = GLib.MainContext.default()
    for _ in range(rounds):
        while context.pending():
            context.iteration(False)
        GLib.usleep(4000)


def in_window(widget: Gtk.Widget, width: int) -> Gtk.Window:
    window = Gtk.Window(default_width=width, default_height=700)
    window.set_child(widget)
    window.present()
    settle()
    return window


def load_cases(names: list[str]) -> list:
    cases = []
    for path in sorted((HERE / "lumaui-parity").glob("*.py")):
        if names and path.stem not in names:
            continue
        spec = importlib.util.spec_from_file_location(f"parity_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for case in getattr(module, "CASES", []):
            cases.append((f"{path.stem}:{case[0]}", case[1], case[2]))
    return cases


def snapshot(make, module, width: int | None) -> tuple[list, Gtk.Widget]:
    """Build one side and walk it; in its own window (alone, so it has the
    focus a real window would) when `width` is given."""
    widget = make(module, Gtk)
    if width is None:
        return tree(widget), widget
    window = in_window(widget, width)
    found = tree(widget)
    window.destroy()
    settle(2)
    return found, widget


def check(name, make_c, make_py) -> list[str]:
    failures = []
    stages = [("fresh", None), ("desktop", 1000), ("phone", 400)]
    for stage, width in stages:
        try:
            c_tree, _c = snapshot(make_c, LumaUI, width)
            py_tree, _py = snapshot(make_py, luma_appkit, width)
        except Exception:  # noqa: BLE001 - a broken case is a failure, not a crash
            failures.append(f"{name} [{stage}]: could not build\n{traceback.format_exc()}")
            return failures
        found = diff(c_tree, py_tree)
        if found:
            failures.append(f"{name} [{stage}]: {found}")
            if os.environ.get("LUMAUI_PARITY_VERBOSE"):
                failures.append("C:\n" + show(c_tree) + "\nPython:\n" + show(py_tree))
            break
    return failures


def main(argv: list[str]) -> int:
    if "--dump" in argv:
        wanted = argv[argv.index("--dump") + 1]
        for name, make_c, make_py in load_cases([]):
            if name == wanted or name.startswith(wanted + ":"):
                print(f"== {name}\nC:\n{show(tree(make_c(LumaUI, Gtk)))}\nPython:\n{show(tree(make_py(luma_appkit, Gtk)))}")
        return 0
    names = [a for a in argv if not a.startswith("-")]
    cases = load_cases(names)
    if "--list" in argv:
        print("\n".join(name for name, *_ in cases))
        return 0
    failures = []
    for name, make_c, make_py in cases:
        found = check(name, make_c, make_py)
        print(("not ok " if found else "ok ") + name)
        failures += found
    print(f"{len(cases) - len({f.split(' [')[0] for f in failures if ' [' in f})}/{len(cases)} parts match")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
