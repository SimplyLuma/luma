# SPDX-License-Identifier: Apache-2.0
"""Python builders for the native CR1 workspace and floating panel.

Docks: ``CreativeWorkspace(panels=[("widgets", panel, "left"), ...])`` docks
keyed, movable panels on the left, the right or the bottom. A person drags a
panel's header to another dock, or uses its More menu. ``workspace_layout()``
and ``apply_layout()`` turn the arrangement into a plain list of
``(key, side, folded)`` and back, so an app can keep named layouts (presets)
in its settings; ``on_layout`` says when a person changed it.
"""
from __future__ import annotations

from typing import Callable, Iterable


def _native():
    import gi

    gi.require_version("LumaUI", "1")
    from gi.repository import LumaUI

    return LumaUI


PANEL_SIDES = ("left", "right", "bottom")


def _side(name: str):
    L = _native()
    if name not in PANEL_SIDES:
        raise ValueError(f"a panel docks {PANEL_SIDES}, not {name!r}")
    return {"left": L.PanelSide.LEFT, "right": L.PanelSide.RIGHT, "bottom": L.PanelSide.BOTTOM}[name]


def CreativeWorkspace(content=None, *, left=None, right=None, tools=None, zoom=None,
                      focused: bool = False, grid: bool = True,
                      camera: tuple[float, float, float] | None = None,
                      panels: Iterable[tuple] = (), layout: Iterable[tuple] | None = None,
                      on_layout: Callable[[list[tuple[str, str, bool]]], None] | None = None,
                      on_work_area: Callable[[int, int, int, int], None] | None = None):
    """Compose app content with native panels, tool slot, and zoom corner.

    ``panels`` are ``(key, FloatingPanel, side)`` docked in order; ``layout``
    restores a saved arrangement; ``on_layout(layout)`` follows a person's moves;
    ``on_work_area(x, y, width, height)`` follows the part no open panel covers.
    """
    workspace = _native().CreativeWorkspace.new(content)
    keys = set()
    for key, panel, side in panels:
        if key in keys:
            raise ValueError(f"panel keys are unique: {key!r}")
        keys.add(key)
        workspace.add_panel(key, panel, _side(side))
    if left is not None:
        workspace.set_left(left)
    if right is not None:
        workspace.set_right(right)
    if tools is not None:
        workspace.set_tools(tools)
    if zoom is not None:
        workspace.set_zoom(zoom)
    workspace.set_grid_visible(grid)
    if camera is not None:
        workspace.set_camera(*camera)
    workspace.set_focused(focused)
    if layout is not None:
        apply_layout(workspace, layout)
    if on_layout is not None:
        workspace.connect("layout-changed", lambda current: on_layout(workspace_layout(current)))
    if on_work_area is not None:
        workspace.connect("work-area-changed", lambda _w, x, y, width, height: on_work_area(x, y, width, height))
    return workspace


def workspace_layout(workspace) -> list[tuple[str, str, bool]]:
    """The docked panels as ``[(key, "left" | "right" | "bottom", folded), ...]`` in dock order."""
    return [tuple(entry) for entry in workspace.get_layout().unpack()]


def apply_layout(workspace, layout: Iterable[tuple]) -> None:
    """Restore a layout from ``workspace_layout()``; unknown keys and sides are skipped."""
    from gi.repository import GLib

    entries = [(str(key), str(side), bool(folded)) for key, side, folded in layout if side in PANEL_SIDES]
    workspace.set_layout(GLib.Variant("a(ssb)", entries))


def FloatingPanel(title: str, icon: str, *, pages: Iterable[tuple] = (), child=None,
                  summary: str | None = None, title_menu=None, editable: bool = False,
                  more=None, closable: bool = True, folded: bool = False, header_actions=None,
                  on_page: Callable[[str], None] | None = None,
                  on_rename: Callable[[str], None] | None = None,
                  on_folded: Callable[[bool], None] | None = None):
    """A native floating panel with optional pages or one app-owned body."""
    pages = tuple(pages)
    if child is not None and pages:
        raise ValueError("a panel has pages or one child, not both")
    panel = _native().FloatingPanel.new(title, icon)
    for key, label, page in pages:
        panel.add_page(key, label, page)
    if child is not None:
        panel.set_child(child)
    panel.set_summary(summary)
    if header_actions is not None:
        panel.set_header_actions(header_actions)
    if title_menu is not None:
        panel.set_title_menu(title_menu)
    panel.set_title_editable(editable)
    if more is not None:
        panel.set_more_menu(more)
    panel.set_closable(closable)
    panel.set_folded(folded)
    if on_page is not None:
        panel.connect("page-changed", lambda _panel, key: on_page(key))
    if on_rename is not None:
        panel.connect("renamed", lambda _panel, name: on_rename(name))
    if on_folded is not None:
        panel.connect("notify::folded", lambda current, _spec: on_folded(current.get_folded()))
    return panel
