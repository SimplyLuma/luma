#!/usr/bin/python3
"""Smoke the public Python CR1 builders against the introspected native kit."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from luma_appkit import CreativeWorkspace, FloatingPanel


def main():
    changed = []
    panel = FloatingPanel(
        "Library", "music-2", child=Gtk.Label(label="Sounds"),
        summary="Selected track", on_folded=changed.append,
    )
    workspace = CreativeWorkspace(Gtk.Label(label="Timeline"), left=panel, grid=False)
    assert workspace.get_left() is panel
    assert not panel.get_folded()
    panel.set_folded(True)
    assert panel.get_folded() and changed == [True]
    try:
        FloatingPanel("Invalid", "music-2", child=Gtk.Label(), pages=[("p", "Page", Gtk.Label())])
    except ValueError:
        pass
    else:
        raise AssertionError("child and pages must be mutually exclusive")


if __name__ == "__main__":
    main()
