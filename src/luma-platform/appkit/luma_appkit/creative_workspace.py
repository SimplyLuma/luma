# SPDX-License-Identifier: Apache-2.0
"""Python builders for the native CR1 workspace and floating panel."""
from __future__ import annotations

from typing import Callable, Iterable


def _native():
    import gi

    gi.require_version("LumaUI", "1")
    from gi.repository import LumaUI

    return LumaUI


def CreativeWorkspace(content=None, *, left=None, right=None, tools=None, zoom=None,
                      focused: bool = False, grid: bool = True,
                      camera: tuple[float, float, float] | None = None):
    """Compose app content with native panels, tool slot, and zoom corner."""
    workspace = _native().CreativeWorkspace.new(content)
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
    return workspace


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
