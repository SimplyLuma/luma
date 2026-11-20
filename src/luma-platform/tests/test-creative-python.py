#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The Python builders for docks, ArrangeView, AudioMeter and the live chip's updates."""
import time
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

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


def picker():
    from gi.repository import Gdk
    from luma_appkit import ColorPicker, PropertyColor, recent_colors

    changes, commits = [], []
    p = ColorPicker("1F6FE0", palette=["FF0000"], on_change=changes.append, on_commit=commits.append)
    blue = Gdk.RGBA()
    blue.parse("#1F6FE0")
    assert p.get_rgba().equal(blue), p.get_rgba().to_string()
    assert isinstance(recent_colors(), list)
    # A field hosts its picker where the app says: here a stand-in for an ActionCenter's grow().
    grown = []

    class Bar:
        def grow(self, key, widget):
            grown.append((key, widget))

    field = PropertyColor("Fill", "1F6FE0", bar=Bar(), on_commit=commits.append)
    field.get_first_child().emit("clicked")
    assert grown and grown[0][0] == "lumaui-color-Fill" and type(grown[0][1]).__name__ == "ColorPicker", grown


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
    # A croppable box keeps the app's crop; crop mode and the drag mode reach Python.
    modes, crops = [], []
    view = ArrangeView(items=[ArrangeItem("w", "Wide", 0, 0, 1280, 360, keeps_aspect=True, croppable=True,
                                          crop=(0.1, 0, 0.2, 0))],
                       on_crop=lambda *a: crops.append(a), on_drag_mode=modes.append)
    ok, *crop = view.get_item_crop("w")
    assert ok and [round(c, 6) for c in crop] == [0.1, 0, 0.2, 0], crop
    view.set_cropping("w")
    assert view.get_cropping() == "w" and view.get_selected() == "w" and view.get_drag_mode() == "none"
    view.set_cropping(None)
    assert view.get_cropping() is None
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


