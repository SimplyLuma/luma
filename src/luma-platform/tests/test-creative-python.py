#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The Python builders for docks, ArrangeView, AudioMeter and the live chip's updates."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

Gtk.init()

from luma_appkit import (ArrangeItem, ArrangeView, AudioMeter, BarChip,  # noqa: E402
                         CreativeWorkspace, FloatingPanel)
from luma_appkit.action_center import make_control  # noqa: E402
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


def live_chip():
    chip = BarChip("Live", meta="0:00:12", detail="6,000 kbps", live=True)
    widget = make_control(chip)
    assert widget.has_css_class("live")
    chip.update(meta="0:00:13", detail="5,980 kbps · no dropped frames")
    assert widget.time_label.get_label() == "0:00:13"
    assert widget.detail_label.get_label().startswith("5,980")
    chip.update("Reconnecting")
    assert chip.label == "Reconnecting"
    BarChip("Recording", meta="0:01").update(meta="0:02")  # not drawn yet: only the data changes


def dock():
    from luma_appkit import Dock, DockTile, NavigationSidebar

    tiles = [DockTile(k, k.title(), "layers", Gtk.Label(label=k), main=(k == "preview"))
             for k in ("preview", "audio", "inspector")]
    seen = []
    layout = Dock.split("h", 0.7, Dock.split("v", 0.6, "preview", "audio"), "inspector")
    d = Dock(tiles, layout=layout, on_layout=seen.append)
    assert d.layout() == layout, d.layout()
    assert d.main_key == "preview"
    d.move_tile("inspector", "preview", "left")
    assert d.layout() == {"split": "v", "ratio": 0.6, "start": {"split": "h", "ratio": 0.5, "start": "inspector",
                                                                "end": "preview"}, "end": "audio"}, d.layout()
    d.move_tile("audio", "inspector", "center")
    assert d.layout()["end"] == "inspector" and d.layout()["start"]["start"] == "audio"
    # Unknown keys go, repeated keys go, missing tiles join; ratios are clamped.
    d.set_layout({"split": "v", "ratio": 3, "start": "ghost", "end": {"split": "h", "ratio": 0.4,
                                                                      "start": "audio", "end": "audio"}})
    assert d.layout() == {"split": "h", "ratio": 0.75, "start": {"split": "h", "ratio": 0.75, "start": "audio",
                                                                 "end": "preview"}, "end": "inspector"}, d.layout()
    for bad in (lambda: Dock([]), lambda: Dock.split("x", 0.5, "a", "b"),
                lambda: d.move_tile("audio", "preview", "middle")):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("refused")
    sidebar = NavigationSidebar(variant="files")
    shares = []
    pane = sidebar.append_pane("Widgets", Gtk.Label(label="tree"), share=0.4, on_share=shares.append)
    assert pane is sidebar.pane and sidebar.pane_split.get_end_child() is pane
    try:
        sidebar.append_pane("Again", Gtk.Label())
    except ValueError:
        pass
    else:
        raise AssertionError("one pane")


if __name__ == "__main__":
    docks()
    arrange()
    meter()
    live_chip()
    dock()
    GLib.MainContext.default().iteration(False)
    print("ok")
