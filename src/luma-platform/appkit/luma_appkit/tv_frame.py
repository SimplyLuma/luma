# SPDX-License-Identifier: Apache-2.0
"""LumaUI on TV: the floating island for every luma_appkit window.

A desktop LumaUI app needs no TV-specific code (docs/developer-platform/tv-mode.md). In TV
posture an `AppWindow` hands its frame to the C kit's TV frame (`LumaUI.window_set_tv_frame`):

- a window showing a `NavigationSidebar` beside its content `Island` (in an `IslandSplitView`,
  or side by side in one box) gets the floating island: the sidebar lifts off the window edge
  beside the page, and the remote moves between them;
- a window showing one `Island` and no sidebar (a welcome, an error) is drawn centred in the
  safe area, with no island.

The sidebar and island are moved into one TV frame box while the posture lasts and put back
exactly where they were when it ends. A window whose TV posture shows its own TV presentation
(a `TvStage`) is left alone. The sidebar follows the frame's classes (`lumaui-tv-island`,
`lumaui-tv-rail`): the island carries destinations only (lead and title; the rail leads alone).
"""

from __future__ import annotations

import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("LumaUI", "1")
from gi.repository import Adw, GLib, Gtk, LumaUI  # noqa: E402

__all__ = ["schedule", "sidebar_classes_changed", "supported"]


def supported() -> bool:
    """Whether the installed C kit has the public TV frame (API level 9)."""
    return hasattr(LumaUI, "window_set_tv_frame")


def schedule(widget: Gtk.Widget) -> None:
    """Look at the window `widget` is in again, once this main-loop turn is done."""
    window = widget if isinstance(widget, Gtk.Window) else widget.get_root()
    if not isinstance(window, Adw.ApplicationWindow) or not hasattr(window, "tv_posture"):
        return
    if getattr(window, "_tv_frame_idle", 0):
        return
    window._tv_frame_idle = GLib.idle_add(_sync, weakref.ref(window))


# ── what the window shows ──────────────────────────────────────────────────