def fit_group():
    """Tiles that all fit, side by side under the main tile, are as tall as the taller one, up to the cap."""
    from luma_appkit import Dock, DockTile, Island
    from luma_appkit.lumaui_tokens import DOCK

    short, tall = Gtk.Box(height_request=60), Gtk.Box(height_request=140)
    preview = DockTile("preview", "Preview", "monitor", Gtk.Box(), main=True, bare=True)
    widgets = DockTile("widgets", "Widgets", "layers", short, fit=True)
    audio = DockTile("audio", "Audio", "volume-2", tall, fit=True, phone="drawer")
    d = Dock([preview, widgets, audio],
             layout=Dock.split("v", 0.5, "preview", Dock.split("h", 0.5, "widgets", "audio")))
    assert d.fit_max == DOCK["fit_max_pct"] / 100
    island = Island()
    island.append(d)
    window = Gtk.Window(default_width=1000, default_height=700)
    window.set_child(island)
    window.present()
    settle()
    assert island.has_css_class("lumaui-docked"), "an island holding a dock is concentric with its tiles"
    natural = audio.measure(Gtk.Orientation.VERTICAL, audio.get_width())[1]
    assert abs(audio.get_height() - natural) <= 1 and audio.get_height() == widgets.get_height(), \
        (audio.get_height(), widgets.get_height(), natural)
    # Past the cap the content scrolls in its tile; the main tile keeps the rest.
    tall.set_size_request(-1, 900)
    d.refit()
    settle()
    cap = int(d.fit_max * d.get_height())
    assert abs(audio.get_height() - cap) <= 1, (audio.get_height(), cap)
    assert audio.scroller is not None and audio.scroller.get_vadjustment().get_upper() > audio.scroller.get_height()
    assert preview.get_height() >= d.get_height() - cap - 3 * DOCK["gap"], preview.get_height()
    # The seam moves: a drag sizes the fitted side anywhere above its minimum, kept through a refit
    # and in the layout; a double click on the seam fits it to its content again.
    outer = next(p for p, node in d._panes if node["split"] == "v")
    length = outer.get_height() - DOCK["gap"]
    outer.held = True  # as while a person drags it
    outer.set_position(length - 200)
    settle()
    assert abs(audio.get_height() - 200) <= 2, audio.get_height()
    d.refit()
    settle()
    assert abs(audio.get_height() - 200) <= 2, ("a refit undid the drag", audio.get_height())
    assert d.layout()["size"] == 200, d.layout()
    outer.set_position(length - 5)  # past the tile's minimum: the paned stops it there
    settle()
    outer.held = False
    assert audio.get_height() >= DOCK["min_size"] - 1, audio.get_height()
    d._seam_pressed(Gtk.GestureClick(), 2, 1, outer.get_start_child().get_height() + 2, outer,
                    next(node for p, node in d._panes if p is outer))
    settle()
    assert "size" not in d.layout() and abs(audio.get_height() - cap) <= 1, audio.get_height()
    seam = d._separator(outer)
    assert seam.get_cursor() is not None and seam.get_cursor().get_name() == "row-resize", seam.get_cursor()
    assert not outer.has_css_class("fixed")
    # On a phone a fit tile stays inline unless it asks for a drawer.
    window.set_default_size(400, 700)
    settle()
    assert d.phone and widgets.has_css_class("inline") and not audio.get_mapped()
    assert not island.has_css_class("lumaui-docked")
    window.destroy()
    # A seam with no room (both sides at their minimum) shows no mark and no resize cursor.
    pair = Dock([DockTile("a", "A", "layers", Gtk.Box(), main=True), DockTile("b", "B", "layers", Gtk.Box())],
                layout=Dock.split("v", 0.5, "a", "b"))
    window = Gtk.Window(default_width=900, default_height=1)
    window.set_child(pair)
    window.present()
    settle()
    seam = pair._panes[0][0]
    assert seam.has_css_class("fixed") and pair._separator(seam).get_cursor() is None, \
        (seam.get_property("min-position"), seam.get_property("max-position"))
    window.set_default_size(900, 700)
    settle()
    assert not seam.has_css_class("fixed") and pair._separator(seam).get_cursor().get_name() == "row-resize"
    window.destroy()
    for bad in (lambda: DockTile("x", "X", "layers", Gtk.Box(), phone="inline"),
                lambda: DockTile("x", "X", "layers", Gtk.Box(), fit=True, phone="sheet"),
                lambda: Dock([DockTile("x", "X", "layers", Gtk.Box())], fit_max=0)):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("refused")


def sidebar_headings():
    """A heading's words start on the rows' lead in every sidebar variant."""
    from luma_appkit import NavigationSidebar, RowLead, SidebarRow
    from luma_appkit.rows_navigation import SIDEBAR_VARIANTS

    leads = {"people": lambda: RowLead.face("Priya Raman"), "resources": lambda: RowLead.well("cpu"),
             "files": lambda: RowLead.thumbnail(None, kind="image")}
    for variant in SIDEBAR_VARIANTS:
        window = Gtk.Window(default_width=900, default_height=600)
        sidebar = NavigationSidebar(variant=variant)
        sidebar.append_section("Scenes")
        sidebar.append_row(SidebarRow("Main", lead=leads.get(variant, lambda: RowLead.icon("inbox"))(), subtitle="2"))
        window.set_child(sidebar)
        window.present()
        settle()
        heading, lead = _first(sidebar, "luma-navigation-heading"), _first(sidebar, "lumaui-row-lead")
        hx = heading.compute_bounds(sidebar)[1].get_x()
        lx = lead.compute_bounds(sidebar)[1].get_x()
        assert abs(hx - lx) <= 1, (variant, hx, lx)
        window.destroy()


