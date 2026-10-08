# SPDX-License-Identifier: Apache-2.0
"""LumaUI creative family (KB-D): the creative suite's parts, for Python.

The parts are implemented once, in C (LumaUI-1, ``src/luma-platform/ui/
luma-creative-*.c``), and reach Python through ``gi.repository.LumaUI``, so
Canvas (Python) and Grid, Stage and Write (C++) share one implementation, one
widget tree and one sheet (``luma-appkit-creative.css``). This module only
adds the kit's keyword style on top; every function returns the C object:

    CreativeWorkspace(content, left=, right=, tools=, zoom=, focused=)
    FloatingPanel(title, icon, pages=[(key, label, child)], summary=, ...)
    ToolBar([Tool(...), Flyout(...), TOOL_SEPARATOR], current=, on_tool=)
    LayerTree([Layer(...)], selected=, on_select=, on_visibility=, on_rename=, on_reorder=)
    PropertySection(title, *fields, action=(icon, label, "win.action"))
    PropertyNumber(label, value, unit=), PropertyColor(label, "1F6FE0"),
    PropertyChoice(label, [(key, label, icon)], segments=), AlignmentActions()

Everything the C parts say about behaviour (drawers on a phone, focus mode,
scrubbing numbers, the flyout) holds here too: see
docs/developer/kit/lumaui-c.md, "Creative suite".
"""
from __future__ import annotations

from typing import Callable, Iterable, NamedTuple, Sequence

__all__ = [
    "ToolBar",
    "Tool",
    "Flyout",
    "TOOL_SEPARATOR",
    "Layer",
    "LayerTree",
    "PropertySection",
    "PropertyNumber",
    "PropertyColor",
    "PropertyChoice",
    "AlignmentActions",
    "lumaui_module",
]


def lumaui_module():
    """``gi.repository.LumaUI`` (LumaUI-1), or ImportError saying what is missing."""
    import gi

    try:
        gi.require_version("LumaUI", "1")
    except ValueError as error:
        raise ImportError(
            "the creative parts are LumaUI-1's (C); install luma-developer-platform "
            "or use the dev prefix (tools/lumaui-c-prefix.sh --env)") from error
    from gi.repository import LumaUI

    return LumaUI


class Tool(NamedTuple):
    """One tool: ``Tool("move", "mouse-pointer-2", "Move", "V")``."""

    key: str
    icon: str
    label: str
    shortcut: str | None = None


class Flyout(NamedTuple):
    """Alternatives behind one tool: ``Flyout("shape", "Shapes", [Tool(...), ...])``."""

    key: str
    label: str
    tools: Sequence[Tool]


#: A hairline between groups of tools.
TOOL_SEPARATOR = None


def _connect(widget, signal: str, callback: Callable | None, adapt: Callable | None = None):
    if callback is not None:
        widget.connect(signal, adapt(callback) if adapt else (lambda _w, *args: callback(*args)))
    return widget


def tool_entries(tools: Iterable) -> list[tuple]:
    """The C calls a tool list makes, checked: ("tool", Tool) / ("flyout", Flyout) / ("separator",)."""
    entries: list[tuple] = []
    keys: set[str] = set()
    for item in tools:
        if item is TOOL_SEPARATOR:
            entries.append(("separator",))
            continue
        if isinstance(item, Flyout):
            if not item.tools:
                raise ValueError(f"flyout {item.key!r} has no tools")
            members = [item.key] + [tool.key for tool in item.tools]
            entries.append(("flyout", item))
        elif isinstance(item, Tool):
            members = [item.key]
            entries.append(("tool", item))
        else:
            raise TypeError(f"a tool bar holds Tool, Flyout or TOOL_SEPARATOR, not {item!r}")
        for key in members:
            if key in keys:
                raise ValueError(f"tool keys are unique: {key!r} is already a tool")
            keys.add(key)
    if not keys:
        raise ValueError("a tool bar has at least one tool")
    return entries


def ToolBar(tools: Iterable, *, current: str | None = None, on_tool: Callable[[str], None] | None = None,
            palette: bool = False):
    """The tool strip (``palette=True``: the floating palette over a picture)."""
    entries = tool_entries(tools)
    L = lumaui_module()
    bar = L.ToolBar.new(L.ToolBarKind.PALETTE if palette else L.ToolBarKind.BAR)
    for entry in entries:
        if entry[0] == "separator":
            bar.add_separator()
        elif entry[0] == "tool":
            tool = entry[1]
            bar.add_tool(tool.key, tool.icon, tool.label, tool.shortcut)
        else:
            flyout = entry[1]
            bar.add_flyout(flyout.key, flyout.label)
            for tool in flyout.tools:
                bar.add_flyout_tool(flyout.key, tool.key, tool.icon, tool.label, tool.shortcut)
    if current is not None:
        bar.set_current(current)
    return _connect(bar, "tool-changed", on_tool)


