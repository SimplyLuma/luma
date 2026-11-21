#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The Python builders for docks, ArrangeView, AudioMeter and the live chip's updates."""
import time
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

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


def arrange_group():
    """Several boxes from Python: selection, group signals as dicts, align and distribute."""
    from luma_appkit import ArrangeItem, ArrangeView, align_rects, distribute_rects

    chosen, groups = [], []
    view = ArrangeView(items=[ArrangeItem("a", "A", 0, 0, 100, 50), ArrangeItem("b", "B", 300, 100, 50, 50)],
                       on_selection=chosen.append, on_group_commit=groups.append)
    view.set_selection(["a", "b"])
    assert view.get_selection() == ["a", "b"] and view.get_selected() == "b"
    view.select_all()
    assert chosen == [] or chosen[-1] == ["a", "b"]
    rects = {"a": (0, 0, 100, 50), "b": (300, 100, 50, 50), "c": (100, 300, 20, 20)}
    assert align_rects(rects, "left")["b"] == (0, 100, 50, 50)
    assert align_rects(rects, "right")["a"] == (250, 0, 100, 50)
    assert align_rects(rects, "middle")["c"][1] == (0 + 320) / 2 - 10
    assert align_rects({"a": (10, 10, 100, 50)}, "centre", canvas=(1920, 1080))["a"] == (910, 10, 100, 50)
    spread = distribute_rects(rects, "h")
    assert spread["a"][0] == 0 and spread["b"][0] == 300 and abs(spread["c"][0] - 190) < 1e-9, spread
    for bad in (lambda: align_rects(rects, "diagonal"), lambda: align_rects({"a": (0, 0, 1, 1)}, "left")):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("refused")


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


def docking_trees():
    """Docking as plain data: every zone, the dock's own edges, tabs and emptied places."""
    from luma_appkit.structure_dock import tree_dock, tree_dock_edge, tree_keys, tree_remove

    t = {"split": "h", "ratio": 0.6, "start": "a", "end": {"split": "v", "ratio": 0.5, "start": "b", "end": "c"}}
    side = lambda zone: tree_dock(t, "c", "a", zone)["start"]  # c leaves b's split; a's place splits
    assert tree_dock(t, "c", "a", "left")["end"] == "b"
    assert side("left") == {"split": "h", "ratio": 0.5, "start": "c", "end": "a"}
    assert side("right") == {"split": "h", "ratio": 0.5, "start": "a", "end": "c"}
    assert side("top") == {"split": "v", "ratio": 0.5, "start": "c", "end": "a"}
    assert side("bottom") == {"split": "v", "ratio": 0.5, "start": "a", "end": "c"}
    assert tree_dock(t, "c", "a", "left")["ratio"] == 0.6, "the other shares stay"
    tabbed = tree_dock(t, "c", "a", "tabs")
    assert tabbed == {"split": "h", "ratio": 0.6, "start": {"tabs": ["a", "c"], "current": "c"}, "end": "b"}
    assert tree_keys(tabbed) == ["a", "c", "b"]
    # Onto a tab: the group takes it; onto a group's edge: the group splits.
    three = tree_dock(tabbed, "b", "c", "tabs")
    assert three == {"tabs": ["a", "c", "b"], "current": "b"}
    split_group = tree_dock(three, "b", "a", "right")
    assert split_group == {"split": "h", "ratio": 0.5, "start": {"tabs": ["a", "c"], "current": "a"}, "end": "b"}
    # Out of a group of two: the group becomes the one left, and an emptied split goes.
    out = tree_dock(tabbed, "c", "b", "bottom")
    assert out == {"split": "h", "ratio": 0.6, "start": "a", "end": {"split": "v", "ratio": 0.5,
                                                                      "start": "b", "end": "c"}}
    assert tree_remove(t, "b") == {"split": "h", "ratio": 0.6, "start": "a", "end": "c"}
    assert tree_remove(tree_remove(t, "b"), "c") == "a" and tree_remove("a", "a") is None
    assert tree_remove({"tabs": ["a", "b", "c"], "current": "b"}, "b") == {"tabs": ["a", "c"], "current": "a"}
    # The dock's own edges: a full-height column or full-width row, a quarter of the dock.
    assert tree_dock_edge(t, "a", "right") == {"split": "h", "ratio": 0.75,
                                               "start": {"split": "v", "ratio": 0.5, "start": "b", "end": "c"},
                                               "end": "a"}
    assert tree_dock_edge(t, "c", "top")["start"] == "c" and tree_dock_edge(t, "c", "top")["ratio"] == 0.25
    assert tree_dock(t, "a", "a", "left") is t
    for bad in (lambda: tree_dock(t, "c", "a", "middle"), lambda: tree_dock_edge(t, "a", "centre")):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("refused")


