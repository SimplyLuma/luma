#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The Python builders for docks, ArrangeView, AudioMeter and the live chip's updates."""
import time
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
    from luma_appkit import RowLead, SidebarRow

    sidebar = NavigationSidebar(variant="files")
    shares = []
    pane = sidebar.append_pane("Widgets", share=0.4, on_share=shares.append)
    assert pane is sidebar.pane and isinstance(pane, Gtk.ListBox)
    assert set(sidebar.list.get_css_classes()) <= set(pane.get_css_classes())
    pane.append(SidebarRow("Camera", lead=RowLead.icon("camera")))
    pane.append(SidebarRow("Display", lead=RowLead.icon("monitor")))
    sidebar.clear_pane()
    rows = []
    child = pane.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    assert len(rows) == 1 and rows[0].has_css_class("luma-navigation-section"), rows
    try:
        sidebar.append_pane("Again")
    except ValueError:
        pass
    else:
        raise AssertionError("one pane")


def settle(seconds=0.4):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def fitted_dock():
    """A tile that fits its content stops the seam at its content; a phone keeps it in view."""
    from luma_appkit import Dock, DockTile, RowAction, SidebarRow

    content = Gtk.Box(height_request=150)
    preview = DockTile("preview", "Preview", "monitor", Gtk.Box(), main=True, bare=True)
    audio = DockTile("audio", "Audio", "volume-2", content, fit=True)
    d = Dock([preview, audio], layout=Dock.split("v", 0.5, "preview", "audio"))
    window = Gtk.Window(default_width=900, default_height=700)
    window.set_child(d)
    window.present()
    settle()
    natural = audio.measure(Gtk.Orientation.VERTICAL, audio.get_width())[1]
    assert abs(audio.get_height() - natural) <= 1, (audio.get_height(), natural)
    assert preview.get_height() > audio.get_height(), (preview.get_height(), audio.get_height())
    content.set_size_request(-1, 220)
    d.refit()
    settle()
    natural = audio.measure(Gtk.Orientation.VERTICAL, audio.get_width())[1]
    assert abs(audio.get_height() - natural) <= 1, (audio.get_height(), natural)
    window.set_default_size(400, 700)
    settle()
    assert d.phone and audio.get_mapped() and audio.has_css_class("inline"), "a fitted tile stays in view"
    window.set_default_size(900, 700)
    settle()
    assert not d.phone and not audio.has_css_class("inline")
    window.destroy()
    # A quiet row action waits for its row.
    row = SidebarRow("Camera", trail=RowAction("eye", "Hide Camera", lambda: None, quiet=True))
    assert row.trail_widget.has_css_class("quiet")
    row = SidebarRow("Display", trail=RowAction("eye-off", "Show Display", lambda: None))
    assert not row.trail_widget.has_css_class("quiet")


if __name__ == "__main__":
    docks()
    arrange()
    meter()
    live_chip()
    dock()
    fitted_dock()
    GLib.MainContext.default().iteration(False)
    print("ok")
