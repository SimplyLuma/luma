# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: the navigation row's slots and the sidebar's variants (SB1).

    sidebar = NavigationSidebar()
    apply_sidebar_variant(sidebar, "people")             # Messages, Phone; width="wide" etc. to override
    sidebar.append_row(SidebarRow("Launch crew", lead=RowLead.group(["Priya Raman", "Sam Ortiz"]),
                                  meta="9:41", subtitle="Sam: see you there", attention=True, trail=3))
    sidebar.append_row(SidebarRow("Priya Raman", lead=RowLead.face("Priya Raman", online=True),
                                  meta="Tue", subtitle="Photos from Saturday", pinned=True))
    append_section(sidebar, "Lists", action=("plus", "New list", new_list))   # a heading with its +

Variants (`SIDEBAR_VARIANTS`), each its width and row metrics as tokens:

- ``people``: conversations and calls. 64 px rows with a 46 px face (38 for
  `RowLead.face(size="medium")`), the time on line 1, the preview on line 2
  with its count or pin (v70 `#m-side .crow`, `.pnrow`).
- ``destinations``: places in the app. 36 px single-line rows, a glyph or a
  mark, a count at the end (v70 `.tki`, `#p-rail .si`).
- ``resources``: things with a live value. A square well or round glyph face,
  two lines, a live trail such as a sparkline (v70 `.mnres`, `.mprow`, `.wxpl`).
- ``files``: documents. A 42x34 thumbnail over two small lines, or no lead
  (v70 `.vwrow`, `.merow`).
- ``tree``: folders and pages. 32 px rows indented 16 per level, a twisty, a
  mark button, drag to move before, after or inside (v70 Notes `.trow`).

`width=` picks a named width instead of the variant's: "narrow", "regular"
or "wide". No pixels: every number is a `nav_*` token.

`SidebarRow` is a `NavigationRow`, so `NavigationSidebar.append_row` and
`list.row-activated` work unchanged; its slots:

- ``lead``: a `RowLead` (face, group, well, glyph face, thumbnail, icon, mark) or any widget.
- ``title_icon``, ``meta`` (time or date on line 1), ``subtitle``, ``subtitle_icon``.
- ``attention``: True (unread: the title bold, the count in the accent) or "missed" (red).
- ``trail``: a count (int), a quiet value (str: a call's time), a widget (sparkline,
  readout, waveform), or a `RowAction`.
- ``people``: who shares it, as faces before the trail (an AvatarStack).
- ``pinned``, ``muted``, ``missing`` (the thing is gone: the row dims).
- tree: ``depth``, ``expanded`` (None: no children), ``mark`` (a MarkValue or
  Mark: shown in a MarkButton), ``on_expand(expanded)``, ``on_mark(value)``,
  ``drag`` (a string carried when dragged) and ``on_drop(value, where)`` with
  where "before", "after" or "inside".

Keyboard: in a tree row Right expands (or does nothing when open), Left
collapses; the twisty is a button too. Accessible: the row reads title,
meta, subtitle, count and state; a tree row states its level and whether it
is expanded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui, rows_tokens  # noqa: E402

__all__ = ["RowAction", "RowLead", "SIDEBAR_VARIANTS", "SIDEBAR_WIDTHS", "SidebarRow", "SidebarSection",
           "append_section", "apply_sidebar_variant", "sidebar_width"]

SIDEBAR_VARIANTS = ("people", "destinations", "resources", "files", "tree", "settings")
SIDEBAR_WIDTHS = ("narrow", "regular", "wide")
FACE_SIZES = ("large", "medium", "small")
THUMBNAIL_KINDS = ("image", "document", "missing")
DROP_PLACES = ("before", "after", "inside")


def sidebar_width(variant: str, width: str | None = None) -> int:
    """The sidebar's outside width for a variant, or for a named width."""
    if variant not in SIDEBAR_VARIANTS:
        raise ValueError(f"sidebar variant must be one of {', '.join(SIDEBAR_VARIANTS)}, not {variant!r}")
    if width is not None:
        if width not in SIDEBAR_WIDTHS:
            raise ValueError(f"sidebar width must be one of {', '.join(SIDEBAR_WIDTHS)}, not {width!r}")
        return int(rows_tokens.value("nav_widths", width))
    return int(rows_tokens.value(f"nav_{variant}", "width"))


