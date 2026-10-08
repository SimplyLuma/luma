# SPDX-License-Identifier: GPL-2.0-or-later
"""Dump the accessibility tree of the app under test, roles and names."""
import gi
gi.require_version("Atspi", "2.0")
from gi.repository import Atspi


def walk(node, depth=0):
    try:
        yield node, depth
        for i in range(node.get_child_count()):
            child = node.get_child_at_index(i)
            if child is not None:
                yield from walk(child, depth + 1)
    except Exception:
        return


desktop = Atspi.get_desktop(0)
for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    if app is None:
        continue
    for node, depth in walk(app):
        try:
            print(f"{'  ' * depth}{node.get_role_name()}: {node.get_name() or ''}")
        except Exception:
            continue