def _drag(dock, widget, to, *, release=True):
    """A person's drag of `widget` (a header or tab) to `to` (dock coordinates)."""
    from gi.repository import Graphene

    gesture = next(c for c in [widget.observe_controllers().get_item(i)
                               for i in range(widget.observe_controllers().get_n_items())]
                   if isinstance(c, Gtk.GestureDrag) and not isinstance(c, Gtk.GestureLongPress))
    ok, start = widget.compute_point(dock, Graphene.Point().init(12, 10))
    assert ok
    gesture.emit("drag-begin", 12, 10)
    gesture.emit("drag-update", (to[0] - start.x) / 2, (to[1] - start.y) / 2)
    gesture.emit("drag-update", to[0] - start.x, to[1] - start.y)
    if release:
        gesture.emit("drag-end", to[0] - start.x, to[1] - start.y)
    return gesture


def docking_drag():
    """Headers dock tiles by dragging: edge bands split, the middle makes tabs, the dock's edge a column;
    a preview shows where; Esc cancels; on_rearrange gets the previous layout."""
    from luma_appkit import Dock, DockTile

    tiles = {k: DockTile(k, k.upper(), "layers", Gtk.Box()) for k in ("a", "b", "c")}
    d = Dock(tiles.values(), layout={"split": "h", "ratio": 0.5, "start": "a",
                                     "end": {"split": "v", "ratio": 0.5, "start": "b", "end": "c"}})
    undo = []
    d.on_rearrange = lambda previous, new: undo.append((previous, new))
    window = Gtk.Window(default_width=1000, default_height=640)
    window.set_child(d)
    window.present()
    settle()

    def box(widget):
        ok, r = widget.compute_bounds(d)
        assert ok
        return r.get_x(), r.get_y(), r.get_width(), r.get_height()

    # c over a's right band: the preview is a's right half; release splits a.
    ax, ay, aw, ah = box(tiles["a"])
    g = _drag(d, tiles["c"].header, (ax + aw * 0.9, ay + ah / 2), release=False)
    assert d.target.get_visible() and tiles["c"].has_css_class("moving")
    settle(0.2)
    r = d._target_rect
    assert abs(r.x - (ax + aw / 2)) <= 2 and abs(r.width - aw / 2) <= 2 and abs(r.height - ah) <= 2, (r.x, r.width)
    assert d.layout()["end"]["end"] == "c", "nothing moves until release"
    g.emit("drag-end", 0, 0)
    assert not d.target.get_visible() and not tiles["c"].has_css_class("moving")
    assert d.layout() == {"split": "h", "ratio": 0.5, "start": {"split": "h", "ratio": 0.5, "start": "a", "end": "c"},
                          "end": "b"}, d.layout()
    assert len(undo) == 1 and undo[0][0]["end"]["end"] == "c"
    settle()
    # b onto the middle of a: tabs, b showing; the tab strip switches.
    ax, ay, aw, ah = box(tiles["a"])
    _drag(d, tiles["b"].header, (ax + aw / 2, ay + ah / 2))
    settle()
    assert d.layout() == {"split": "h", "ratio": 0.5, "start": {"tabs": ["a", "b"], "current": "b"}, "end": "c"}, \
        d.layout()
    widget = next(w for slot, w in d._slots if isinstance(slot, dict))
    assert widget.has_css_class("lumaui-dock-tabs") and tiles["b"].get_mapped() and not tiles["a"].get_mapped()
    assert tiles["b"].more_button.get_parent() is widget.tools
    widget.buttons["a"].emit("clicked")
    settle()
    assert d.layout()["start"]["current"] == "a" and tiles["a"].get_mapped()
    assert tiles["b"].more_button.get_parent() is tiles["b"].header
    # A tab dragged to the dock's right edge: a full-height column, a quarter of the dock.
    root = d._root_widget
    rx, ry, rw, rh = box(root)
    g = _drag(d, widget.buttons["b"], (rx + rw - 6, ry + rh / 2), release=False)
    settle(0.2)
    r = d._target_rect
    assert abs(r.width - rw * 0.25) <= 2 and abs(r.height - rh) <= 2 and abs(r.x + r.width - (rx + rw)) <= 2
    g.emit("drag-end", 0, 0)
    settle()
    assert d.layout()["end"] == "b" and d.layout()["ratio"] == 0.75, d.layout()
    assert "tabs" not in str(d.layout()), "a group left with one tile is that tile"
    # Esc cancels: nothing moves, the preview goes.
    before = d.layout()
    ax, ay, aw, ah = box(tiles["a"])
    g = _drag(d, tiles["c"].header, (ax + aw / 2, ay + ah / 2), release=False)
    assert d.target.get_visible()
    keys = next(c for c in [window.observe_controllers().get_item(i)
                            for i in range(window.observe_controllers().get_n_items())]
                if isinstance(c, Gtk.EventControllerKey) and c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE)
    assert keys.emit("key-pressed", Gdk.KEY_Escape, 0, 0)
    g.emit("drag-end", 0, 0)
    assert d.layout() == before and not d.target.get_visible() and not tiles["c"].has_css_class("moving")
    # A click on a header (no real move) is not a drag.
    from gi.repository import Graphene
    ok, here = tiles["c"].header.compute_point(d, Graphene.Point().init(12, 10))
    g = _drag(d, tiles["c"].header, (here.x + 3, here.y + 2))
    assert d.layout() == before and not d.target.get_visible(), (before, d.layout())
    # Tabs survive set_layout; a bare tile never joins a group.
    d.set_layout({"tabs": ["a", "b", "zz"], "current": "zz"})
    assert d.layout()["start"] == {"tabs": ["a", "b"], "current": "a"} or d.layout() == {"split": "h", "ratio": 0.75,
                                                                                        "start": {"tabs": ["a", "b"], "current": "a"},
                                                                                        "end": "c"}, d.layout()
    # Tiles that fit their content stack as tabs too: the group fits like its current tile.
    fit = Dock([DockTile("p", "P", "monitor", Gtk.Box(), main=True, bare=True),
                DockTile("m", "M", "volume-2", Gtk.Box(height_request=120), fit=True),
                DockTile("w", "W", "layers", Gtk.Box(height_request=80), fit=True)],
               layout={"split": "v", "ratio": 0.6, "start": "p", "end": {"tabs": ["m", "w"], "current": "m"}})
    other = Gtk.Window(default_width=900, default_height=600)
    other.set_child(fit)
    other.present()
    settle()
    assert fit._fits(fit.layout()["end"]) and fit.layout()["end"]["current"] == "m"
    other.destroy()
    # A phone: no docking.
    window.set_default_size(400, 700)
    settle()
    assert d.phone
    before = d.layout()
    _drag(d, tiles["a"].header, (20, 200))
    assert d.layout() == before and not d.target.get_visible()
    window.destroy()


