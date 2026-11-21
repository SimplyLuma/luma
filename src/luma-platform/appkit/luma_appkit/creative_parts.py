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
    PropertyNumber(label, value, unit=), PropertyColor(label, "1F6FE0", on_commit=, bar=),
    ColorPicker("1F6FE0", on_change=, on_commit=), recent_colors(),
    PropertyChoice(label, [(key, label, icon)], segments=), AlignmentActions()
    ArrangeView(content, canvas=(w, h), items=[ArrangeItem(...)], on_select=, on_change=, on_commit=)

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
    "ColorPicker",
    "recent_colors",
    "PropertyChoice",
    "AlignmentActions",
    "ArrangeItem",
    "ArrangeView",
    "ALIGNMENTS",
    "align_rects",
    "distribute_rects",
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
                  on_change: Callable | None = None, on_commit: Callable | None = None, bar=None):
    """A colour field; ``color`` and ``palette`` take codes ("1F6FE0") or Gdk.RGBA.

    The swatch opens the ColorPicker (Luma's palette, ``palette``, recent
    colours, the eyedropper, a code) in a popover, or with ``bar=`` (an
    ActionCenter, Python or C) as that bar's grown panel. ``on_change(rgba)``
    follows every change, live while dragging; ``on_commit(rgba)`` fires once
    when the person finishes one (one undo step).
    """
    L = lumaui_module()
    field = L.PropertyColor.new(label)
    if color is not None:
        field.set_rgba(_rgba(color))
    colors = [_rgba(c) for c in palette]
    if colors:
        field.set_palette(colors)
    if word is not None:
        field.set_word(word)
    if bar is not None:
        if isinstance(bar, L.ActionCenter):
            field.set_action_center(bar)
        else:
            field.connect("open-picker", lambda _field, picker: (bar.grow(f"lumaui-color-{label}", picker), True)[1])
    _connect(field, "color-committed", on_commit)
    return _connect(field, "color-changed", on_change)


def ColorPicker(color=None, *, palette: Iterable = (), with_alpha: bool = True,
                on_change: Callable | None = None, on_commit: Callable | None = None):
    """The kit's colour picker, to put in an app's own panel (``center.grow("colour", ColorPicker(...))``).

    Saturation/brightness, hue and opacity, Luma's palette, ``palette`` (the
    document's colours), this app's recent colours, the eyedropper (the
    Screenshot portal's PickColor, ``pick_from_screen()``) and a hex code.
    ``on_change(rgba)`` follows every change live; ``on_commit(rgba)`` fires once
    per finished change. ``set_rgba()``/``get_rgba()`` set and read the colour.
    """
    L = lumaui_module()
    picker = L.ColorPicker.new()
    picker.set_with_alpha(with_alpha)
    if color is not None:
        picker.set_rgba(_rgba(color))
    colors = [_rgba(c) for c in palette]
    if colors:
        picker.set_palette(colors)
    _connect(picker, "color-committed", on_commit)
    return _connect(picker, "color-changed", on_change)


def recent_colors() -> list:
    """The colours recently committed in this app (any picker), newest first, as Gdk.RGBA."""
    return list(lumaui_module().ColorPicker.get_recent_colors())


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


class ArrangeItem(NamedTuple):
    """One box on an ArrangeView's canvas, in canvas units."""

    id: str
    label: str
    x: float
    y: float
    width: float
    height: float
    locked: bool = False
    visible: bool = True
    keeps_aspect: bool = False
    #: A person may crop it (Ctrl on a handle, crop mode); a double click then crops instead of activating.
    croppable: bool = False
    #: Fractions of the source cut from the left, top, right and bottom (0 to 0.49 each); the rectangle
    #: above is what shows.
    crop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