def Layer(id: str, name: str, icon: str, *, children: Iterable = (), visible: bool = True,
          can_hide: bool = True, detail: str | None = None, expanded: bool = True):
    """A LumaUI.Layer with its children (topmost first)."""
    L = lumaui_module()
    layer = L.Layer.new(id, name, icon)
    layer.set_visible(visible)
    layer.set_can_hide(can_hide)
    layer.set_detail(detail)
    layer.set_expanded(expanded)
    for child in children:
        layer.append(child)
    return layer


def LayerTree(layers=None, *, selected: Iterable[str] = (), on_select: Callable[[list[str]], None] | None = None,
              on_activate: Callable | None = None, on_visibility: Callable | None = None,
              on_rename: Callable | None = None, on_reorder: Callable | None = None,
              on_context: Callable | None = None):
    """The layer tree. ``layers`` is a Gio.ListModel of LumaUI.Layer, or a list of them.

    ``on_select(ids)``, ``on_activate(layer)``, ``on_visibility(layer)``,
    ``on_rename(layer)`` and ``on_reorder(layer, parent, index)``; the app
    moves the layer itself.
    """
    L = lumaui_module()
    if layers is not None and not hasattr(layers, "get_n_items"):
        from gi.repository import Gio

        store = Gio.ListStore.new(L.Layer)
        for layer in layers:
            store.append(layer)
        layers = store
    tree = L.LayerTree.new(layers)
    ids = list(selected)
    if ids:
        tree.set_selected(ids)
    if on_select is not None:
        tree.connect("selection-changed", lambda t: on_select(list(t.get_selected())))
    _connect(tree, "activated", on_activate)
    _connect(tree, "visibility-changed", on_visibility)
    _connect(tree, "renamed", on_rename)
    _connect(tree, "move-requested", on_reorder)
    _connect(tree, "context-requested", on_context)
    return tree


def PropertySection(title: str, *fields, action: tuple[str, str, str] | None = None):
    """An inspector section; a field given as a 2-tuple goes in as a pair (None leaves a column empty)."""
    L = lumaui_module()
    section = L.PropertySection.new(title)
    if action is not None:
        icon, label, name = action
        section.set_action(icon, label, name)
    for field in fields:
        if isinstance(field, tuple):
            start, end = field
            section.add_pair(start, end)
        else:
            section.append(field)
    return section


def PropertyNumber(label: str | None, value: float = 0, *, unit: str | None = None, name: str | None = None,
                   step: float | None = None, digits: int = 0, minimum: float | None = None,
                   maximum: float | None = None, compact: bool = False, mixed: bool = False,
                   on_change: Callable[[float], None] | None = None):
    """A number field (.sufld): scrub, step, arithmetic, Mixed."""
    L = lumaui_module()
    field = L.PropertyNumber.new(label, unit)
    if name is not None:
        field.set_name(name)
    if minimum is not None or maximum is not None:
        field.set_range(-1.7976931348623157e308 if minimum is None else minimum,
                        1.7976931348623157e308 if maximum is None else maximum)
    if step is not None or digits:
        field.set_step(step or 1.0, digits)
    field.set_compact(compact)
    field.set_value(value)
    if mixed:
        field.set_mixed()
    return _connect(field, "value-changed", on_change)


def _rgba(value):
    if value is None or not isinstance(value, str):
        return value
    L = lumaui_module()
    ok, rgba = L.PropertyColor.parse(value)
    if not ok:
        raise ValueError(f"{value!r} is not a colour code")
    return rgba


def PropertyColor(label: str, color=None, *, word: str | None = None, palette: Iterable = (),
                  on_change: Callable | None = None):
    """A colour field; ``color`` and ``palette`` take codes ("1F6FE0") or Gdk.RGBA."""
    L = lumaui_module()
    field = L.PropertyColor.new(label)
    if color is not None:
        field.set_rgba(_rgba(color))
    colors = [_rgba(c) for c in palette]
    if colors:
        field.set_palette(colors)
    if word is not None:
        field.set_word(word)
    return _connect(field, "color-changed", on_change)


def PropertyChoice(label: str, choices: Iterable[tuple], *, current: str | None = None, segments: bool = False,
                   on_change: Callable[[str], None] | None = None):
    """A choice: ``choices`` are ``(key, label)`` or ``(key, label, icon)``; ``segments=True`` for .seg."""
    L = lumaui_module()
    field = L.PropertyChoice.new(L.ChoiceKind.SEGMENTS if segments else L.ChoiceKind.MENU, label)
    for choice in choices:
        key, name, icon = (tuple(choice) + (None,))[:3]
        field.add(key, name, icon)
    if current is not None:
        field.set_current(current)
    return _connect(field, "changed", on_change)


def AlignmentActions(action: str | None = None, *, on_align: Callable[[str], None] | None = None):
    """The six alignments; ``action`` is a GAction taking the key ("win.align")."""
    L = lumaui_module()
    return _connect(L.AlignmentActions.new(action), "align", on_align)