def _first(widget, css):
    if widget.has_css_class(css):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _first(child, css)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def dock_equal_split():
    """A split's ratio is a share of the two tiles' own length: 0.5 is two equal tiles."""
    from luma_appkit import Dock, DockTile

    a = DockTile("a", "A", "layers", Gtk.Box(), main=True)
    b = DockTile("b", "B", "layers", Gtk.Box())
    d = Dock([a, b], layout=Dock.split("h", 0.5, "a", "b"))
    window = Gtk.Window(default_width=900, default_height=600)
    window.set_child(d)
    window.present()
    settle()
    assert abs(a.get_width() - b.get_width()) <= 1, (a.get_width(), b.get_width())
    assert abs(d.layout()["ratio"] - 0.5) < 0.01, d.layout()
    # A person's drag sets the share; a relayout or a resize afterwards keeps it.
    paned = d._panes[0][0]
    paned.held = True
    paned.set_position(300)
    settle()
    paned.held = False
    assert abs(a.get_width() - 300) <= 1, a.get_width()
    ratio = d.layout()["ratio"]
    d.queue_resize()
    settle()
    assert abs(a.get_width() - 300) <= 1 and d.layout()["ratio"] == ratio, (a.get_width(), d.layout())
    window.set_default_size(1200, 600)
    settle()
    share = a.get_width() / (a.get_width() + b.get_width())
    assert abs(share - ratio) < 0.01 and d.layout()["ratio"] == ratio, (share, ratio, d.layout())
    window.destroy()


def panel_checked_states():
    """Switch rows and choice chips state CHECKED as a tristate (a bool is a GLib critical, fatal here)."""
    from luma_appkit import PanelChoices, PanelSwitch

    toggled = []
    row = PanelSwitch("mic", "Reduce background noise", active=True, on_toggle=toggled.append)
    row.emit("clicked")
    assert toggled == [False]
    choices = PanelChoices([("f", "°F"), ("c", "°C")], selected="c")
    choices.set_selected("f")
    assert choices.selected == "f"


def dock_header_drag():
    """A header drag shows the drop target as it moves and moves the tile on release (the drag's
    content can't be read back in PyGObject, so the dock follows its own moving tile)."""
    from gi.repository import Gdk
    from luma_appkit import Dock, DockTile

    a = DockTile("a", "A", "layers", Gtk.Box(), main=True)
    b = DockTile("b", "B", "layers", Gtk.Box())
    d = Dock([a, b], layout=Dock.split("h", 0.5, "a", "b"))
    window = Gtk.Window(default_width=900, default_height=600)
    window.set_child(d)
    window.present()
    settle()
    source = next(c for c in [b.header.observe_controllers().get_item(i)
                              for i in range(b.header.observe_controllers().get_n_items())]
                  if isinstance(c, Gtk.DragSource))
    provider = source.emit("prepare", 4, 4)
    assert isinstance(provider, Gdk.ContentProvider) and "gchararray" in provider.ref_formats().to_string()
    target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
    ok, box = a.compute_bounds(d)
    assert ok
    x, y = box.get_x() + 6, box.get_y() + box.get_height() / 2  # a's left band
    assert d._drop_motion(target, x, y) == 0, "no drag under way: no target"
    b.add_css_class("moving")  # what the header's drag-begin does
    assert d._drop_motion(target, x, y) == Gdk.DragAction.MOVE and d.target.get_visible()
    assert d._dropped(target, f"lumaui-dock-tile:b", x, y)
    b.remove_css_class("moving")
    layout = d.layout()
    assert layout["start"] == "b" and layout["end"] == "a", layout
    window.destroy()


def meter_track():
    """A silent meter's empty track reads against the well it sits on."""
    from luma_appkit import VolumeSlider

    slider = VolumeSlider(-60, 6, value=-60, meter=2, horizontal=True)
    window = Gtk.Window(default_width=300, default_height=120)
    window.set_child(slider)
    window.present()
    settle()
    track = _first(slider.get_meter(), "lumaui-meter-track")
    found, well = slider.get_style_context().lookup_color("luma_well")
    colour = track.get_color()
    assert colour.alpha >= 0.12, colour.to_string()
    if found:
        assert colour.to_string() != well.to_string(), "the track is not the well's own colour"
    window.destroy()


if __name__ == "__main__":
    docks()
    arrange()
    picker()
    meter()
    live_chip()
    dock()
    fitted_dock()
    fit_group()
    sidebar_headings()
    meter_track()
    dock_header_drag()
    dock_equal_split()
    panel_checked_states()
    GLib.MainContext.default().iteration(False)
    print("ok")