def _walk(widget: Gtk.Widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _shown(widget: Gtk.Widget) -> bool:
    return widget.get_mapped() and widget.get_child_visible()


def _present(widget: Gtk.Widget | None, window: Gtk.Window) -> bool:
    """Still shown by `window` (rooted there, every ancestor visible), mapped yet or not."""
    if widget is None or widget.get_root() is not window:
        return False
    while widget is not None and widget is not window:
        if not widget.get_visible():
            return False
        widget = widget.get_parent()
    return True


def _find(root: Gtk.Widget, cls: type | None = None, css: str | None = None) -> Gtk.Widget | None:
    for widget in _walk(root):
        if (cls is None or isinstance(widget, cls)) and (css is None or widget.has_css_class(css)) and _shown(widget):
            return widget
    return None


def _ancestor(widget: Gtk.Widget, types: tuple[type, ...]) -> Gtk.Widget | None:
    parent = widget.get_parent()
    while parent is not None and not isinstance(parent, types):
        parent = parent.get_parent()
    return parent


# ── moving widgets and putting them back ───────────────────────────────────

def _take(widget: Gtk.Widget):
    """Take `widget` out of its parent; returns the function that puts it back."""
    parent = widget.get_parent()
    if isinstance(parent, Gtk.Box):
        before = widget.get_prev_sibling()
        parent.remove(widget)
        return lambda: parent.insert_child_after(widget, before) if widget.get_parent() is None else None
    if isinstance(parent, Adw.ToolbarView):
        parent.set_content(None)
        return lambda: parent.set_content(widget) if widget.get_parent() is None else None
    if isinstance(parent, Gtk.Overlay) and parent.get_child() is widget:
        parent.set_child(None)
        return lambda: parent.set_child(widget) if widget.get_parent() is None else None
    if hasattr(parent, "set_child") and hasattr(parent, "get_child") and parent.get_child() is widget:
        parent.set_child(None)
        return lambda: parent.set_child(widget) if widget.get_parent() is None else None
    return None


def _put_like(old: Gtk.Widget, new: Gtk.Widget):
    """Put `new` where `old` is (taking `old` out); returns the function that undoes it."""
    parent = old.get_parent()
    if isinstance(parent, Gtk.Box):
        before = old.get_prev_sibling()
        parent.remove(old)
        parent.insert_child_after(new, before)
    elif isinstance(parent, Adw.ToolbarView):
        parent.set_content(new)
    elif hasattr(parent, "set_child") and hasattr(parent, "get_child") and parent.get_child() is old:
        parent.set_child(new)
    else:
        return None

    def undo() -> None:
        if new.get_parent() is not parent:
            return  # the app has shown something else here since: nothing to put back
        if isinstance(parent, Gtk.Box):
            before = new.get_prev_sibling()
            parent.remove(new)
            parent.insert_child_after(old, before)
        elif isinstance(parent, Adw.ToolbarView):
            parent.set_content(old)
        else:
            parent.set_child(old)
    return undo


class _Frame:
    """One TV frame: what was moved, and how to put it back."""

    def __init__(self, sidebar: Gtk.Widget | None, island: Gtk.Widget, frame: Gtk.Widget, undo: list) -> None:
        # Held strongly (a PyGObject wrapper's weak reference dies with the wrapper, not the
        # widget); the window drops the frame as soon as the app shows something else.
        self._sidebar = sidebar
        self._island = island
        self.frame = frame
        self.undo = undo

    def sidebar(self) -> Gtk.Widget | None:
        return self._sidebar

    def island(self) -> Gtk.Widget:
        return self._island

    def holds(self, sidebar: Gtk.Widget | None, island: Gtk.Widget) -> bool:
        return self._sidebar is sidebar and self._island is island

    def standing(self, window: Gtk.Window) -> bool:
        """The frame's widgets are still what the window shows (the app has not replaced them)."""
        return (self._sidebar is None or _present(self._sidebar, window)) and _present(self._island, window)

    def restore(self) -> None:
        for undo in reversed(self.undo):
            undo()
        self.undo = []
        self._sidebar = self._island = None


def _build(sidebar: Gtk.Widget | None, island: Gtk.Widget) -> _Frame | None:
    """The TV frame box for this sidebar and page, moving them into it when they are apart."""
    undo = []
    if not island.has_css_class("luma-island"):
        island.add_css_class("luma-island")
        undo.append(lambda: island.remove_css_class("luma-island"))
    if sidebar is None:
        return _Frame(None, island, island.get_parent(), undo)
    parent = sidebar.get_parent()
    if parent is island.get_parent() and isinstance(parent, Gtk.Box):
        return _Frame(sidebar, island, parent, undo)  # already side by side: the box is the frame
    split = _ancestor(sidebar, (Adw.NavigationSplitView, Adw.OverlaySplitView))
    if split is None or not island.is_ancestor(split):
        return None
    frame = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
    frame.add_css_class("lumaui-tv-frame-box")
    put_back_split = _put_like(split, frame)
    if put_back_split is None:
        return None
    put_back_sidebar, put_back_island = _take(sidebar), _take(island)
    if put_back_sidebar is None or put_back_island is None:
        for put_back in (put_back_sidebar, put_back_island, put_back_split):
            if put_back is not None:
                put_back()
        return None
    frame.append(sidebar)
    frame.append(island)
    island.set_hexpand(True)

    def apart() -> None:
        if sidebar.get_parent() is frame:
            frame.remove(sidebar)
        if island.get_parent() is frame:
            frame.remove(island)
        put_back_sidebar()
        put_back_island()
        put_back_split()
    undo.append(apart)
    return _Frame(sidebar, island, frame, undo)


def _content_island(sidebar: Gtk.Widget) -> Gtk.Widget | None:
    """The page beside a sidebar: its split view's content, or its Island sibling in a box."""
    parent = sidebar.get_parent()
    if isinstance(parent, Gtk.Box):
        sibling = parent.get_first_child()
        while sibling is not None:
            if sibling is not sidebar and sibling.has_css_class("luma-island") and sibling.get_visible():
                return sibling
            sibling = sibling.get_next_sibling()
    split = _ancestor(sidebar, (Adw.NavigationSplitView, Adw.OverlaySplitView))
    if isinstance(split, Adw.NavigationSplitView):
        page = split.get_content()
        return page.get_child() if page is not None else None
    if isinstance(split, Adw.OverlaySplitView):
        return split.get_content()
    return None


def _sync(ref) -> bool:
    window = ref()
    if window is None:
        return GLib.SOURCE_REMOVE
    window._tv_frame_idle = 0
    current: _Frame | None = getattr(window, "_tv_frame", None)
    body = getattr(window, "body", None)
    want = None
    if window.tv_posture and supported() and body is not None and _find(body, css="lumaui-tv-stage") is None:
        from .widgets import NavigationSidebar
        found = _find(body, NavigationSidebar)
        if current is not None and current.standing(window) and (current.sidebar() is not None or found is None):
            # Moving the widgets maps and unmaps them: what the frame holds is still the window's.
            return GLib.SOURCE_REMOVE
        if found is not None:
            island = _content_island(found)
            want = (found, island) if island is not None else None
        else:
            island = _find(body, css="luma-island")
            want = (None, island) if island is not None else None
    if current is not None and want is not None and current.holds(*want):
        return GLib.SOURCE_REMOVE
    if current is not None:
        LumaUI.window_set_tv_active(window, False)
        LumaUI.window_set_tv_frame(window, None, None, None, None)
        current.restore()
        window._tv_frame = None
    if want is not None:
        frame = _build(*want)
        if frame is not None and frame.frame is not None:
            window._tv_frame = frame
            LumaUI.window_set_tv_frame(window, window._toolbar_view, frame.frame, want[0], want[1])
            LumaUI.window_set_tv_active(window, True)
    return GLib.SOURCE_REMOVE


# ── the sidebar in the island ──────────────────────────────────────────────

def sidebar_classes_changed(sidebar: Gtk.Widget) -> None:
    """Follow the frame's classes: the island shows destinations only; the rail shows leads alone."""
    tv = sidebar.has_css_class("lumaui-tv-island")
    rail = tv and sidebar.has_css_class("lumaui-tv-rail")
    if (tv, rail) == getattr(sidebar, "_tv_state", (False, False)):
        return
    if tv and not getattr(sidebar, "_tv_state", (False, False))[0]:
        sidebar._tv_shown = {w: w.get_visible() for w in _sidebar_extras(sidebar)}
    for widget, shown in getattr(sidebar, "_tv_shown", {}).items():
        widget.set_visible(False if tv else shown)
    sidebar._tv_state = (tv, rail)
    row = sidebar.list.get_first_child()
    while row is not None:
        if row.has_css_class("luma-navigation-section"):
            line = row.get_child()
            heading = line.get_first_child() if line is not None else None
            if heading is not None:
                heading.set_visible(not tv)  # a section is a hairline on a TV
        elif hasattr(row, "set_tv_island"):
            row.set_tv_island(tv, rail)
        row = row.get_next_sibling()


def _sidebar_extras(sidebar: Gtk.Widget) -> list[Gtk.Widget]:
    extras = [sidebar.footer]
    for name in ("_header", "phone_title_label"):
        widget = getattr(sidebar, name, None)
        if widget is not None:
            extras.append(widget)
    return extras