def dock_close_open():
    """Closing a tile takes it out (its place remembered in the layout data); opening brings it back."""
    from luma_appkit import Dock, DockTile

    tiles = {k: DockTile(k, k.upper(), "layers", Gtk.Box(), closable=True, main=k in ("land", "vert"))
             for k in ("land", "vert", "widgets", "audio")}
    d = Dock(tiles.values(), layout={"split": "h", "ratio": 0.7,
                                     "start": {"split": "h", "ratio": 0.6, "start": "land", "end": "vert"},
                                     "end": {"split": "v", "ratio": 0.5, "start": "widgets", "end": "audio"}})
    undo, asked = [], []
    d.on_rearrange = lambda previous, new: undo.append((previous, new))
    window = Gtk.Window(default_width=1200, default_height=700)
    window.set_child(d)
    window.present()
    settle()
    assert d.main_key == "land" and d.is_open("vert")
    d.close_tile("vert")
    settle()
    layout = d.layout()
    assert not d.is_open("vert") and tiles["vert"].get_parent() is None
    record = layout["closed"]["vert"]
    assert {k: record[k] for k in ("split", "side", "sibling", "ratio")} == \
        {"split": "h", "side": "end", "sibling": ["land"], "ratio": 0.4}, layout
    assert layout["layout"]["start"] == "land" and len(undo) == 1
    # The closed set round-trips: set_layout keeps it closed (it doesn't join at the end).
    d.set_layout(layout)
    assert not d.is_open("vert") and d.layout() == layout
    # Reopened at its last place, with its share and its geometry.
    width = tiles["vert"].get_width()
    d.open_tile("vert")
    settle()
    assert d.layout()["start"] == {"split": "h", "ratio": 0.6, "start": "land", "end": "vert"}, d.layout()
    assert "closed" not in str(d.layout()) and len(undo) == 2
    # A tile split from a group of tiles goes back beside the whole group, not one of them.
    d.close_tile("audio")
    d.open_tile("audio")
    settle()
    assert d.layout()["end"] == {"split": "v", "ratio": 0.5, "start": "widgets", "end": "audio"}, d.layout()
    d.set_layout({"split": "h", "ratio": 0.6, "start": "land",
                  "end": {"split": "v", "ratio": 0.5, "start": {"split": "h", "ratio": 0.5, "start": "vert",
                                                                "end": "widgets"}, "end": "audio"}})
    settle()
    d.close_tile("audio")
    settle()
    d.open_tile("audio")
    settle()
    assert d.layout()["end"]["end"] == "audio" and d.layout()["end"]["start"]["start"] == "vert", d.layout()
    assert abs(d.layout()["end"]["ratio"] - 0.5) < 0.01, d.layout()
    # Closed, the window resized, reopened: the share holds, not the pixels.
    before = tiles["audio"].get_height() / d.get_height()
    d.close_tile("audio")
    window.set_default_size(1200, 900)
    settle()
    d.open_tile("audio")
    settle()
    assert abs(tiles["audio"].get_height() / d.get_height() - before) < 0.03, (before, tiles["audio"].get_height())
    # Closed, the others rearranged, reopened: beside the smallest part still holding its old neighbours,
    # at its old size.
    tall = tiles["audio"].get_height()
    d.close_tile("audio")
    d.move_tile("widgets", "land", "left")
    settle()
    d.open_tile("audio")
    settle()
    assert d.is_open("audio") and abs(tiles["audio"].get_height() - tall) < 30, (tall, tiles["audio"].get_height(),
                                                                                d.layout())
    # In a tab group it goes back into the group at its index.
    d.move_tile("audio", "vert", "tabs")
    d.move_tile("widgets", "vert", "tabs")
    settle()
    group = next(slot for slot, _w in d._slots if isinstance(slot, dict))["tabs"]
    d.close_tile("audio")
    d.open_tile("audio")
    settle()
    assert next(slot for slot, _w in d._slots if isinstance(slot, dict))["tabs"] == group
    window.set_default_size(1200, 700)
    d.set_layout({"split": "h", "ratio": 0.7, "start": {"split": "h", "ratio": 0.6, "start": "land", "end": "vert"},
                  "end": {"split": "v", "ratio": 0.5, "start": "widgets", "end": "audio"}})
    settle()
    # The main tile may close while another main stays: the other leads.
    d.close_tile("land")
    assert d.main_key == "vert"
    settle()
    # A suggested place doesn't override a remembered one (a canvas toggle passes one every time).
    d.open_tile("land", beside="vert", edge="right", size=90)
    settle()
    assert d.layout()["start"] == {"split": "h", "ratio": 0.6, "start": "land", "end": "vert"}, d.layout()
    d.close_tile("land")
    settle()
    # With no remembered place, it opens where suggested, that many px of the target's place.
    d.set_layout({"layout": d.layout()["layout"], "closed": {"land": {}}})
    settle()
    width = tiles["vert"].get_width()
    d.open_tile("land", beside="vert", edge="left", size=200)
    settle()
    split = d.layout()["start"]
    assert split["start"] == "land" and abs(split["ratio"] - 200 / width) < 0.02, (split, width)
    # "Close panel": on_close may refuse; the last open tile never closes.
    d.on_close = lambda key: (asked.append(key), key != "audio")[1]
    assert not d.request_close("audio") and d.is_open("audio") and asked == ["audio"]
    assert d.request_close("widgets") and not d.is_open("widgets")
    for key in ("land", "vert"):
        d.close_tile(key)
    assert d.is_open("audio") and not d.can_close("audio")
    d.close_tile("audio")
    assert d.is_open("audio"), "the last tile stays"
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
    arrange_group()
    meter()
    live_chip()
    dock()
    fitted_dock()
    fit_group()
    sidebar_headings()
    meter_track()
    dock_close_open()
    docking_trees()
    docking_drag()
    dock_equal_split()
    panel_checked_states()
    GLib.MainContext.default().iteration(False)
    print("ok")
