#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The Python builders for docks, ArrangeView, AudioMeter and the live BarReadout."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import (ArrangeItem, ArrangeView, AudioMeter, BarReadout,  # noqa: E402
                         CreativeWorkspace, FloatingPanel)
from luma_appkit.creative_parts import lumaui_module  # noqa: E402
from luma_appkit.creative_workspace import apply_layout, workspace_layout  # noqa: E402


def docks():
    seen = []
    panels = [(key, FloatingPanel(key.title(), "layers", child=Gtk.Label(label=key)), side)
              for key, side in (("widgets", "left"), ("inspector", "right"), ("audio", "bottom"))]
    workspace = CreativeWorkspace(Gtk.Label(label="work"), panels=panels, grid=False,
                                  layout=[("audio", "left", True)], on_layout=seen.append)
    assert workspace_layout(workspace) == [("audio", "left", True), ("widgets", "left", False),
                                           ("inspector", "right", False)], workspace_layout(workspace)
    workspace.move_panel("audio", lumaui_module().PanelSide.BOTTOM, -1)
    assert seen and seen[-1][-1] == ("audio", "bottom", True), seen
    apply_layout(workspace, [("inspector", "left", False), ("widgets", "nowhere", True)])
    assert workspace_layout(workspace)[0] == ("inspector", "left", False)
    try:
        CreativeWorkspace(panels=[("a", panels[0][1], "top")])
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown side is refused")


def arrange():
    picked, commits = [], []
    view = ArrangeView(Gtk.Label(label="canvas"), canvas=(1080, 1920),
                       items=[ArrangeItem("a", "Camera", 10, 20, 300, 400, keeps_aspect=True),
                              ArrangeItem("b", "Display", 0, 0, 1080, 1920, locked=True)],
                       on_select=picked.append, on_commit=lambda *args: commits.append(args))
    ok, x, y, w, h = view.get_item_rect("a")
    assert ok and (x, y, w, h) == (10, 20, 300, 400)
    view.set_selected("a")
    assert view.get_selected() == "a" and picked == []
    try:
        ArrangeView(items=[ArrangeItem("a", "A", 0, 0, 0, 10)])
    except ValueError:
        pass
    else:
        raise AssertionError("a box has a size")


def meter():
    m = AudioMeter(2, horizontal=True, label="Microphone")
    assert m.get_horizontal()
    m.set_levels_db(-20, -30)
    assert abs(m.get_level_db(0) + 20) < 1e-6
    try:
        AudioMeter(3)
    except ValueError:
        pass
    else:
        raise AssertionError("channels are 1 or 2")


def readout():
    live = BarReadout("0:00:12", "6,000 kbps", state="live", note="Live")
    widget = live._build()
    assert widget.has_css_class("live")
    live.set("0:00:13", "5,980 kbps")
    live.set_state("connecting", "Reconnecting")
    assert live.state == "connecting" and live.note == "Reconnecting"
    try:
        BarReadout("1", state="paused")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown state is refused")
    try:
        BarReadout("1").set_state("live")
    except ValueError:
        pass
    else:
        raise AssertionError("a plain readout has no state")


if __name__ == "__main__":
    docks()
    arrange()
    meter()
    readout()
    GLib.MainContext.default().iteration(False)
    print("ok")