def apply_sidebar_variant(sidebar: Gtk.Widget, variant: str, *, width: str | None = None) -> Gtk.Widget:
    """Make a NavigationSidebar one of the variants: its width, its rows' metrics and headings."""
    size = sidebar_width(variant, width)
    for name in [c for c in sidebar.get_css_classes() if c.startswith("lumaui-sidebar-") and
                 c.removeprefix("lumaui-sidebar-") in SIDEBAR_VARIANTS]:
        sidebar.remove_css_class(name)
    sidebar.add_css_class("lumaui-sidebar-variant")
    sidebar.add_css_class(f"lumaui-sidebar-{variant}")
    sidebar.lumaui_variant = variant
    # NavigationSidebar sizes itself from its gutter (0 on the frame): the outside is the request.
    from .lumaui_tokens import SIDEBAR
    sidebar.set_size_request(size - 2 * SIDEBAR["gutter"], -1)
    if variant == "settings" and not getattr(sidebar, "_lumaui_frame_gap", False):
        # v70 .cfside's own right padding is its gap to the island: the frame gives none while it shows.
        sidebar._lumaui_frame_gap = True
        sidebar.connect("map", lambda s: _frame_gap(s, True))
        sidebar.connect("unmap", lambda s: _frame_gap(s, True))  # hidden, no gap either: the body's 8 is the inset
    return sidebar


def _frame_gap(sidebar: Gtk.Widget, shown: bool) -> None:
    from .lumaui_tokens import WINDOW
    frame = sidebar.get_parent()
    while frame is not None and not isinstance(frame, Gtk.Box):  # through a wrapper (SidebarToggle's revealer)
        frame = frame.get_parent()
    if frame is not None and "settings" == getattr(sidebar, "lumaui_variant", None):
        frame.set_spacing(0 if shown else WINDOW["gutter"])


# ── what leads a row ────────────────────────────────────────────────────────