def ArrangeView(content=None, *, canvas: tuple[float, float] = (1920, 1080), items: Iterable[ArrangeItem] = (),
                selected: str | None = None, snapping: bool = True,
                on_select: Callable[[str | None], None] | None = None,
                on_change: Callable[[str, float, float, float, float], None] | None = None,
                on_commit: Callable[[str, float, float, float, float], None] | None = None,
                on_activate: Callable[[str], None] | None = None,
                on_crop: Callable[[str, float, float, float, float], None] | None = None,
                on_crop_commit: Callable[[str, float, float, float, float], None] | None = None,
                on_drag_mode: Callable[[str], None] | None = None,
                on_selection: Callable[[list[str]], None] | None = None,
                on_group_change: Callable[[dict], None] | None = None,
                on_group_commit: Callable[[dict], None] | None = None):
    """Boxes on a canvas, moved, resized and cropped by hand, with snapping guides.

    ``content`` is the picture of the canvas (it covers the fitted canvas).
    ``on_change`` follows every step of a drag or nudge; ``on_commit`` is the
    end of one (an undo step). Update boxes with ``set_item_rect(id, x, y, w, h)``.

    A handle drag scales a box that keeps its aspect (anchored at the opposite
    corner or edge); Shift stretches, Alt resizes from the centre, Ctrl crops
    a croppable box's edge. ``on_crop(id, left, top, right, bottom)`` follows a
    crop (fractions of the source), ``on_crop_commit`` ends one; keep them with
    ``set_item_crop(id, l, t, r, b)``. ``set_cropping(id)`` / ``get_cropping()``
    is crop mode (also a double click or Enter). ``on_drag_mode(mode)`` follows
    ``get_drag_mode()`` ("move", "resize", "stretch", "resize-centre",
    "stretch-centre", "crop", "crop-centre", "pan", "none") for a hint.

    Several boxes: Shift+click, a rubber band from empty canvas, Ctrl+A or ``set_selection([ids])``;
    ``on_selection(ids)`` follows a person's changes (``on_select(id)`` still gets the last one).
    Two or more move and scale together inside one box: ``on_group_change({id: (x, y, w, h)})`` every
    step, ``on_group_commit(...)`` once (one undo step). ``align_rects``/``distribute_rects`` lay them out.
    """
    L = lumaui_module()
    view = L.ArrangeView.new(content)
    width, height = canvas
    view.set_canvas_size(width, height)
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ValueError(f"box ids are unique: {item.id!r}")
        seen.add(item.id)
        add_arrange_item(view, item)
    view.set_snapping(snapping)
    if selected is not None:
        view.set_selected(selected)
    _connect(view, "selection-changed", on_select)
    _connect(view, "item-changed", on_change)
    _connect(view, "item-committed", on_commit)
    _connect(view, "item-activated", on_activate)
    _connect(view, "crop-changed", on_crop)
    _connect(view, "crop-committed", on_crop_commit)
    if on_drag_mode is not None:
        view.connect("notify::drag-mode", lambda v, _s: on_drag_mode(v.get_drag_mode()))
    if on_selection is not None:
        view.connect("selection-ids-changed", lambda _v, ids: on_selection(list(ids)))
    for signal, callback in (("group-changed", on_group_change), ("group-committed", on_group_commit)):
        if callback is not None:
            view.connect(signal, lambda _v, rects, cb=callback: cb(dict(rects.unpack())))
    return view


ALIGNMENTS = ("left", "centre", "right", "top", "middle", "bottom")


def _bounds(rects: dict) -> tuple[float, float, float, float]:
    x0 = min(r[0] for r in rects.values())
    y0 = min(r[1] for r in rects.values())
    x1 = max(r[0] + r[2] for r in rects.values())
    y1 = max(r[1] + r[3] for r in rects.values())
    return x0, y0, x1 - x0, y1 - y0


def align_rects(rects: dict, how: str, *, canvas: tuple[float, float] | None = None) -> dict:
    """``rects`` ({id: (x, y, w, h)}) lined up: to the box around them, or, for a single rect, to the
    canvas (``canvas=(w, h)``). ``how``: left, centre, right, top, middle, bottom. Returns the new
    rects; sizes never change."""
    if how not in ALIGNMENTS:
        raise ValueError(f"an alignment is one of {', '.join(ALIGNMENTS)}, not {how!r}")
    if not rects:
        return {}
    if len(rects) == 1:
        if canvas is None:
            raise ValueError("one rect aligns to the canvas: pass canvas=(w, h)")
        bx, by, bw, bh = 0.0, 0.0, float(canvas[0]), float(canvas[1])
    else:
        bx, by, bw, bh = _bounds(rects)
    out = {}
    for key, (x, y, w, h) in rects.items():
        if how == "left":
            x = bx
        elif how == "centre":
            x = bx + (bw - w) / 2
        elif how == "right":
            x = bx + bw - w
        elif how == "top":
            y = by
        elif how == "middle":
            y = by + (bh - h) / 2
        else:
            y = by + bh - h
        out[key] = (x, y, w, h)
    return out


def distribute_rects(rects: dict, axis: str = "h") -> dict:
    """``rects`` spread so the gaps between them are equal, the first and last staying put (three or
    more; ``axis`` "h" across, "v" down). Returns the new rects."""
    if axis not in ("h", "v"):
        raise ValueError(f"distribute along h or v, not {axis!r}")
    if len(rects) < 3:
        return dict(rects)
    i = 0 if axis == "h" else 1
    order = sorted(rects, key=lambda k: rects[k][i])
    first, last = rects[order[0]], rects[order[-1]]
    span = last[i] + last[i + 2] - first[i]
    gap = (span - sum(rects[k][i + 2] for k in order)) / (len(order) - 1)
    out, at = {}, first[i]
    for key in order:
        r = list(rects[key])
        r[i] = at
        out[key] = tuple(r)
        at += r[i + 2] + gap
    return out


def add_arrange_item(view, item: ArrangeItem) -> None:
    """Add (or replace) one box on an ArrangeView."""
    if item.width <= 0 or item.height <= 0:
        raise ValueError(f"a box has a size: {item!r}")
    if view.get_item_rect(item.id)[0]:
        view.remove_item(item.id)
    view.add_item(item.id, item.label, item.x, item.y, item.width, item.height)
    view.set_item_locked(item.id, item.locked)
    view.set_item_visible(item.id, item.visible)
    view.set_item_keeps_aspect(item.id, item.keeps_aspect)
    view.set_item_croppable(item.id, item.croppable)
    if len(item.crop) != 4:
        raise ValueError(f"a crop is (left, top, right, bottom): {item.crop!r}")
    view.set_item_crop(item.id, *item.crop)