class RowLead:
    """What leads a row. Each constructor returns a widget the row lays out and the kit styles."""

    @staticmethod
    def face(name: str, *, size: str = "large", picture: Gdk.Paintable | None = None,
             online: bool | None = None, hue: float | None = None) -> Gtk.Widget:
        """A person's round face: large 46 (Messages), medium 38 (Phone), small 32. `online` adds presence;
        `hue` is the person's own colour when there is no photo (v71 av(k))."""
        if size not in FACE_SIZES:
            raise ValueError(f"face size must be one of {', '.join(FACE_SIZES)}, not {size!r}")
        pixels = int(rows_tokens.value("row_lead", f"face_{size}"))
        from .rows_people import PresenceFace
        widget = PresenceFace(name, size=pixels, picture=picture, online=bool(online), decorative=True, hue=hue)
        return _lead(widget, "face", size)

    @staticmethod
    def group(people: Sequence[object], *, size: str = "large") -> Gtk.Widget:
        """A group's face: its first two people, corners opposite (Messages' group, Phone's conference call)."""
        if size not in FACE_SIZES:
            raise ValueError(f"face size must be one of {', '.join(FACE_SIZES)}, not {size!r}")
        from .rows_people import GroupFace
        widget = GroupFace(people, size=int(rows_tokens.value("row_lead", f"face_{size}")), decorative=True)
        return _lead(widget, "face", size)

    @staticmethod
    def well(icon: str) -> Gtk.Widget:
        """A glyph in a square well, lit in the accent when its row is chosen (Monitor's resources)."""
        box = Gtk.CenterBox(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        box.set_center_widget(icons.image(icon))
        return _lead(box, "well")

    @staticmethod
    def glyph(icon: str, *, accent: bool = False) -> Gtk.Widget:
        """A glyph on a round face (Maps' places); `accent` for the one that is you (My location)."""
        box = Gtk.CenterBox(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        box.set_center_widget(icons.image(icon))
        widget = _lead(box, "glyph")
        lumaui.set_css_class(widget, "accent", accent)
        return widget

    @staticmethod
    def thumbnail(picture: Gdk.Paintable | None = None, *, kind: str = "image") -> Gtk.Widget:
        """A 42x34 thumbnail (Viewer): the picture, a page for a document, a glyph when the file is gone."""
        if kind not in THUMBNAIL_KINDS:
            raise ValueError(f"thumbnail kind must be one of {', '.join(THUMBNAIL_KINDS)}, not {kind!r}")
        frame = Gtk.CenterBox(valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN,
                              accessible_role=Gtk.AccessibleRole.PRESENTATION)
        if kind == "image" and picture is not None:
            frame.set_center_widget(Gtk.Picture(paintable=picture, content_fit=Gtk.ContentFit.COVER,
                                                can_shrink=True, hexpand=True, vexpand=True))
        elif kind == "document":
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
            page.add_css_class("lumaui-row-page")
            for index in range(3):
                line = Gtk.Box()
                line.add_css_class("lumaui-row-page-line")
                if index == 0:
                    line.add_css_class("first")
                page.append(line)
            frame.set_center_widget(page)
        else:
            frame.set_center_widget(icons.image("image"))
        widget = _lead(frame, "thumbnail")
        widget.add_css_class(kind)
        return widget

    @staticmethod
    def icon(icon: str) -> Gtk.Widget:
        """A plain glyph (a destination: Today, Photos, Favourites)."""
        return _lead(icons.image(icon), "icon")

    @staticmethod
    def dot(hue: float) -> Gtk.Widget:
        """A list's colour (Tasks' lists): a small rounded square in its hue (v70 .tkdot)."""
        return _lead(_HueDot(hue), "dot")

    @staticmethod
    def mark(value: object) -> Gtk.Widget:
        """A list's or calendar's mark (Tasks' lists): a MarkValue or a Mark."""
        from .rows_mark import Mark, MarkValue
        widget = value if isinstance(value, Mark) else Mark(value=value if isinstance(value, MarkValue) else None)
        return _lead(widget, "mark")


class _HueDot(Gtk.Widget):
    """v70 .tkdot: 10 px, 4 round, oklch(0.7 0.14 hue); the colour is the token lightness and chroma at its hue."""

    __gtype_name__ = "LumaUIRowDot"

    def __init__(self, hue: float) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.PRESENTATION, halign=Gtk.Align.CENTER)
        self.hue = float(hue) % 360

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        side = int(rows_tokens.value("row_lead", "dot"))
        return side, side, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        gi.require_version("Gsk", "4.0")
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene, Gsk
        m = rows_tokens.group("row_lead")
        width, height = self.get_width(), self.get_height()
        rect = Graphene.Rect().init(0, 0, width, height)
        corner = Graphene.Size().init(m["dot_radius"], m["dot_radius"])
        rounded = Gsk.RoundedRect()
        rounded.init(rect, corner, corner, corner, corner)
        colour = Gdk.RGBA()
        colour.parse(lumaui.oklch_rgba(m["dot_lightness_ratio"], m["dot_chroma_ratio"], self.hue))
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(colour, rect)
        snapshot.pop()


def _lead(widget: Gtk.Widget, kind: str, size: str | None = None) -> Gtk.Widget:
    widget.add_css_class("lumaui-row-lead")
    widget.add_css_class(f"lead-{kind}")
    if size:
        widget.add_css_class(size)
    widget.lumaui_lead = (kind, size)
    widget.set_valign(Gtk.Align.CENTER)
    widget.set_hexpand(False)
    return widget


@dataclass(frozen=True)
class RowAction:
    """A trailing action in a row (Remove, Retry): a Lucide glyph and the words it stands for."""

    icon: str
    label: str
    on_activate: Callable[[], None]


# ── the row ──────────────────────────────────────────────────────────────────

def _widgets_navigation_row() -> type:
    from .widgets import NavigationRow
    return NavigationRow


def _label(text: str, css: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.add_css_class(css)
    return label


class _SidebarRowBase(Gtk.ListBoxRow):
    """Stands in for NavigationRow where widgets.py cannot load (no Luma typelib): same classes and API."""

    def set_trailing(self, trailing: str) -> None:  # pragma: no cover - replaced below
        raise NotImplementedError


def _row_base() -> type:
    try:
        return _widgets_navigation_row()
    except (ImportError, ValueError):
        return _SidebarRowBase


class SidebarRow(_row_base()):  # type: ignore[misc]
    """A sidebar row with slots: lead, meta, subtitle, attention, trail; depth, twisty, mark and drop in a tree."""

    __gtype_name__ = "LumaUISidebarRow"

    def __init__(self, title: str, *, lead: Gtk.Widget | None = None, title_icon: str | None = None,
                 meta: str = "", subtitle: str = "", subtitle_icon: str | None = None,
                 attention: bool | str = False, trail: int | str | Gtk.Widget | RowAction | None = None,
                 pinned: bool = False, muted: bool = False, missing: bool = False,
                 depth: int = 0, expanded: bool | None = None, mark: object | None = None,
                 on_expand: Callable[[bool], None] | None = None, on_mark: Callable[[object], None] | None = None,
                 drag: str | None = None, on_drop: Callable[[str, str], None] | None = None,
                 folder: bool = False, people: Sequence[object] | None = None,
                 can_contain: bool | None = None,
                 can_drop: Callable[[str, str], bool] | None = None) -> None:
        if attention not in (False, True, "unread", "missed"):
            raise ValueError("attention is True (unread) or \"missed\"")
        if depth < 0:
            raise ValueError("a row's depth is 0 or more")
        tree = depth > 0 or expanded is not None or mark is not None or on_drop is not None
        # A tree's rows are tree items: screen readers hear their level and whether they are open.
        Gtk.ListBoxRow.__init__(self, accessible_role=Gtk.AccessibleRole.TREE_ITEM if tree else Gtk.AccessibleRole.LIST_ITEM)
        self.add_css_class("luma-navigation-row")
        self.add_css_class("lumaui-row")
        self._title_text, self._meta_text, self._subtitle_text = title, meta, subtitle
        self._count: int | None = None
        self._trail_text = ""
        self.on_expand, self.on_drop = on_expand, on_drop
        self.depth, self.drag_value = depth, drag
        self.can_contain, self.can_drop = can_contain, can_drop
        self._drop_place = None
        self._tree = depth > 0 or expanded is not None or mark is not None or on_drop is not None
        lumaui.set_css_class(self, "tree", self._tree)
        lumaui.set_css_class(self, "folder", folder)

        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        line.add_css_class("luma-navigation-line")
        line.add_css_class("lumaui-row-line")
        self._line = line
        if self._tree:
            # v70 .trow: the row itself steps in 16 per level, chip and all.
            self.set_margin_start(depth * int(rows_tokens.value("nav_tree", "indent")))
            self.twisty: Gtk.Widget
            if expanded is None:
                self.twisty = Gtk.Box(accessible_role=Gtk.AccessibleRole.PRESENTATION)
            elif folder:
                # A folder opens from anywhere on its row (v70 .trow.fd): its twisty only shows which way.
                self.twisty = Gtk.CenterBox(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
                self.twisty.set_center_widget(icons.image("chevron-right"))
                self.connect("activate", lambda _r: self.set_expanded(not self.expanded))
            else:
                self.twisty = Gtk.Button(valign=Gtk.Align.CENTER)
                self.twisty.set_child(icons.image("chevron-right"))
                self.twisty.update_property([Gtk.AccessibleProperty.LABEL], ["Collapse" if expanded else "Expand"])
                self.twisty.set_tooltip_text("Collapse" if expanded else "Expand")
                self.twisty.connect("clicked", lambda _b: self.set_expanded(not self.expanded))
            self.twisty.add_css_class("lumaui-row-twisty")
            line.append(self.twisty)
        self.expanded = bool(expanded)
        self._expandable = expanded is not None

        self.mark_button = None
        if mark is not None:
            from .rows_mark import MarkButton
            self.mark_button = MarkButton(mark, on_change=on_mark, label="Change icon")
            self.mark_button.add_css_class("lumaui-row-mark")
            line.append(self.mark_button)

        self.lead = lead
        if lead is not None:
            if not hasattr(lead, "lumaui_lead"):
                _lead(lead, "custom")
            kind, size = lead.lumaui_lead
            self.add_css_class(f"lead-{kind}")
            if size:
                self.add_css_class(f"lead-{kind}-{size}")
            line.append(lead)
        else:
            self.add_css_class("lead-none")

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        labels.add_css_class("lumaui-row-labels")
        first = Gtk.Box()
        first.add_css_class("lumaui-row-first")
        if title_icon:
            icon = icons.image(title_icon)
            icon.add_css_class("lumaui-row-title-icon")
            first.append(icon)
        self.title_label = _label(title, "luma-navigation-title")
        self.title_label.add_css_class("lumaui-row-title")
        self.title_label.set_hexpand(True)
        first.append(self.title_label)
        self.meta_label = _label(meta, "lumaui-row-meta")
        self.meta_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.meta_label.set_visible(bool(meta))
        first.append(self.meta_label)
        labels.append(first)

        second = Gtk.Box()
        second.add_css_class("lumaui-row-second")
        if subtitle_icon:
            icon = icons.image(subtitle_icon)
            icon.add_css_class("lumaui-row-subtitle-icon")
            second.append(icon)
        self.subtitle_label = _label(subtitle, "luma-navigation-subtitle")
        self.subtitle_label.add_css_class("lumaui-row-subtitle")
        self.subtitle_label.set_hexpand(True)
        second.append(self.subtitle_label)
        self._second = second
        labels.append(second)
        line.append(labels)

        # With a time on line 1 the count or pin sits under it on line 2 (v70 .crow);
        # otherwise the trail ends the row, centred.
        self._trail_box = Gtk.Box(valign=Gtk.Align.CENTER)
        self._trail_box.add_css_class("lumaui-row-trail")
        (second if meta else line).append(self._trail_box)
        self.people = None
        if people:
            # Who shares it (Tasks' lists, Notes' pages): faces before the count (v70 .tkstk, .nfaces).
            from .rows_people import AvatarStack
            self.people = AvatarStack(people, size="small" if self._tree else "row")
            self.people.add_css_class("lumaui-row-people")
            self._trail_box.append(self.people)
        self._pin = None
        lumaui.set_css_class(self, "pinned", pinned)
        if pinned and not self._tree:  # a pinned page in a tree says so by its place, not a glyph
            self._pin = icons.image("pin")
            self._pin.add_css_class("lumaui-row-pin")
            self._trail_box.append(self._pin)
        self._badge = None
        self.trail_widget: Gtk.Widget | None = None
        self.set_trail(trail)
        self.set_child(line)

        self._attention: bool | str = False
        self.set_attention(attention)
        lumaui.set_css_class(self, "muted", muted)
        self.set_missing(missing)
        self._refresh_second()

        if self._tree:
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._key)
            self.add_controller(keys)
            if self._expandable:
                self.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(self.expanded))])
            lumaui.set_css_class(self, "open", self.expanded)
        if drag is not None:
            source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            source.connect("prepare", self._drag_prepare)
            source.connect("drag-begin", self._drag_begin)
            source.connect("drag-end", lambda *_a: self.remove_css_class("dragging"))
            self.drag_source = source
            self.add_controller(source)
        if on_drop is not None:
            target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
            target.set_preload(True)
            target.connect("enter", self._drop_motion)
            target.connect("motion", self._drop_motion)
            target.connect("notify::value", self._drop_value_ready)
            target.connect("leave", self._drop_leave)
            target.connect("drop", self._dropped)
            self.add_controller(target)
        self._speak()

    # ── state the app changes in place (selection and focus are kept) ─────

    def set_trail(self, trail: int | Gtk.Widget | RowAction | None) -> None:
        """The row's trailing count, widget or action; None removes it."""
        if self.trail_widget is not None:
            self._trail_box.remove(self.trail_widget)
            self.trail_widget = None
        self._badge, self._count = None, None
        if isinstance(trail, bool):
            raise TypeError("a row's trail is a count, a widget or a RowAction")
        if isinstance(trail, str):
            trail = self._quiet(trail) if trail else None
        if isinstance(trail, int):
            from .content_badges import CountBadge
            self._badge = CountBadge(trail, attention=bool(self._attention_on()) if hasattr(self, "_attention") else False)
            self._count = trail
            widget: Gtk.Widget | None = self._badge
        elif isinstance(trail, RowAction):
            widget = Gtk.Button(tooltip_text=trail.label, valign=Gtk.Align.CENTER)
            widget.set_child(icons.image(trail.icon))
            widget.add_css_class("lumaui-row-action")
            widget.update_property([Gtk.AccessibleProperty.LABEL], [trail.label])
            widget.connect("clicked", lambda _b, cb=trail.on_activate: cb())
        else:
            widget = trail
        if widget is not None:
            widget.set_valign(Gtk.Align.CENTER)
            self._trail_box.append(widget)
            self.trail_widget = widget
        if self._pin is not None:
            # v70: the pin shows only when there is no count to show.
            self._pin.set_visible(not self._count)
        self._trail_box.set_visible(widget is not None or self._pin is not None or self.people is not None)
        if hasattr(self, "_attention"):
            self._speak()

    @staticmethod
    def _quiet(text: str) -> Gtk.Label:
        """A trailing value in the meta's voice (Phone's call time, a size)."""
        label = Gtk.Label(label=text)
        label.add_css_class("lumaui-row-meta")
        label.add_css_class("luma-navigation-trailing")
        return label

    def set_count(self, count: int) -> None:
        """Update the trailing count (0 hides it, as CountBadge does)."""
        if self._badge is None:
            self.set_trail(count)
            return
        self._count = count
        self._badge.set_count(count)
        if self._pin is not None:
            self._pin.set_visible(not count)
        self._speak()

    def set_trailing(self, trailing: str) -> None:
        """NavigationRow's live value: a number becomes the count; other text shows as a quiet value."""
        text = trailing.strip()
        if text.replace(",", "").isdigit():
            self.set_count(int(text.replace(",", "")))
            return
        self.set_trail(text or None)

    def set_meta(self, meta: str) -> None:
        self._meta_text = meta
        self.meta_label.set_label(meta)
        self.meta_label.set_visible(bool(meta))
        self._speak()

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle_text = subtitle
        self.subtitle_label.set_label(subtitle)
        self._refresh_second()
        self._speak()

    def set_title(self, title: str) -> None:
        self._title_text = title
        self.title_label.set_label(title)
        self._speak()

    def _attention_on(self) -> bool | str:
        return self._attention

    def set_attention(self, attention: bool | str) -> None:
        """True or "unread": the title bold, the count in the accent; "missed": red; False: neither."""
        if attention not in (False, True, "unread", "missed"):
            raise ValueError("attention is True (unread) or \"missed\"")
        self._attention = "unread" if attention is True else attention
        lumaui.set_css_class(self, "attention", self._attention == "unread")
        lumaui.set_css_class(self, "missed", self._attention == "missed")
        if self._badge is not None:
            self._badge.set_attention(self._attention == "unread")
        self._speak()

    def set_missing(self, missing: bool) -> None:
        """The thing this row stands for is gone (moved or deleted): the row dims."""
        self.missing = bool(missing)
        lumaui.set_css_class(self, "missing", self.missing)

    def set_expanded(self, expanded: bool) -> None:
        """Open or close a tree row's children; tells `on_expand`."""
        if not self._expandable or bool(expanded) == self.expanded:
            return
        self.expanded = bool(expanded)
        lumaui.set_css_class(self, "open", self.expanded)
        self.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(self.expanded))])
        if isinstance(self.twisty, Gtk.Button):
            self.twisty.update_property([Gtk.AccessibleProperty.LABEL], ["Collapse" if self.expanded else "Expand"])
            self.twisty.set_tooltip_text("Collapse" if self.expanded else "Expand")
        if self.on_expand is not None:
            self.on_expand(self.expanded)

    def _refresh_second(self) -> None:
        has_trail = self._meta_text and self._trail_box.get_visible()
        self.subtitle_label.set_visible(bool(self._subtitle_text))
        self._second.set_visible(bool(self._subtitle_text) or bool(has_trail))
        lumaui.set_css_class(self, "two-line", self._second.get_visible())

    def _speak(self) -> None:
        parts = [self._title_text]
        if self._meta_text:
            parts.append(self._meta_text)
        if self._subtitle_text:
            parts.append(self._subtitle_text)
        if self._count:
            parts.append(f"{self._count} unread" if self._attention == "unread" else str(self._count))
        elif self._attention == "unread":
            parts.append("unread")
        if self._attention == "missed":
            parts.append("missed")
        if getattr(self, "missing", False):
            parts.append("moved or deleted")
        if self.lead is not None and getattr(self.lead, "online", False):
            parts.append("online")
        self.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(parts)])
        if self._tree:
            self.update_property([Gtk.AccessibleProperty.LEVEL], [GObject.Value(GObject.TYPE_INT, self.depth + 1)])
        # NavigationRow's own names, for code that reads them.
        self._accessible_title, self._accessible_subtitle = self._title_text, self._subtitle_text

    # ── tree keys and drops ────────────────────────────────────────────────

    def _key(self, _controller, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        rtl = self.get_direction() == Gtk.TextDirection.RTL
        opening, closing = (Gdk.KEY_Left, Gdk.KEY_Right) if rtl else (Gdk.KEY_Right, Gdk.KEY_Left)
        if keyval == opening and self._expandable and not self.expanded:
            self.set_expanded(True)
            return True
        if keyval == closing and self._expandable and self.expanded:
            self.set_expanded(False)
            return True
        return False

    def drop_place(self, y: float) -> str:
        """Where a drop at `y` would go: the top quarter before, the bottom quarter after, else inside.

        A row that cannot hold children (a page with none, `expanded=None` and
        not a folder) takes only before and after, split at its middle."""
        height = max(1, self.get_height())
        if not (self.can_contain if self.can_contain is not None else
                self._expandable or self.has_css_class("folder")):
            return "before" if y < height / 2 else "after"
        if y < height / 4:
            return "before"
        if y > height * 3 / 4:
            return "after"
        return "inside"

    def _drag_prepare(self, _source, x, y):
        self._drag_hotspot = (int(x), int(y))
        return Gdk.ContentProvider.new_for_value(self.drag_value)

    def _drag_begin(self, source, _drag) -> None:
        # The payload is an application ID, never a user-facing drag label.
        # Snapshot the existing row's icon/title slots, including its live rename.
        source.set_icon(Gtk.WidgetPaintable.new(self.get_child()),
                        *getattr(self, "_drag_hotspot", (0, 0)))
        self.add_css_class("dragging")

    def do_snapshot(self, snapshot) -> None:
        Gtk.ListBoxRow.do_snapshot(self, snapshot)
        if self._drop_place not in ("before", "after"):
            return
        from . import media_style
        # A border/shadow follows the row's rounded corners. Insertion is a
        # straight line between rows, with no allocation change while dragging.
        y = 0 if self._drop_place == "before" else max(0, self.get_height() - 2)
        snapshot.append_color(media_style.colour(self, "luma_accent_ink"),
                              media_style.rect(0, y, self.get_width(), 2))

    def _mark_drop(self, place: str | None) -> None:
        self._drop_place = place
        for name in DROP_PLACES:
            lumaui.set_css_class(self, f"drop-{name}", place == name)
        self.queue_draw()

    def _drop_leave(self, *_args):
        self._drop_position = None
        self._mark_drop(None)

    def _drop_value_ready(self, target, _property):
        position = getattr(self, "_drop_position", None)
        if position is not None:
            self._drop_motion(target, *position)

    def _drop_motion(self, target, x: float, y: float) -> Gdk.DragAction:
        self._drop_position = (x, y)
        place = self.drop_place(y)
        value = target.get_value()
        if value is None:
            # Native DnD reads the preloaded payload asynchronously. Keep the
            # typed transport open while waiting, without claiming a valid hint.
            self._mark_drop(None)
            return Gdk.DragAction.MOVE
        valid = isinstance(value, str) and value != self.drag_value
        if valid and self.can_drop is not None:
            valid = self.can_drop(value, place)
        self._mark_drop(place if valid else None)
        return Gdk.DragAction.MOVE if valid else Gdk.DragAction(0)

    def _dropped(self, _target, value: object, _x: float, y: float) -> bool:
        place = self.drop_place(y)
        self._drop_leave()
        if not isinstance(value, str) or value == self.drag_value or self.on_drop is None:
            return False
        if self.can_drop is not None and not self.can_drop(value, place):
            return False
        return self.on_drop(value, place) is not False


class SidebarSection(Gtk.ListBoxRow):
    """A sidebar heading, as written ("Lists", "Recents"), optionally with its action (+ New list)."""

    __gtype_name__ = "LumaUISidebarSection"

    def __init__(self, label: str, *, action: tuple[str, str, Callable[[], None]] | None = None,
                 subtitle: str | None = None, hue: float | None = None, ruled: bool = False) -> None:
        """`subtitle` and `hue`: an account's heading (v71 Charlie .crnh): its dot in its hue, its name,
        its address under it. `ruled`: a hairline above, as a heading with an action has."""
        super().__init__(selectable=False, activatable=False)
        self.add_css_class("luma-navigation-section")
        self.add_css_class("lumaui-row-section")
        line = Gtk.Box()
        line.add_css_class("luma-navigation-heading-line")
        if hue is not None:
            dot = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
            dot.add_css_class("lumaui-section-dot")
            lumaui.hue_class(dot, int(round(hue)) % 360)
            line.append(dot)
        heading = Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        heading.add_css_class("luma-navigation-heading")
        if subtitle:
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            words.append(heading)
            sub = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            sub.add_css_class("lumaui-section-subtitle")
            words.append(sub)
            line.append(words)
            self.add_css_class("account")
        else:
            line.append(heading)
        if ruled or subtitle:
            self.add_css_class("ruled")
        self.action_button = None
        if action is not None:
            icon, words, callback = action
            button = Gtk.Button(tooltip_text=words, valign=Gtk.Align.CENTER)
            button.set_child(icons.image(icon))
            button.add_css_class("lumaui-section-action")
            button.update_property([Gtk.AccessibleProperty.LABEL], [words])
            button.connect("clicked", lambda _b: callback())
            line.append(button)
            self.action_button = button
            self.add_css_class("with-action")
        self.set_child(line)
        self.update_property([Gtk.AccessibleProperty.LABEL], [f"{label}, {subtitle}" if subtitle else label])


def append_section(sidebar: Gtk.Widget, label: str, *,
                   action: tuple[str, str, Callable[[], None]] | None = None,
                   subtitle: str | None = None, hue: float | None = None, ruled: bool = False) -> SidebarSection:
    """Add a heading to a NavigationSidebar; `action=(icon, label, callback)` gives it its +;
    `subtitle`/`hue` make it an account's heading (dot, name, address); `ruled` a hairline above."""
    section = SidebarSection(label, action=action, subtitle=subtitle, hue=hue, ruled=ruled)
    if sidebar.list.get_first_child() is not None:
        section.add_css_class("separated")
    sidebar.list.append(section)
    return section


def rows(sidebar: Gtk.Widget) -> Iterable[SidebarRow]:
    """The SidebarRows in a sidebar, in order (headings skipped)."""
    child = sidebar.list.get_first_child()
    while child is not None:
        if isinstance(child, SidebarRow):
            yield child
        child = child.get_next_sibling()
