# SPDX-License-Identifier: Apache-2.0
"""LumaUI action: the action center, the bar that grows in place.

GTK and GNOME have no standard for this, so LumaUI makes it a primitive. The
floating bar at the foot of an island is where a person acts, and instead of
opening a dialog it grows in place (v70 `.fbar`, `.fbar.lac2`, `.lac`,
`.lacsplit`, `lAC.open`, `lAC.bind`; Charlie's reply is the approved
reference and is ported exactly):

- **bar**: the actions for what is selected or open.
- **double**: a context line over the bar ("Replying to Priya's message").
- **editor**: the full editor the bar grows into: a header with the mode and
  who it is for, optional field rows, a tool row, the application's content
  (a composer, an event form, settings fields, photo-edit tools), and a
  footer with Discard, the key hint and the primary action. It caps at the
  island less 20 px, and its content scrolls; text is at most 40% of the
  window tall.
- **split**: two bars side by side (a stack on a phone).

Esc folds the editor back to the bar and keeps the draft ("Draft: …" in the
prompt, restored when it grows again). Ctrl+Return runs the primary action.
Everything is reachable with Tab. On a phone the editor is a full-width sheet
from the bottom edge and the bars span the width. The bar registers with the
nearest ToastHost, so toasts clear it.

    center = ActionCenter()
    center.attach(island_host)                       # a ToastHost / LayerHost around the island
    center.set_editor(ActionEditor(
        "Reply all", "reply-all", summary="to {}", summary_emphasis="Priya Raman, Nora Feld",
        modes=[("one", "Reply", "reply"), ("all", "Reply all", "reply-all"), ("fwd", "Forward", "forward")],
        mode="all", on_mode=switch_mode,
        tools=[BarAction("bold", tooltip="Bold", on_activate=bold), SEPARATOR, SPACER,
               BarAction("paperclip", tooltip="Attach", on_activate=attach)],
        placeholder="Write your reply",
        primary=BarAction("send-horizontal", "Send", on_activate=send),
    ))
    center.show_bar([BarPrompt("Reply to Priya and Nora…"),
                     BarAction("paperclip", tooltip="Attach", on_activate=attach)])
    center.show_bar(items, context=BarContext("reply", "Replying to {}’s message", emphasis="Priya"))
    center.show_split([BarChip("3 photos", icon="image"), BarAction("share-2", "Share")],
                      [BarAction("heart", "Favourite"), BarAction("trash-2", tooltip="Delete")])
    center.grow() / center.fold() / center.discard()

Applications never style the bar: they hand it actions and content.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, GObject, Graphene, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from . import lumaui_tokens as tokens  # noqa: E402
from .structure_layers import LayerHost  # noqa: E402

__all__ = ["ActionCenter", "ActionEditor", "BarAction", "BarChip", "BarContext", "BarPrompt", "BarModes", "BarWidget",
           "SEPARATOR", "RULE", "SPACER", "AC_STATES", "make_control", "register_item"]

#: v71 base `.fbar { bottom: 16px }` (an app that does not override it to 24: Clock). Window bars only.
BAR_BOTTOM_TIGHT = 16

#: The states the action center moves between.
AC_STATES = ("hidden", "bar", "double", "editor", "split")


# ── what goes in a bar ─────────────────────────────────────────────────────

@dataclass
class BarWidget:
    """An unparented shared control, retained when the bar is rebuilt.

    Use this for composite controls such as MediaTransportLCD; the control
    keeps its own responsive sizing and real callbacks.
    """
    widget: Gtk.Widget

    def __post_init__(self) -> None:
        if not isinstance(self.widget, Gtk.Widget):
            raise TypeError("BarWidget needs a Gtk.Widget")
        if self.widget.get_parent() is not None:
            raise ValueError("BarWidget needs an unparented widget")


@dataclass
class BarAction:
    """A control: `icon` plus `label` is a button, `icon` alone is an icon button (then `tooltip` names it),
    and `label` with an empty `icon` is a word alone (`BarAction("", "Save", primary=True)`).

    `primary` is the key action (one per bar), `danger` is red ink, `active`
    marks a toggle that is on. `note` is a short annotation beside an icon
    (Camera's flash "A", the timer's "3s": v70 `.cmt small`).
    """

    icon: str
    label: str | None = None
    on_activate: Callable[[], None] | None = None
    tooltip: str | None = None
    primary: bool = False
    danger: bool = False
    sensitive: bool = True
    active: bool = False
    filled: bool = False  # quiet filled action in an inset card or utility panel
    note: str | None = None
    record: bool = False  # the record key: red, white words, its glyph filled (v70 Memos' Done, .mestop)
    #: v71: the panel this button grows the bar into (a widget, or a callable that makes one). Tapping the
    #: button again folds it; the button is the raised chip while its panel is open.
    panel: object = None
    #: Replace this panel trigger with a Close icon while open (v71 Memos New).
    panel_close: bool = False
    #: The panel's name for `ActionCenter.grow` (default: the icon).
    key: str | None = None
    #: A lone star is the favourite (yellow, no chip, v71 .cfavb); False keeps the toggle's raised chip
    #: (v71 Charlie's phone Flag, .ib.on).
    favourite: bool = True
    #: A menu that hangs from the button instead of growing the bar (rows: MenuItem, heading, None; or a
    #: callable returning them): v71 Charlie's desktop "↩ All ⌄", a menu under the chip (crPop).
    menu: object = None
    #: The menu's width: None (the kit's 220 at least), "narrow" (v71 crPop's 208) or "wide".
    menu_width: str | None = None
    #: v71: on a phone a button with an icon shows its icon alone, 48 square; a labelled key that keeps its
    #: words there (Contacts' Edit) says so.
    keep_label: bool = False
    #: v71: a place or view picker in the bar (Tasks' "Today ⌄", Calendar's "Month ⌄"): its lead, its words in
    #: 15/650 and a chevron; it keeps its words on a phone and is raised while its panel is open.
    dropdown: bool = False
    #: A compact collection picker uses tighter spacing and main ink (v71 Photos); "chip" is the context
    #: chip as a switch (v71 Charlie's desktop "↩ All ⌄", .fbar .ctxc).
    dropdown_size: str = "regular"
    #: A face before the words in place of the glyph (a list's colour dot: any widget).
    lead: object = None
    #: v71: in a bar that fills the phone width (`show_bar(fill=True)`) a labelled key that shares it too
    #: (Valet's Cancel | Install).
    fill: bool = False
    #: v71: a count in the button's top-right corner (Weather's alerts bell): 16 tall, amber; 0 hides it.
    badge: int | None = None
    #: The count's tone: "amber" (Weather's alerts), "red" (Phone's voicemail, Depot's updates) or "accent" (Charlie's unread).
    badge_tone: str = "amber"
    #: Inline counts follow the words of a dropdown instead of floating at its corner.
    badge_placement: str = "corner"
    #: A typographic glyph (Aa) occupying the same target as an icon.
    text_glyph: bool = False

    def __post_init__(self) -> None:
        if self.badge_placement not in ("corner", "inline") or (self.badge_placement == "inline" and not self.dropdown):
            raise ValueError("an inline badge belongs to a dropdown; placement is corner or inline")
        if self.text_glyph and (self.icon or not self.label):
            raise ValueError("a text glyph uses a label without an icon")
        if not self.label and not self.tooltip:
            raise ValueError(f"an icon-only action needs a tooltip to name it ({self.icon!r})")
        if not self.icon and not self.label:
            raise ValueError("an action without an icon is a word: give it a label")


@dataclass
class BarChip:
    """The context chip at the start of a bar ("3 photos", "Your message"), with an optional ×.

    `lead` puts a face before the words: a Person or PersonAvatar, a
    BarThumbnail(paintable, duration=) or an app icon name; `meta` is a quiet
    second word ("PID 1234"). A chip with either is the selected subject (v70
    `.msubj`, `.ctxc`): capped at 240, and it reads "Selected: …" to a screen
    reader (bar_items draws it).
    """

    label: str
    icon: str | None = None
    on_dismiss: Callable[[], None] | None = None
    lead: object = None
    meta: str | None = None
    #: A flat identity with stacked title/detail (v71 Depot); subject preserves the context chip.
    presentation: str = "subject"
    live: bool = False  # recording: red, a pulsing dot for its glyph, `meta` is the running time (v70 .ctxc.merec)
    #: v71: the live chip as an inset well taking the row's spare width (Memos recording; automatic on a phone).
    well: bool = False


@dataclass
class BarContext:
    """The line over a double-height bar. `{}` in `text` is where `emphasis` goes, in bold."""

    icon: str
    text: str
    emphasis: str | None = None
    on_dismiss: Callable[[], None] | None = None
    #: Clearing is ✕, never ✓ (v71 rule 11): ✓ says something was completed, ✕ says never mind.
    clear_icon: str = "x"
    clear_label: str = "Cancel"

    def __post_init__(self) -> None:
        if self.clear_icon in ("check", "check-check", "circle-check"):
            raise ValueError("clearing a selection is ✕ (x), not ✓: ✓ means something was completed")


@dataclass
class BarPrompt:
    """The well that grows into the editor ("Reply to Priya and Nora…").

    As a two-row bar's foot (`show_bar(..., entry=BarPrompt(...))`, v71 Charlie's phone thread bar),
    `icon` leads its words and `tools` (BarActions: Attach) follow it on the same line.
    """

    text: str
    on_activate: Callable[[], None] | None = None
    icon: str | None = None
    tools: Sequence[object] = ()


class _Marker:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


#: A hairline between groups of actions.
SEPARATOR = _Marker("SEPARATOR")
RULE = _Marker("RULE")
#: Flexible room: what follows sits at the end.
SPACER = _Marker("SPACER")


def _markup(text: str, emphasis: str | None) -> str:
    if emphasis is None or "{}" not in text:
        return GLib.markup_escape_text(text)
    head, tail = text.split("{}", 1)
    return (GLib.markup_escape_text(head) + "<b>" + GLib.markup_escape_text(emphasis) + "</b>"
            + GLib.markup_escape_text(tail))


#: Items other kit modules add to a bar (BarEntry, BarSearch, …): kind → factory(item, size).
_ITEM_FACTORIES: dict[type, Callable[[object, str], Gtk.Widget]] = {}


def register_item(kind: type, factory: Callable[[object, str], Gtk.Widget]) -> None:
    """Let a kit module put its own item in a bar: `factory(item, size)` returns its widget.

    The widget may set `bar_wide = True` (the bar takes the wide width, as with
    a prompt) or `bar_span = (max_width, side_margin[, phone_side_margin])` (the wide bar at its own
    token width, e.g. a Tasks entry at min(600, 100% − 32)) or
    `bar_phone_wide = True` (as wide as its actions, but the full span on a
    phone, as a search well), and may offer `carry_draft() -> str | None` and
    `receive_draft(text)` so its text carries into the editor on grow() and
    back on fold().
    """
    _ITEM_FACTORIES[kind] = factory


def make_control(item: object, *, size: str = "bar") -> Gtk.Widget:
    """The widget for one bar item. `size` is "bar", "tool", "bubble", or "caption" (52px icon-over-label footer action)."""
    if isinstance(item, BarWidget):
        if item.widget.get_parent() is not None:
            raise ValueError("the hosted bar widget still has a parent")
        item.widget.add_css_class("in-bar")
        item.widget.set_valign(Gtk.Align.CENTER)
        if not hasattr(item.widget, "bar_item"):
            item.widget.bar_item = item
        return item.widget
    for kind in type(item).__mro__:
        factory = _ITEM_FACTORIES.get(kind)
        if factory is not None:
            widget = factory(item, size)
            widget.bar_item = item
            return widget
    if item is SEPARATOR or item is RULE:
        rule = Gtk.Box(valign=Gtk.Align.CENTER)
        rule.add_css_class("lumaui-bar-rule")
        # In an app window's bar v70's rule is an invisible 6 px spacer (computed on #p-bar), so
        # grouped items sit 14 apart; tool rows and the bubble draw the 1 px rule (.cr2tools .vr).
        if item is RULE:
            rule.add_css_class("explicit-rule")
        elif size == "bar":
            rule.add_css_class("space")
        return rule
    if item is SPACER:
        spacer = Gtk.Box(hexpand=True)
        spacer.bar_spacer = True
        return spacer
    if isinstance(item, BarChip) and item.presentation not in ("subject", "identity"):
        raise ValueError("chip presentation is subject or identity")
    if isinstance(item, BarChip) and item.presentation == "identity":
        from .bar_items import identity_chip
        widget = identity_chip(item)
        widget.bar_item = item
        return widget
    if isinstance(item, BarChip) and item.live:
        chip = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.STATUS)
        chip.add_css_class("lumaui-bar-chip")
        chip.add_css_class("live")
        if item.well:
            chip.add_css_class("well")
            chip.set_hexpand(True)
        dot = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        dot.add_css_class("lumaui-live-dot")
        chip.append(dot)
        chip.append(Gtk.Label(label=item.label))
        if item.meta:
            spacer = Gtk.Box(hexpand=True, visible=item.well)  # in the well the time sits at its right
            chip.append(spacer)
            chip.live_spacer = spacer
            time = Gtk.Label(label=item.meta)
            time.add_css_class("lumaui-live-time")
            chip.append(time)
            chip.time_label = time
        chip.update_property([Gtk.AccessibleProperty.LABEL], [f"{item.label} {item.meta or ''}".strip()])
        chip.bar_item = item
        return chip
    if isinstance(item, BarChip) and (item.lead is not None or item.meta):
        from .bar_items import subject_chip
        widget = subject_chip(item)
        widget.bar_item = item
        return widget
    if isinstance(item, BarChip):
        chip = Gtk.Box(valign=Gtk.Align.CENTER)
        chip.add_css_class("lumaui-bar-chip")
        if item.icon:
            glyph = icons.image(item.icon)
            glyph.add_css_class("lumaui-bar-chip-icon")
            chip.append(glyph)
        text = Gtk.Label(label=item.label, ellipsize=Pango.EllipsizeMode.END, max_width_chars=30)
        chip.append(text)
        if item.on_dismiss is not None:
            # Clearing a selection is ✕ (v71 rule 11): never mind, not done.
            close = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text="Clear selection")
            close.add_css_class("lumaui-bar-chip-close")
            close.set_child(icons.image("x"))
            close.remove_css_class("image-button")  # a LumaUI part, not the legacy icon-button look
            close.update_property([Gtk.AccessibleProperty.LABEL], ["Clear selection"])
            close.connect("clicked", lambda _b: item.on_dismiss())
            chip.append(close)
        return chip
    if isinstance(item, BarPrompt):
        raise TypeError("a prompt belongs to the action center (show_bar), not a free control")
    if not isinstance(item, BarAction):
        raise TypeError(f"not a bar item: {item!r}")
    if item.dropdown_size not in ("regular", "compact", "view", "chip"):
        raise ValueError("dropdown_size is regular, compact, view or chip")
    button = Gtk.Button(valign=Gtk.Align.CENTER, sensitive=item.sensitive)
    button.add_css_class("lumaui-bar-button")
    button.add_css_class(size)
    if item.text_glyph:
        button.add_css_class("text-glyph")
    if item.keep_label or item.dropdown:
        button.add_css_class("keep-label")
    if item.dropdown:
        button.add_css_class("dropdown")
        if item.dropdown_size != "regular":
            button.add_css_class(f"{item.dropdown_size}-picker")
        line = Gtk.Box(halign=Gtk.Align.CENTER)
        line.add_css_class("lumaui-bar-button-content")
        if item.lead is not None:
            _detach(item.lead)
            item.lead.set_valign(Gtk.Align.CENTER)
            line.append(item.lead)
        elif item.icon:
            line.append(icons.image(item.icon))
        words = Gtk.Label(label=item.label or item.tooltip or "", ellipsize=Pango.EllipsizeMode.END)
        words.add_css_class("lumaui-bar-button-label")
        line.append(words)
        chevron = icons.image("chevron-down")
        chevron.add_css_class("lumaui-bar-dropdown-chevron")
        line.append(chevron)
        button.set_child(line)
        button.bar_dropdown_words = words
        button.update_property([Gtk.AccessibleProperty.LABEL], [item.tooltip or item.label or ""])
        if item.tooltip:
            button.set_tooltip_text(item.tooltip)
        _connect(button, item)
        button.bar_item = item
        return _decorate(button, item)
    for flag, name in ((item.primary, "primary"), (item.danger, "danger"), (item.active, "on"),
                       (item.filled, "filled"), (item.record, "record")):
        if flag:
            button.add_css_class(name)
    if not item.icon:  # a word only ("Save", "Done"): v70 .bt with no glyph
        button.add_css_class("text")
        button.set_child(Gtk.Label(label=item.label))
        button.update_property([Gtk.AccessibleProperty.LABEL], [item.tooltip or item.label])
        if item.tooltip:
            button.set_tooltip_text(item.tooltip)
        _connect(button, item)
        button.bar_item = item
        return _decorate(button, item)
    if item.icon == "record-dot":
        # v71 Memos' Record key: a 10 px filled red dot before the words, on the key's own surface.
        glyph = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        glyph.add_css_class("lumaui-record-dot")
    else:
        glyph = icons.image(f"{item.icon}-filled" if item.record and item.icon in ("square", "circle") else item.icon)
    if item.icon == "star" and not item.label and item.favourite:
        _favourite_star(button, glyph)
    button.remove_css_class("image-button")  # a LumaUI part, never the legacy outlined icon button
    if item.label:
        line = Gtk.Box(halign=Gtk.Align.CENTER,
                       orientation=Gtk.Orientation.VERTICAL if size == "caption" else Gtk.Orientation.HORIZONTAL)
        line.add_css_class("lumaui-bar-button-content")
        line.append(glyph)
        words = Gtk.Label(label=item.label)
        words.add_css_class("lumaui-bar-button-label")
        line.append(words)
        button.set_child(line)
        button.add_css_class("labelled")
        button.bar_words = words
    else:
        button.add_css_class("icon")
        if item.note:
            button.add_css_class("noted")
            line = Gtk.Box(halign=Gtk.Align.CENTER)
            line.add_css_class("lumaui-bar-button-noted")
            line.append(glyph)
            note = Gtk.Label(label=item.note)
            note.add_css_class("lumaui-bar-note")
            line.append(note)
            button.set_child(line)
        else:
            button.set_child(glyph)
        # GTK adds its generic image-button styling when the only child is an
        # image. LumaUI's own bar/inline styles provide the control treatment.
        button.remove_css_class("image-button")
    name = item.tooltip or item.label or item.icon
    if item.note:
        name = f"{name}, {item.note}"
    if item.tooltip:
        button.set_tooltip_text(item.tooltip)
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    if item.active:
        button.update_state([Gtk.AccessibleState.PRESSED], [int(Gtk.AccessibleTristate.TRUE)])
    button.remove_css_class("image-button")  # GTK adds it back when an image becomes the child
    _connect(button, item)
    button.bar_item = item
    return _decorate(button, item)


def _favourite_star(button: Gtk.Button, glyph: Gtk.Image) -> None:
    """v71 `.cphbar .cfavb.on`: a lit star in a bar is the favourite, the filled yellow star with no chip
    (the CSS reads `.favourite.on`); the glyph follows the `on` class whenever the app sets it."""
    button.add_css_class("favourite")

    def sync(*_args) -> None:
        glyph.set_from_icon_name(icons.icon_name("star-filled" if button.has_css_class("on") else "star"))
    button.connect("notify::css-classes", sync)
    sync()


def _decorate(button: Gtk.Button, item: BarAction) -> Gtk.Widget:
    """v71 extras on a bar button: the shared-width flag, the red key and a corner count."""
    if item.fill:
        button.add_css_class("fill")
    if item.primary and item.danger:
        button.add_css_class("destructive")  # the red key: Remove, Delete for good (v71 Valet)
    if item.badge is None:
        return button
    from .content_badges import CountBadge
    inline = item.badge_placement == "inline"
    holder = button if inline else Gtk.Overlay(child=button, valign=Gtk.Align.CENTER)
    if not inline:
        holder.add_css_class("lumaui-bar-badged")
    badge = CountBadge(item.badge or 0, attention=item.badge_tone != "neutral")
    badge.add_css_class("lumaui-bar-corner-badge")
    if item.badge_tone not in ("amber", "red", "accent", "neutral"):
        raise ValueError("a bar badge is amber, red, accent or neutral")
    if item.badge_tone in ("red", "accent", "neutral"):
        badge.add_css_class(item.badge_tone)   # accent: v71 Charlie's unread count (.crunn)
    badge.set_halign(Gtk.Align.CENTER if inline else Gtk.Align.END)
    badge.set_valign(Gtk.Align.CENTER if inline else Gtk.Align.START)
    badge.set_can_target(False)
    badge.set_visible(bool(item.badge))
    if inline:
        badge.add_css_class("inline")
        line = button.get_child()
        line.insert_child_after(badge, button.bar_dropdown_words)
    else:
        holder.add_overlay(badge)
    holder.bar_item = item
    holder.bar_badge = badge
    holder.bar_button = button
    name = item.tooltip or item.label or item.icon
    # v71 names the button for what it does ("Show only unread"); the count is said as its description,
    # and set_badge keeps it current.
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [str(item.badge) if item.badge else ""])
    return holder


def set_badge(widget: Gtk.Widget, count: int | None) -> None:
    """Change the corner count of a bar button made with `BarAction(badge=…)` (0 or None hides it)."""
    badge = getattr(widget, "bar_badge", None)
    if badge is None:
        raise ValueError("this bar button was made without badge=")
    badge.set_count(count or 0)
    badge.set_visible(bool(count))
    button = getattr(widget, "bar_button", None)
    if button is not None:
        button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [str(count) if count else ""])



def _connect(button: Gtk.Button, item: BarAction) -> None:
    """What a tap does: the action, and (v71) growing or folding the bar's panel for it, or its menu."""
    if item.menu is not None:
        if item.panel is not None:
            raise ValueError("a bar action opens a panel or a menu, not both")
        button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])

        def open_menu(_b: Gtk.Button) -> None:
            if item.on_activate is not None:
                item.on_activate()
            from .menus import bar_menu
            rows = item.menu() if callable(item.menu) else item.menu
            bar_menu(button, list(rows), label=item.label or item.tooltip or "Menu", align="center",
                     width=item.menu_width)   # crPop: centred on the key

        button.connect("clicked", open_menu)
    elif item.panel is not None:
        button.update_property([Gtk.AccessibleProperty.HAS_POPUP], [True])
        button.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(False))])

        def toggle(_b: Gtk.Button) -> None:
            if item.on_activate is not None:
                item.on_activate()
            center = button.get_ancestor(ActionCenter)
            if center is None:
                raise ValueError("a bar action with a panel lives in an ActionCenter")
            center.grow(item.key or item.icon or item.label, item.panel, anchor=button)

        button.connect("clicked", toggle)
    elif item.on_activate is not None:
        button.connect("clicked", lambda _b: item.on_activate())


# ── the editor the bar grows into ──────────────────────────────────────────

class ActionEditor(Gtk.Box):
    """The full editor: header, optional fields, tools, the application's content, footer.

    With no `body`, the kit's composer (a text view with `placeholder`) is the
    content and its text is the draft. A custom `body` (a form, photo tools)
    keeps its own state across a fold; `draft` then returns None unless the
    application passes `draft=` to describe it.
    """

    __gtype_name__ = "LumaUIActionEditor"

    def __init__(self, title: str, icon: str, *, summary: str | None = None, summary_emphasis: str | None = None,
                 modes: Sequence[tuple[str, str, str]] = (), mode: str | None = None,
                 on_mode: Callable[[str], None] | None = None,
                 fields: Sequence[tuple[str, Gtk.Widget | str]] = (), tools: Sequence[object] = (),
                 body: Gtk.Widget | None = None, placeholder: str = "Write something",
                 primary: BarAction | None = None, on_discard: Callable[[], None] | None = None,
                 draft: Callable[[], str] | None = None, revert: BarAction | None = None,
                 width: int | None = None, keep_hint: bool = False, submit_on_return: bool = False) -> None:
        """`keep_hint`: a composer's footer keeps its computer metrics on a phone (v71 Charlie's .cr2ft:
        36 keys); its key hint still goes (Nick, 1 Oct: no keyboard hints on phones).

        `submit_on_return` enables unmodified Return for the primary action and its hint;
        Shift+Return retains a newline. The default still requires Ctrl+Return.

        v71 `revert=` (Photos' editor, `.lac.ped`): the modes are a switch in the header beside the tools,
        the footer is Revert to original · Cancel (`on_discard`) · the primary; no trash, no hint. On a phone
        (`.phed`): Cancel · title · Done on top, the body, then the modes, Auto and Revert as tiles. `width`
        is the editor's width in a window (default 720; 640 with `revert`)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("lumaui-ac-editor-content")
        self.keep_hint = keep_hint
        self.submit_on_return = submit_on_return
        if keep_hint:
            self.add_css_class("keeps-foot")
        self.revert, self.width = revert, width if width is not None else (640 if revert is not None else None)
        self.title, self.icon = title, icon
        self.modes, self.mode, self.on_mode = list(modes), mode, on_mode
        self.primary, self.on_discard, self._draft_fn = primary, on_discard, draft
        self.center: "ActionCenter | None" = None
        self._fields: dict[str, Gtk.Widget] = {}

        header = Gtk.Box()
        header.add_css_class("lumaui-ac-header")
        self.mode_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self.mode_button.add_css_class("lumaui-ac-mode")
        if not self.modes:
            self.mode_button.add_css_class("static")
            self.mode_button.set_can_focus(False)
            self.mode_button.set_can_target(False)
        else:
            self.mode_button.connect("clicked", self._choose_mode)
            self.mode_button.set_tooltip_text("Change")
        self._fill_mode(title, icon)
        header.append(self.mode_button)
        self.summary = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.summary.add_css_class("lumaui-ac-summary")
        self.set_summary(summary, summary_emphasis)
        header.append(self.summary)
        self.append(header)

        for label, widget in fields:
            row = Gtk.Box()
            row.add_css_class("lumaui-ac-field")
            caption = Gtk.Label(label=label, xalign=0)
            caption.add_css_class("lumaui-ac-field-label")
            row.append(caption)
            if isinstance(widget, str):
                widget = Gtk.Entry(placeholder_text=widget, hexpand=True)
            widget.set_hexpand(True)
            widget.add_css_class("lumaui-ac-field-input")
            widget.update_relation([Gtk.AccessibleRelation.LABELLED_BY], [Gtk.AccessibleList.new_from_list([caption])])
            row.append(widget)
            self._fields[label] = widget
            self.append(row)

        if tools:
            row = Gtk.Box()
            row.add_css_class("lumaui-ac-tools")
            for item in tools:
                row.append(make_control(item, size="tool"))
            self.append(row)

        self.text_view: Gtk.TextView | None = None
        if body is None:
            metrics = tokens.ACTION_CENTER
            self.text_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                          left_margin=metrics["text_padding_x"], right_margin=metrics["text_padding_x"],
                                          top_margin=metrics["text_padding_top"],
                                          bottom_margin=metrics["text_padding_bottom"])
            self.text_view.add_css_class("lumaui-ac-text")
            self.text_view.update_property([Gtk.AccessibleProperty.PLACEHOLDER, Gtk.AccessibleProperty.LABEL],
                                           [placeholder, placeholder])
            self._placeholder = Gtk.Label(label=placeholder, xalign=0, valign=Gtk.Align.START,
                                          halign=Gtk.Align.START, can_target=False)
            self._placeholder.add_css_class("lumaui-ac-placeholder")
            overlay = Gtk.Overlay(child=self.text_view)
            overlay.add_overlay(self._placeholder)
            self.text_view.get_buffer().connect("changed", self._text_changed)
            body = overlay
            self.add_css_class("composer")
        self.body = body
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                           vexpand=True)
        self.scroller.add_css_class("lumaui-ac-body")
        self.scroller.set_min_content_height(tokens.ACTION_CENTER["text_min_height"] if self.text_view else 0)
        self.scroller.set_child(body)
        self.append(self.scroller)

        if revert is not None:
            self._build_edit(title, icon, header, tools, primary, revert)
            return
        footer = Gtk.Box()
        footer.add_css_class("lumaui-ac-footer")
        self.discard_button = make_control(BarAction("trash-2", tooltip="Discard", on_activate=self._discard))
        footer.append(self.discard_button)
        footer.append(Gtk.Box(hexpand=True))
        hint = Gtk.Label(label="Esc to fold" + ((" · Return to " + (primary.label or "submit").lower())
                              if primary and submit_on_return else " · Ctrl Return" if primary else ""))
        hint.add_css_class("lumaui-ac-hint")
        if keep_hint:
            hint.add_css_class("kept")
        footer.append(hint)
        self.hint = hint  # v71: keyboard hints are hidden on a phone
        self.primary_button = None
        if primary is not None:
            primary.primary = True
            self.primary_button = make_control(primary)
            footer.append(self.primary_button)
        self.append(footer)

    # ── v71: the edit layout (revert=) ─────────────────────────────────────

    def _build_edit(self, title: str, icon: str, header: Gtk.Box, tools: Sequence[object],
                    primary: BarAction | None, revert: BarAction) -> None:
        self.add_css_class("edit")
        self._phone_parts: list[Gtk.Widget] = []
        self._desk_parts: list[Gtk.Widget] = [header]
        self.summary.set_visible(False)
        self.summary.set_hexpand(False)
        self.mode_button.add_css_class("static")
        self.mode_button.add_css_class("chip")
        self.mode_button.set_can_focus(False)
        self.mode_button.set_can_target(False)
        self._fill_mode(title, icon, chevron=False)
        self.mode_switch = None
        if self.modes:
            from .structure_placement import ModeSwitch
            self.mode_switch = ModeSwitch([(k, label) for k, label, _i in self.modes], current=self.mode,
                                          on_change=self._mode_chosen, label="Tools", compact=True)
            header.append(self.mode_switch)
        header.append(Gtk.Box(hexpand=True))
        for item in tools:
            if item is SEPARATOR or item is SPACER:
                continue
            header.append(make_control(item, size="tool"))
        # The tool row the base editor builds is the header's now.
        tool_row = header.get_next_sibling()
        if tool_row is not None and tool_row.has_css_class("lumaui-ac-tools"):
            self.remove(tool_row)
        footer = Gtk.Box()
        footer.add_css_class("lumaui-ac-footer")
        self.revert_button = Gtk.Button(label=revert.label or revert.tooltip or "Revert to original",
                                        sensitive=revert.sensitive, valign=Gtk.Align.CENTER)
        self.revert_button.add_css_class("lumaui-text-button")
        self.revert_button.add_css_class("lumaui-ac-cancel")
        self.revert_button.connect("clicked", lambda _b: revert.on_activate() if revert.on_activate else None)
        footer.append(self.revert_button)
        footer.append(Gtk.Box(hexpand=True))
        self.cancel_button = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        self.cancel_button.add_css_class("lumaui-text-button")
        self.cancel_button.add_css_class("lumaui-ac-cancel")
        self.cancel_button.connect("clicked", lambda _b: self._discard())
        footer.append(self.cancel_button)
        self.discard_button = self.cancel_button
        self.hint = Gtk.Label(visible=False)
        self.primary_button = None
        if primary is not None:
            primary.primary = True
            self.primary_button = make_control(primary)
            footer.append(self.primary_button)
        self.append(footer)
        self._desk_parts.append(footer)
        # The phone: Cancel · title · Done on top; the modes, Auto and Revert as tiles at the foot.
        top = Gtk.CenterBox(visible=False)
        top.add_css_class("lumaui-ac-edit-top")
        cancel = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        cancel.add_css_class("lumaui-ac-edit-cancel")
        cancel.connect("clicked", lambda _b: self._discard())
        top.set_start_widget(cancel)
        name = Gtk.Label(label=title)
        name.add_css_class("lumaui-ac-edit-title")
        top.set_center_widget(name)
        if primary is not None:
            done = Gtk.Button(label=primary.label or "Done", valign=Gtk.Align.CENTER)
            done.add_css_class("lumaui-ac-edit-done")
            done.connect("clicked", lambda _b: primary.on_activate() if primary.on_activate else None)
            top.set_end_widget(done)
        self.prepend(top)
        foot = Gtk.Box(visible=False, homogeneous=False)
        foot.add_css_class("lumaui-ac-edit-tabs")
        self._tiles: dict[str, Gtk.Button] = {}

        def tile(glyph: str, words: str, callback, *, sensitive: bool = True) -> Gtk.Button:
            button = Gtk.Button(hexpand=True, sensitive=sensitive)
            button.add_css_class("lumaui-ac-edit-tab")
            stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            stack.append(icons.image(glyph))
            stack.append(Gtk.Label(label=words))
            button.set_child(stack)
            button.update_property([Gtk.AccessibleProperty.LABEL], [words])
            button.connect("clicked", lambda _b: callback())
            return button

        for key, label, mode_icon in self.modes:
            button = tile(mode_icon, label, lambda k=key: self._mode_chosen(k))
            lumaui.set_css_class(button, "on", key == self.mode)
            self._tiles[key] = button
            foot.append(button)
        if self.modes:
            rule = Gtk.Box()
            rule.add_css_class("lumaui-ac-edit-sep")
            foot.append(rule)
        for item in tools:
            if isinstance(item, BarAction):
                foot.append(tile(item.icon, item.label or item.tooltip or "",
                                 lambda i=item: i.on_activate() if i.on_activate else None, sensitive=item.sensitive))
        self.revert_tile = tile(revert.icon or "rotate-ccw", "Revert",
                                lambda: revert.on_activate() if revert.on_activate else None, sensitive=revert.sensitive)
        foot.append(self.revert_tile)
        self.append(foot)
        self._phone_parts = [top, foot]

    def set_revertable(self, revertable: bool) -> None:
        """Whether there is anything to revert (the button and the tile)."""
        if getattr(self, "revert_button", None) is not None:
            self.revert_button.set_sensitive(revertable)
            self.revert_tile.set_sensitive(revertable)

    def set_phone(self, phone: bool) -> None:
        """The edit layout's phone shape (the action center calls this)."""
        lumaui.set_css_class(self, "phone", phone)
        for part in getattr(self, "_desk_parts", ()):
            part.set_visible(not phone) if self.revert is not None else None
        for part in getattr(self, "_phone_parts", ()):
            part.set_visible(phone)

    def _mode_chosen(self, key: str) -> None:
        self.mode = key
        for name, button in getattr(self, "_tiles", {}).items():
            lumaui.set_css_class(button, "on", name == key)
        if getattr(self, "mode_switch", None) is not None and self.mode_switch.current != key:
            self.mode_switch.set_current(key)
        if self.on_mode is not None:
            self.on_mode(key)

    # ── public API ────────────────────────────────────────────────────────

    def field(self, label: str) -> Gtk.Widget:
        """The input of the field row named `label`."""
        return self._fields[label]

    def set_summary(self, text: str | None, emphasis: str | None = None) -> None:
        self.summary.set_markup(_markup(text, emphasis) if text else "")
        self.summary.set_visible(bool(text))

    def set_mode(self, key: str) -> None:
        for mode_key, label, icon in self.modes:
            if mode_key == key:
                if self.revert is not None:  # the edit layout: the title stays, the switch and tiles follow
                    self.mode = key
                    for name, button in self._tiles.items():
                        lumaui.set_css_class(button, "on", name == key)
                    if self.mode_switch is not None:
                        self.mode_switch.set_current(key)
                    return
                self.mode, self.title, self.icon = key, label, icon
                self._fill_mode(label, icon)
                return
        raise KeyError(key)

    @property
    def draft(self) -> str | None:
        """What has been written so far: the composer's text, or the application's own description."""
        if self._draft_fn is not None:
            return self._draft_fn()
        if self.text_view is not None:
            buffer = self.text_view.get_buffer()
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        return None

    def clear(self) -> None:
        """Forget the draft (after Send or Discard)."""
        if self.text_view is not None:
            self.text_view.get_buffer().set_text("")

    def focus_content(self) -> None:
        target = self.text_view or _first_focusable(self.body) or self.primary_button
        if target is not None:
            target.grab_focus()

    def run_primary(self) -> bool:
        if self.primary is None or self.primary_button is None or not self.primary_button.is_sensitive():
            return False
        if self.primary.on_activate is not None:
            self.primary.on_activate()
        return True

    # ── internals ─────────────────────────────────────────────────────────

    def _fill_mode(self, title: str, icon: str, *, chevron: bool = True) -> None:
        line = Gtk.Box()
        line.add_css_class("lumaui-ac-mode-content")
        glyph = icons.image(icon)
        glyph.add_css_class("lumaui-ac-mode-icon")
        line.append(glyph)
        line.append(Gtk.Label(label=title))
        if self.modes and chevron and self.revert is None:
            chevron = icons.image("chevron-down")
            chevron.add_css_class("lumaui-ac-mode-chevron")
            line.append(chevron)
        self.mode_button.set_child(line)
        self.mode_button.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def _choose_mode(self, button: Gtk.Button) -> None:
        from .action_bubble import FloatingMenu, MenuItem

        def pick(key: str) -> None:
            self.set_mode(key)
            if self.on_mode is not None:
                self.on_mode(key)

        rows = [MenuItem(label, icon=icon, selected=key == self.mode, on_activate=lambda k=key: pick(k))
                for key, label, icon in self.modes]
        FloatingMenu(rows, label="Mode").popup(button)

    def _text_changed(self, buffer: Gtk.TextBuffer) -> None:
        self._placeholder.set_visible(buffer.get_char_count() == 0)
        if self.center is not None:
            self.center._cap_text()

    def _discard(self) -> None:
        if self.center is not None:
            self.center.discard()
        else:
            self.clear()


def _first_focusable(widget: Gtk.Widget | None) -> Gtk.Widget | None:
    if widget is None:
        return None
    if widget.get_focusable() and widget.get_can_focus() and widget.is_sensitive() and widget.get_visible():
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _first_focusable(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


# ── the action center ──────────────────────────────────────────────────────

class ActionCenter(Gtk.Box):
    """The floating bar of one island. See the module docstring for the states and API."""

    __gtype_name__ = "LumaUIActionCenter"
    __gsignals__ = {"state-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
                    # v71: the panel the bar has grown into ("" when it folds)
                    "panel-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, editor: ActionEditor | None = None, *, phone_grown_inset: int | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.FILL, valign=Gtk.Align.FILL,
                         visible=False)
        self.add_css_class("lumaui-action-center")
        self._state = "hidden"
        self._host: LayerHost | None = None
        self._phone = False
        self._prompt: BarPrompt | None = None
        self.prompt_button: Gtk.Button | None = None
        self._text_cap = 0
        self._bar_span: tuple[int, int] | None = None
        self._phone_wide = False
        if phone_grown_inset is not None and (not isinstance(phone_grown_inset, int) or phone_grown_inset < 0):
            raise ValueError("phone_grown_inset must be a nonnegative integer or None")
        self._phone_grown_inset = phone_grown_inset
        self.editor: ActionEditor | None = None
        # v71: the grown panel, the held row, the field that replaces the row, More, the safe area.
        self._panel_key: str | None = None
        self._panel_anchor: Gtk.Widget | None = None
        self._panel_on_fold: Callable[[], None] | None = None
        self._entry: Gtk.Widget | None = None
        self._more_items: list[object] = []
        self._more_button: Gtk.Widget | None = None
        self._overflow: list[Gtk.Widget] = []
        self._fit_width = -1
        self._fit_pending = False
        self._held: dict | None = None
        self._search = None
        self._scrollers: list[Gtk.Widget] = []
        self._safe: dict[int, tuple[Gtk.Widget, str, int]] = {}
        self._safe_auto = True
        self._safe_pending = False
        self._fill = False
        self._panel_only: Callable[[], None] | None = None
        self._tab_switch = None
        self._tab_bar = None

        # The bar (and its double-height context line).
        self.bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.TOOLBAR)
        self.bar.add_css_class("lumaui-ac-bar")
        self.bar.update_property([Gtk.AccessibleProperty.LABEL], ["Actions"])
        # v71: the panel the bar grows into, above its own row, inside the same glass (.fexp).
        self.panel = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                        visible=False)
        self.panel.add_css_class("lumaui-ac-panel")
        # v71: what is held across views (Move) is the bar's second row (.fhold).
        self.hold_row = Gtk.Box(visible=False)
        self.hold_row.add_css_class("lumaui-ac-hold")
        self.context_line = Gtk.Box(visible=False)
        self.context_line.add_css_class("lumaui-ac-context")
        # v71 Tide: an app row (the mini player) above the bar's row, in the same glass, under its own hairline.
        self.head_row = Gtk.Box(visible=False)
        self.head_row.add_css_class("lumaui-ac-head")
        self.bar_row = Gtk.Box()
        self.bar_row.add_css_class("lumaui-ac-row")
        # v71: a two-row bar's always-there field under a hairline (Tasks' and Calendar's add field).
        self.foot_row = Gtk.Box(visible=False)
        self.foot_row.add_css_class("lumaui-ac-foot")
        self._replaced: Gtk.Widget | None = None
        # v71: a field that is the point of a panel (or the bar's search) replaces the row; ✕ gives it back.
        self.entry_row = Gtk.Box(visible=False)
        self.entry_row.add_css_class("lumaui-ac-row")
        self.entry_row.add_css_class("entry")
        for part in (self.panel, self.hold_row, self.context_line, self.head_row, self.bar_row, self.foot_row,
                     self.entry_row):
            self.bar.append(part)
        panel_keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        panel_keys.connect("key-pressed", self._panel_key_pressed)
        self.bar.add_controller(panel_keys)

        # The editor card.
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False,
                            accessible_role=Gtk.AccessibleRole.GROUP)
        self.card.add_css_class("lumaui-ac-editor")
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._editor_key)
        self.card.add_controller(keys)

        # Split: two bars.
        self.split = Gtk.Box(visible=False)
        self.split.add_css_class("lumaui-ac-split")

        for part in (self.bar, self.card, self.split):
            self.append(part)
        if editor is not None:
            self.set_editor(editor)

    # ── placing it ────────────────────────────────────────────────────────

    def set_phone_grown_inset(self, pixels: int | None) -> None:
        """Override the grown phone panel gutter; None restores automatic sizing.

        This does not stretch the resting toolbar or alter desktop sizing.
        The panel retains its resting toolbar's minimum width.
        """
        if pixels is not None and (not isinstance(pixels, int) or pixels < 0):
            raise ValueError("phone_grown_inset must be a nonnegative integer or None")
        self._phone_grown_inset = pixels
        self._queue_fit()
        self.queue_resize()

    def get_phone_grown_inset(self) -> int | None:
        return self._phone_grown_inset

    def attach(self, region: Gtk.Widget, *, over: str = "island", inset: str = "island",
               safe_area: bool = True) -> "ActionCenter":
        """Float at the foot of `region`: a LayerHost/ToastHost, or a widget inside one (or a window).

        `over` says what the bar floats over: "island" (Maps, Photos, Notes: v70 .fbar
        in the island) or "frame" (Messages, Terminal: a bar on the window body, flat,
        in the frame's inks). Only light mode differs.
        """
        if over not in ("island", "frame"):
            raise ValueError('an action center floats over the "island" or the "frame"')
        # `inset="edge"`: a bar at a pane's very foot (v70 Terminal's #tm-bar, 8 above it), not 24.
        # `inset="tight"`: 16 above the island's foot (v71's base `.fbar { bottom: 16px }`, which Clock keeps
        # where most apps say 24).
        if inset not in ("island", "edge", "tight"):
            raise ValueError('an action center sits the island\'s inset, "tight" or at the "edge"')
        self._edge = inset == "edge"
        self._tight = inset == "tight"
        lumaui.set_css_class(self, "on-frame", over == "frame")
        host = region if isinstance(region, LayerHost) else LayerHost.for_widget(region)
        if self.get_parent() is not None:
            self.get_parent().remove_overlay(self)
        host.add_overlay(self)
        host.set_measure_overlay(self, False)
        if self._host is not host:
            host.connect("get-child-position", self._position)
        self._host = host
        if hasattr(host, "track_bar"):
            host.track_bar(self)
        # v71: on a phone every scroller behind the bar ends 20 above it (`attach_scroller` names more).
        self._safe_auto = safe_area
        lumaui.install(host.get_display())
        return self

    @property
    def host(self) -> LayerHost | None:
        return self._host

    @property
    def state(self) -> str:
        return self._state

    # ── states ────────────────────────────────────────────────────────────

    def set_phone_control_size(self, size: str) -> None:
        """Use regular48px phone controls or small36px composer controls."""
        if size not in ("regular", "small"):
            raise ValueError("phone control size must be regular or small")
        lumaui.set_css_class(self.bar_row, "small-controls", size == "small")
        self._queue_fit()

    def show_bar(self, items: Sequence[object], *, context: BarContext | None = None,
                 modes: Gtk.Widget | None = None, more: Sequence[object] = (), entry: object = None,
                 fill: bool = False, tabs: bool | str | None = None, head: Gtk.Widget | None = None,
                 toolbar: bool = False, phone_control_size: str = "regular", head_inset: bool = True) -> None:
        """The bar, or double height when there is a `context` line over it.

        `modes` (a ModeSwitch) puts the app's places or modes at the start of
        the bar (v70 Clock, Phone, Photos): only the current one keeps its
        label on a phone. `more` is what the bar's ⋯ lists (see `more()`).
        `tabs`: a bar that is only a switch between 3 or more places with icons
        (`modes=` and no items) is the app's tab bar on a phone (v71 phone.js
        `tabBar`: the kit TabBar, an icon over each name, full width); None
        decides by `TabBar.wants_tabs`, False keeps the switch, "compact" keeps
        it as icon-only tabs in the bar (Clock).
        `fill=True` (v71 Memos' page bar, Valet, Tide): on a phone the bar spans the
        16 gutter and its icon buttons (and `BarAction(fill=True)` keys) share it.
        `entry` (a BarEntry, BarSearch or widget) makes a two-row bar: the items,
        then under a hairline the always-there field, full width and the
        bottom-most line (v71 Tasks, Calendar); `set_foot()` swaps it alone.
        `head` (v71 Tide's mini player) is an app widget drawn above the row inside the bar's glass, full
        width, under the bar's hairline; a grown panel rises above it, and a search or entry replaces only
        the row. `set_head()` swaps it alone. A phone's head bar spans the 16 gutter, as `fill` does.
        `head_inset=False` lets a head with its own trailing spacing retain the separator
        without a second padding inset. The default preserves ordinary custom heads.
        A new bar folds a grown panel.
        """
        self.set_phone_control_size(phone_control_size)
        self._fold_panel(quiet=True)
        self._fill = fill
        lumaui.set_css_class(self, "document-toolbar", toolbar)
        self._panel_only = None
        self.bar_row.set_visible(True)
        lumaui.set_css_class(self.bar, "panel-only", False)
        if self._search is not None:
            self._search = None
            lumaui.set_css_class(self, "searching", False)
        if self._entry is not None and self._held is None:
            self._hide_entry_row()
        lumaui.set_css_class(self.head_row, "self-padded", not head_inset)
        self._set_head(head)
        self._overflow = []
        self._more_button = None
        self._more_items = list(more)
        _clear(self.bar_row)
        self._prompt = None
        self.prompt_button = None
        self._set_foot(entry)   # after the prompt is cleared: the foot may be the prompt
        self._tab_switch = None
        self._tab_bar = None
        if modes is not None:
            self.bar_row.append(_modes_item(modes, "bar"))
            if not items and tabs not in (False, "compact") and _places(modes) is not None:
                from .structure_tabs import TabBar
                if tabs is True or TabBar.wants_tabs(_places(modes)):
                    self._tab_switch = modes
        for item in items:
            if isinstance(item, BarPrompt):
                self._prompt = item
                self.prompt_button = self._make_prompt(item)
                self.bar_row.append(self.prompt_button)
            else:
                self.bar_row.append(make_control(item))
        _clear(self.context_line)
        if context is not None:
            glyph = icons.image(context.icon)
            glyph.add_css_class("lumaui-ac-context-icon")
            self.context_line.append(glyph)
            text = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            text.set_markup(_markup(context.text, context.emphasis))
            self.context_line.append(text)
            if context.on_dismiss is not None:
                close = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text=context.clear_label)
                close.add_css_class("lumaui-bar-chip-close")
                close.set_child(icons.image(context.clear_icon))
                close.remove_css_class("image-button")  # a LumaUI part, not the legacy icon-button look
                close.update_property([Gtk.AccessibleProperty.LABEL], [context.clear_label])
                close.connect("clicked", lambda _b: context.on_dismiss())
                self.context_line.append(close)
        self.context_line.set_visible(context is not None)
        self._bar_span = next((child.bar_span for child in _children(self.bar_row)
                               if getattr(child, "bar_span", None)), None)
        lumaui.set_css_class(self.bar, "flat-entry", any(getattr(item, "flat", False) for item in items))
        self._phone_wide = any(getattr(child, "bar_phone_wide", False) for child in _children(self.bar_row))
        wide = (context is not None or self._prompt is not None or self._bar_span is not None
                or self.foot_row.get_visible() or self.head_row.get_visible()
                or any(getattr(child, "bar_wide", False) for child in _children(self.bar_row)))
        lumaui.set_css_class(self.bar, "double", context is not None)
        lumaui.set_css_class(self.bar, "wide", wide)
        # Clock's icon tabs in the bar: the key beside them is 56 wide (v71 .cktabbar > .ckk).
        lumaui.set_css_class(self.bar, "with-tabs", any(child.has_css_class("lumaui-tab-bar")
                                                        for child in _children(self.bar_row)))
        if self._more_items:
            self._ensure_more_button()
        self._set_state("double" if context is not None else "bar")
        self._sync_tabs()
        self._refit()
        self._morph(self.bar)

    def show_split(self, first: Sequence[object], second: Sequence[object]) -> None:
        """Two bars side by side: what is selected, and what to do with it."""
        _clear(self.split)
        for items in (first, second):
            part = Gtk.Box(accessible_role=Gtk.AccessibleRole.TOOLBAR)
            part.add_css_class("lumaui-ac-bar")
            part.add_css_class("lumaui-ac-row")
            for item in items:
                part.append(make_control(item))
            self.split.append(part)
        self._set_state("split")
        self._morph(self.split)

    def set_editor(self, editor: ActionEditor) -> None:
        """The editor the prompt grows into (kept, with its draft, until replaced)."""
        if self.editor is not None and self.editor.get_parent() is self.card:
            self.card.remove(self.editor)
        self.editor = editor
        editor.center = self
        self.card.append(editor)

    def grow(self, key: str | None = None, widget: object = None, *, entry: Gtk.Widget | None = None,
             anchor: Gtk.Widget | None = None, on_fold: Callable[[], None] | None = None,
             close_word: str | None = None, panel_padding: bool = True) -> None:
        """Grow. With no arguments, into the editor (Charlie's reply), restoring the draft.

        v71: `grow(key, widget)` grows the bar into a panel above its own row,
        inside the same glass (⋯, Details, Share, Add, a context menu, a confirm).
        `widget` is a widget or a callable that makes one. Growing the same
        `key` again folds it. `entry` is a field that is the point of the panel:
        it replaces the bar's row (with ✕) and takes focus; a field that is not
        the point goes last inside `widget`. `close_word="Cancel"` draws that word beside the entry in place
        of ✕ (v71 Clock's Add a city: a field that adds something, so ✕ would not say what it cancels). `anchor` (the button that grew it)
        is the raised chip while the panel shows. Grown width: a phone less 12 a
        side, a window min(380, window − 24); the row's layout holds.
        """
        if key is not None or widget is not None:
            self._grow_panel(key or "panel", widget, entry=entry, anchor=anchor, on_fold=on_fold,
                             close_word=close_word)
            self.set_panel_padding(panel_padding)
            return
        if self.editor is None:
            raise ValueError("set_editor() first: the action center has nothing to grow into")
        was = self._state
        if was != "editor":
            self._carry_draft_in()
        self._set_state("editor")
        if was != "editor":
            self.card.remove_css_class("shown")
            lumaui.on_next_frame(self.card, lambda: self.card.add_css_class("shown"))
        self._cap_text()
        GLib.idle_add(lambda: (self.editor.focus_content(), False)[1])

    def set_panel_padding(self, padded: bool) -> None:
        """Set the current panel's menu inset; false lets a form own its padding.

        Each new grow call restores padding unless panel_padding=False is passed.
        """
        lumaui.set_css_class(self.panel, "unpadded", not padded)

    def fold(self) -> None:
        """Back to the bar: fold a grown panel, or the editor (keeping what was written)."""
        if self._panel_key is not None:
            self._fold_panel()
            return
        if self._state != "editor":
            return
        self._set_state("double" if self.context_line.get_visible() else "bar")
        self._refresh_prompt()
        carried = self._carry_draft_out()
        self._morph(self.bar)
        if carried is not None:
            carried.grab_focus()
        elif self.prompt_button is not None:
            self.prompt_button.grab_focus()

    def discard(self) -> None:
        """Forget the draft and fold."""
        if self.editor is not None:
            self.editor.clear()
            if self.editor.on_discard is not None:
                self.editor.on_discard()
        if self._state == "editor":
            self.fold()
        else:
            self._refresh_prompt()

    def hide_bar(self) -> None:
        self._fold_panel(quiet=True)
        self._set_state("hidden")

    # ── v71: the bar grows ────────────────────────────────────────────────

    @property
    def grown(self) -> str | None:
        """The key of the panel the bar has grown into, or None."""
        return self._panel_key

    def fold_panel(self) -> None:
        """Fold the grown panel (a panel row calls this after it acts)."""
        self._fold_panel()

    def more(self, items: Sequence[object]) -> None:
        """⋯ at the end of the row (before the key), listing `items` (MenuItem, BarAction, a heading, None).

        On a phone a bar that doesn't fit also moves its least important items
        into ⋯: single actions leave first, from the end; a switch (views,
        tabs) leaves last; the key, the context chip and the first control stay.
        """
        self._more_items = list(items)
        if self._more_items:
            self._ensure_more_button()
        elif self._more_button is not None and not self._overflow:
            self.bar_row.remove(self._more_button)
            self._more_button = None
        self._refit()

    def hold(self, label: str, icon: str | None = "file-text", on_release: Callable[[], None] | None = None, *,
             kind: str = "Moving", thumbnail: Gdk.Paintable | None = None,
             prompt: str = "Where are we moving this?") -> None:
        """Hold something across views (Move): a second row shows it, with ✕ to let go.

        Until a destination is picked the row says `prompt`; `hold_destination()`
        names the place and the key ("Move to Launch · Move here").
        """
        _clear(self.hold_row)
        face = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.START, overflow=Gtk.Overflow.HIDDEN)
        face.add_css_class("lumaui-ac-hold-face")
        face.set_size_request(44, 44)
        if thumbnail is not None:
            picture = Gtk.Picture(paintable=thumbnail, content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            picture.set_size_request(44, 44)
            face.append(picture)
        elif icon:
            glyph = icons.image(icon)
            glyph.set_halign(Gtk.Align.CENTER)
            glyph.set_valign(Gtk.Align.CENTER)
            glyph.set_size_request(44, 44)
            face.append(glyph)
        self.hold_row.append(face)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        tag = Gtk.Label(label=kind.upper(), xalign=0)
        tag.add_css_class("lumaui-ac-hold-kind")
        name = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("lumaui-ac-hold-name")
        words.append(tag)
        words.append(name)
        self.hold_row.append(words)
        stop = make_control(BarAction("x", tooltip=f"Stop {kind.lower()}", on_activate=self._release_tapped))
        self.hold_row.append(stop)
        self.hold_row.update_property([Gtk.AccessibleProperty.LABEL], [f"{kind}: {label}"])
        self._held = {"label": label, "on_release": on_release, "prompt": prompt}
        self.hold_row.set_visible(True)
        self.hold_destination(None)
        self._apply_width()

    def hold_destination(self, name: str | None, *, icon: str = "folder", action: BarAction | None = None,
                         lead: str = "Move to") -> None:
        """Where the held thing would go: the row names it and the key (None: the prompt)."""
        if self._held is None:
            raise ValueError("hold() first")
        row = Gtk.Box()
        row.add_css_class("lumaui-ac-where")
        place = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        place.add_css_class("lumaui-ac-where-text")
        if name is None:
            ask = Gtk.Label(label=self._held["prompt"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
            ask.add_css_class("lumaui-ac-where-prompt")
            place.append(ask)
        else:
            small = Gtk.Label(label=lead, xalign=0)
            small.add_css_class("lumaui-ac-where-lead")
            line = Gtk.Box()
            line.add_css_class("lumaui-ac-where-name")
            line.append(icons.image(icon))
            line.append(Gtk.Label(label=name, xalign=0, ellipsize=Pango.EllipsizeMode.END))
            place.append(small)
            place.append(line)
        row.append(place)
        if name is not None and action is not None:
            action.primary = True
            action.keep_label = True
            row.append(make_control(action))
        self._show_entry_row(row, close=False)

    def release(self) -> None:
        """Let go of what is held: the bar's own row comes back."""
        if self._held is None:
            return
        self._held = None
        self.hold_row.set_visible(False)
        _clear(self.hold_row)
        self._hide_entry_row()
        self._apply_width()

    def show_panel(self, widget: Gtk.Widget, *, on_dismiss: Callable[[], None] | None = None) -> None:
        """The bar's glass holds `widget` and no row (v71 Messages' held message: reactions over tiles).

        Esc calls `on_dismiss`, which gives the app's bar back (`show_bar`). On a phone it spans the width
        less 16 a side, 6 all round at 26, 34 up, like a full-width composer.
        """
        self.show_bar([])
        self._set_foot(widget)
        self.bar_row.set_visible(False)
        self._panel_only = on_dismiss or (lambda: None)
        lumaui.set_css_class(self.bar, "panel-only", True)
        lumaui.set_css_class(self.bar, "wide", True)
        self._apply_width()

    def _sync_tabs(self) -> None:
        """A lone place switch is the kit TabBar on a phone and the switch again in a window."""
        switch = getattr(self, "_tab_switch", None)
        if switch is None:
            return
        want = self._phone
        if want and self._tab_bar is None:
            from .structure_tabs import TabBar
            places = _places(switch)
            bar = TabBar(places, current=switch.current, on_change=lambda key: _choose_mode(switch, key))
            for key, button in switch.buttons.items():
                badge = getattr(button, "count_badge", None)
                if badge is not None and badge.get_visible() and badge.get_label() not in ("", "0"):
                    bar.set_count(key, int(badge.get_label().replace(",", "") or 0))
            own_select = switch._select

            def select(key: str, *, notify: bool) -> None:  # the app's set_current() moves the tab too
                own_select(key, notify=notify)
                if self._tab_bar is not None and self._tab_bar.current != key:
                    self._tab_bar.set_current(key)

            switch._select = select
            bar.set_hexpand(True)
            self._tab_bar = bar
            switch.set_visible(False)
            self.bar_row.append(bar)
            self.bar.add_css_class("tabs")
        elif not want and self._tab_bar is not None:
            self.bar_row.remove(self._tab_bar)
            self._tab_bar = None
            switch.set_visible(True)
            self.bar.remove_css_class("tabs")

    @property
    def tab_bar(self):
        """The TabBar standing in for a lone place switch on a phone, or None."""
        return self._tab_bar

    def set_side(self, side: int | None) -> None:
        """A computer's bar and editor stand this far in from the island's sides (None: the kit's 20).
        v71 Charlie's thread bar and composer are min(720, 100% − 48): 24."""
        self._side = side
        self._apply_width()

    def set_foot(self, entry: object) -> None:
        """Swap a two-row bar's bottom field (None: one row again) without redrawing the row above."""
        self._set_foot(entry)
        lumaui.set_css_class(self.bar, "wide", self.bar.has_css_class("wide") or entry is not None)
        self._apply_width()

    def set_head(self, head: Gtk.Widget | None) -> None:
        """Swap the app row above the bar's row alone (None: no head) without redrawing the row."""
        self._set_head(head)
        lumaui.set_css_class(self.bar, "wide", self.bar.has_css_class("wide") or head is not None)
        self._apply_width()

    def _set_head(self, head: Gtk.Widget | None) -> None:
        _clear(self.head_row)
        if head is not None:
            _detach(head)
            head.set_hexpand(True)
            self.head_row.append(head)
        self.head_row.set_visible(head is not None)
        lumaui.set_css_class(self.bar, "headed", head is not None)

    def _set_foot(self, entry: object) -> None:
        _clear(self.foot_row)
        if isinstance(entry, BarPrompt):
            self._prompt = entry
            widget = self._make_prompt(entry)
            widget.set_hexpand(True)
            self.foot_row.append(widget)
            for tool in entry.tools:
                self.foot_row.append(make_control(tool))
        elif entry is not None:
            widget = entry if isinstance(entry, Gtk.Widget) else make_control(entry)
            _detach(widget)
            widget.set_hexpand(True)
            self.foot_row.append(widget)
        self.foot_row.set_visible(entry is not None)
        lumaui.set_css_class(self.bar, "two-row", entry is not None)

    def search(self, on_changed: Callable[[str], None] | None = None, *, placeholder: str = "Search",
               collapsed: bool = False, text: str = "", on_activate: Callable[[str], None] | None = None,
               on_close: Callable[[], None] | None = None):
        """Search in the bar (v71 rule 12): "Search", a padded glyph, ✕ only once there is text.

        `collapsed=True` returns a search glyph to put in `show_bar` items: tapped,
        it expands in place. Otherwise the search opens now: the field replaces
        the bar's bottom row (the bottom-most thing, closest to the thumb) with
        ✕, which clears it and gives the row back, as does leaving it empty.
        Returns the BarSearch.
        """
        from .bar_items import BarSearch
        item = BarSearch(placeholder, text=text, on_change=on_changed, on_activate=on_activate,
                         collapsed=collapsed, on_close=on_close)
        if not collapsed:
            self.open_search(item)
        return item

    def open_search(self, item) -> None:
        """Open `item` (a BarSearch) as the field that replaces the bottom row."""
        from .bar_items import BarSearch
        if self._state not in ("bar", "double"):
            raise ValueError("search opens from the bar's row: show_bar() first")
        self._fold_panel(quiet=True)

        def changed(text: str) -> None:
            if item.text != text:
                item.set_text(text)
                if item.on_change is not None:
                    item.on_change(text)

        field = BarSearch(item.placeholder, text=item.text, label=item.label, keep=True, on_change=changed,
                          on_activate=item.on_activate)
        widget = make_control(field)
        field.on_empty_leave = lambda: self.close_search()
        field.on_escape_empty = lambda: self.close_search()
        self._search = (item, field)

        def close_tapped() -> None:
            if field.text:
                field.set_text("")
                changed("")
            self.close_search()

        self._show_entry_row(widget, close=True, on_close=close_tapped, close_label="Close search")
        lumaui.set_css_class(self, "searching", True)
        self._apply_width()
        GLib.idle_add(lambda: (field.focus(), False)[1])

    def close_search(self) -> None:
        """Give the bar its row back after a search."""
        if self._search is None:
            return
        item, _field = self._search
        self._search = None
        self._hide_entry_row()
        lumaui.set_css_class(self, "searching", False)
        self._apply_width()
        if item.on_close is not None:
            item.on_close()

    @property
    def searching(self) -> bool:
        return self._search is not None

    def sheet(self, widget: Gtk.Widget, title: str | None = None, *,
              on_cancel: Callable[[], None] | None = None):
        """An app asking for more (Ari's Add a provider): on a phone it rises from the bar's place
        (16 a side, 34 up, 26 round, up to the window less 120, scrolling, above everything); in a
        window it is a card centred on the window. Returns the BarFrame (`close()` it when done)."""
        from .bar_frame import BarFrame, phone
        where = self._host if self._host is not None else self
        self._fold_panel(quiet=True)
        return BarFrame.present(where, widget, kind="sheet", title=title, on_cancel=on_cancel,
                                centred=not phone(where))

    @property
    def held(self) -> str | None:
        return self._held["label"] if self._held else None

    def attach_scroller(self, scroller: Gtk.Widget) -> None:
        """A scroller behind the bar: on a phone its content ends 20 above the bar, re-measured on change."""
        if scroller not in self._scrollers:
            self._scrollers.append(scroller)
        self._queue_safe_area()

    # ── internals: the grown panel (v71) ─────────────────────────────────

    def _grow_panel(self, key: str, widget: object, *, entry: Gtk.Widget | None,
                    anchor: Gtk.Widget | None, on_fold: Callable[[], None] | None,
                    close_word: str | None = None) -> None:
        if self._state not in ("bar", "double"):
            raise ValueError("the bar grows a panel from its row: show_bar() first")
        if self._panel_key == key:
            self._fold_panel()
            return
        self._fold_panel(quiet=True)
        if entry is not None and self._search is not None:
            self.close_search()
        content = widget() if callable(widget) and not isinstance(widget, Gtk.Widget) else widget
        if content is not None and not isinstance(content, Gtk.Widget):
            raise TypeError("a panel is a widget, or a callable that makes one")
        if anchor is None:
            candidate = self.bar_row.get_first_child()
            while candidate is not None:
                item = getattr(candidate, "bar_item", None)
                if isinstance(item, BarAction) and item.panel is not None and (item.key or item.icon or item.label) == key:
                    anchor = getattr(candidate, "bar_button", candidate)
                    break
                candidate = candidate.get_next_sibling()
        self._panel_key, self._panel_anchor, self._panel_on_fold = key, anchor, on_fold
        if content is not None:
            _detach(content)
            self.panel.set_child(content)
            self.panel.set_visible(True)
            self.panel.remove_css_class("shown")
            lumaui.on_next_frame(self.panel, lambda: self.panel.add_css_class("shown"))
        if entry is not None:
            self._show_entry_row(entry, close=True, close_word=close_word)
        if anchor is not None:
            item = getattr(anchor, "bar_item", None)
            if isinstance(item, BarAction) and item.panel_close:
                anchor._panel_original = (anchor.get_child(), anchor.get_css_classes(), anchor.get_tooltip_text())
                anchor.set_child(icons.image("x"))
                for css in ("labelled", "keep-label", "primary", "danger", "filled", "record", "image-button"):
                    anchor.remove_css_class(css)
                anchor.add_css_class("icon")
                anchor.set_tooltip_text("Close")
                anchor.update_property([Gtk.AccessibleProperty.LABEL], ["Close"])
            anchor.add_css_class("on")
            anchor.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(True))])
        self.add_css_class("grown")
        self._apply_width()
        self._lift_toast()
        self.emit("panel-changed", key)
        target = entry if entry is not None else _first_focusable(content)
        if target is not None:
            GLib.idle_add(lambda: (target.grab_focus(), False)[1])

    def _fold_panel(self, *, quiet: bool = False) -> None:
        if self._panel_key is None:
            return
        anchor, callback = self._panel_anchor, self._panel_on_fold
        self._panel_key = self._panel_anchor = self._panel_on_fold = None
        self.panel.set_visible(False)
        self.panel.set_child(None)
        if self._entry is not None and self._held is None and self._search is None:
            self._hide_entry_row()
        if anchor is not None:
            original = getattr(anchor, "_panel_original", None)
            if original is not None:
                child, classes, tooltip = original
                anchor.set_child(child)
                anchor.set_css_classes(classes)
                anchor.set_tooltip_text(tooltip)
                item = anchor.bar_item
                anchor.update_property([Gtk.AccessibleProperty.LABEL], [item.tooltip or item.label or item.icon])
                del anchor._panel_original
            anchor.remove_css_class("on")
            anchor.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(False))])
        self.remove_css_class("grown")
        self._apply_width()
        self._lift_toast()
        self.emit("panel-changed", "")
        if callback is not None:
            callback()
        if not quiet and anchor is not None and anchor.get_mapped():
            anchor.grab_focus()

    def _lift_toast(self) -> None:
        """A toast showing moves to clear the bar as it grows or folds (v71 rule 8)."""
        from .action_toast import Toast
        host = self._host
        toast = Toast._current.get(id(host)) if host is not None else None
        if toast is not None:
            GLib.idle_add(lambda: (toast._place(host) if toast._host is host else None, False)[1])

    def _show_entry_row(self, field: Gtk.Widget, *, close: bool, on_close: Callable[[], None] | None = None,
                        close_label: str = "Close", close_word: str | None = None) -> None:
        """The field replaces the bar's bottom row (the two-row bar's field, else the row)."""
        _clear(self.entry_row)
        _detach(field)
        field.set_hexpand(True)
        self.entry_row.append(field)
        if close:
            if close_word:
                self.entry_row.append(make_control(BarAction("", close_word, on_activate=on_close or self.fold)))
            else:
                self.entry_row.append(make_control(BarAction("x", tooltip=close_label, on_activate=on_close or self.fold)))
        self._entry = field
        if self._replaced is None:
            self._replaced = self.foot_row if self.foot_row.get_visible() else self.bar_row
            self._replaced.set_visible(False)
        self.entry_row.set_visible(True)

    def _hide_entry_row(self) -> None:
        _clear(self.entry_row)
        self._entry = None
        self.entry_row.set_visible(False)
        if self._replaced is not None:
            self._replaced.set_visible(True)
            self._replaced = None

    def _release_tapped(self) -> None:
        callback = self._held["on_release"] if self._held else None
        self.release()
        if callback is not None:
            callback()

    def _panel_key_pressed(self, _controller, keyval: int, _code: int, _state) -> bool:
        if keyval == Gdk.KEY_Escape and self._panel_key is not None:
            self._fold_panel()
            return True
        if keyval == Gdk.KEY_Escape and self._panel_only is not None:
            self._panel_only()
            return True
        return False

    # ── internals: More (v71) ─────────────────────────────────────────────

    def _ensure_more_button(self) -> Gtk.Widget:
        if self._more_button is not None:
            return self._more_button
        button = make_control(BarAction("ellipsis", tooltip="More", panel=self._more_panel, key="more"))
        button.add_css_class("lumaui-ac-more")
        button.bar_more = True
        key = next((c for c in reversed(_children(self.bar_row))
                    if getattr(getattr(c, "bar_item", None), "primary", False)), None)
        if key is not None:
            before = key.get_prev_sibling()
            if before is None:
                self.bar_row.prepend(button)
            else:
                self.bar_row.insert_child_after(button, before)
        else:
            self.bar_row.append(button)
        self._more_button = button
        return button

    def _more_panel(self) -> Gtk.Widget:
        from .bar_panel import PanelRow, panel_list
        rows: list[object] = []
        for child in self._overflow:
            item = getattr(child, "bar_item", None)
            if isinstance(item, BarAction):
                if item.panel is not None:
                    rows.append(PanelRow(item.label or item.tooltip or item.icon, icon=item.icon or None,
                                         selected=item.active, closes=False,
                                         on_activate=lambda i=item: self._grow_from_more(i)))
                else:
                    rows.append(item)
            elif hasattr(child, "bar_search"):
                rows.append(PanelRow("Search", icon="search", closes=False,
                                     on_activate=lambda c=child: self._search_from_more(c)))
            else:
                for button in _buttons(child):
                    words = _button_words(button)
                    if not words:
                        continue
                    active = button.get_active() if isinstance(button, Gtk.ToggleButton) else button.has_css_class("on")
                    rows.append(PanelRow(words, icon=_button_icon(button), selected=active,
                                         on_activate=lambda b=button: _press(b)))
        if self._overflow and self._more_items:
            rows.append(None)
        rows.extend(self._more_items)
        return panel_list(rows, label="More")

    def _grow_from_more(self, item: BarAction) -> None:
        self._fold_panel(quiet=True)
        self.grow(item.key or item.icon or item.label, item.panel, anchor=self._more_button)

    def _search_from_more(self, widget: Gtk.Widget) -> None:
        self._fold_panel(quiet=True)
        opener = getattr(widget, "open_in_bar", None)
        if callable(opener):
            opener(self)

    def _refit(self) -> None:
        self._fit_width = -1
        self._queue_fit()

    def _queue_fit(self) -> None:
        if not self._fit_pending:
            self._fit_pending = True
            GLib.idle_add(self._fit)

    def _fit(self) -> bool:
        """On a phone, a bar that doesn't fit puts its least important items in ⋯ (v71 phone.js fitBar)."""
        self._fit_pending = False
        width = self._host_size()[0]
        self._fit_width = width
        for child in self._overflow:
            child.set_visible(True)
        self._overflow = []
        if self._more_button is not None and not self._more_items:
            self.bar_row.remove(self._more_button)
            self._more_button = None
        if (self._state not in ("bar", "double") or not self._phone or width <= 0 or self._bar_span
                or self._panel_only is not None):
            return False
        # A replacement switch may not have mapped yet. Fit its phone shape,
        # otherwise hiding the wide switch prevents its WidthWatch from ever
        # observing the host and the places remain trapped in More.
        for child in _children(self.bar_row):
            if getattr(child, "_bar_icon_tabs", False):
                child._width_changed(width)
        metrics = tokens.ACTION_CENTER
        bar_width = self.layout(width)[1]
        room = (bar_width if bar_width > 0 else width - 2 * metrics["phone_side_margin"]) - 2 * metrics["phone_bar_padding"]
        gap = (tokens.BAR["toolbar"]["gap"] if self.has_css_class("document-toolbar")
               else tokens.BAR["compact_picker"]["row_gap"] if self.bar_row.has_css_class("compact-pickers")
               else metrics["bar_gap"] if self.bar_row.has_css_class("small-controls")
               else metrics["phone_bar_gap"])

        def need() -> int:
            shown = [c for c in _children(self.bar_row) if c.get_visible()]
            # Explicitly flexible controls (kept search and ellipsizing modes)
            # shrink before otherwise-fitting actions move into overflow.
            return sum(c.measure(Gtk.Orientation.HORIZONTAL, -1)[
                0 if getattr(c, "bar_flexible", False) or (getattr(c, "bar_search", False) and c.get_hexpand()) else 1
            ] for c in shown) + gap * max(0, len(shown) - 1)

        if need() <= room:
            return False
        more = self._ensure_more_button()
        children = _children(self.bar_row)
        first = children[0] if children else None

        def keep(child: Gtk.Widget) -> bool:
            item = getattr(child, "bar_item", None)
            return child is first or child is more or getattr(item, "primary", False) or isinstance(item, BarChip)

        def is_switch(child: Gtk.Widget) -> bool:
            return child.has_css_class("lumaui-modes") or child.has_css_class("lumaui-segmented")

        for wanted in (lambda c: not is_switch(c), is_switch):
            for child in reversed(children):
                if need() <= room:
                    break
                if keep(child) or not wanted(child) or not child.get_visible():
                    continue
                child.set_visible(False)
                self._overflow.append(child)
        if not self._overflow and not self._more_items:
            # Nothing could leave (the first control alone is too wide): no ⋯ for an empty list.
            self.bar_row.remove(more)
            self._more_button = None
            return False
        # A hairline or a gap left at either end of what remains goes too.
        for child in children:
            if child.has_css_class("lumaui-bar-rule") and child.get_visible():
                prev, nxt = _visible_sibling(child, -1), _visible_sibling(child, 1)
                if prev is None or nxt is None or nxt is more:
                    child.set_visible(False)
                    self._overflow.append(child)
        return False

    def _all_filling_actions(self) -> bool:
        visible = [child for child in _children(self.bar_row) if child.get_visible()]
        return len(visible) > 1 and all(child.has_css_class("lumaui-bar-button")
                                       and child.has_css_class("fill") for child in visible)

    def _shape_row(self) -> None:
        """v71 on a phone: icon-and-word buttons show the icon alone (unless they keep their words);
        grown, the row's buttons share the width unless a field or a labelled key takes it."""
        grown = self._panel_key is not None or self._held is not None or (self._fill and self._phone)
        children = _children(self.bar_row)
        equal_actions = self._phone and grown and self._all_filling_actions()
        self.bar_row.set_homogeneous(equal_actions)
        lumaui.set_css_class(self.bar_row, "equal-actions", equal_actions)
        for child in children:
            if child.has_css_class("live") and child.has_css_class("lumaui-bar-chip"):
                # v71 Memos: on a phone the recording status is an inset well taking the spare width.
                well = self._phone or child.bar_item.well
                lumaui.set_css_class(child, "well", well)
                child.set_hexpand(well)
                if hasattr(child, "live_spacer"):
                    child.live_spacer.set_visible(well)
        lumaui.set_css_class(self.bar_row, "compact-pickers", any(c.has_css_class("compact-picker") or c.has_css_class("view-picker") for c in children))
        # A SPACER takes the slack itself (v71 Calendar's "Month ⌄ · · · Today"): nothing stretches then.
        spaced = any(getattr(c, "bar_spacer", False) for c in children)
        # v71 `.ckwin .fbar .bt` (Clock): beside a primary word the secondary word is a well, both 78 wide (a window).
        words_only = [c for c in children if c.has_css_class("lumaui-bar-button") and c.has_css_class("text")]
        lumaui.set_css_class(self.bar_row, "word-pair", not self._phone
                             and any(c.has_css_class("primary") for c in words_only)
                             and any(not c.has_css_class("primary") for c in words_only))
        keyed = any(c.has_css_class("keep-label") and not c.has_css_class("fill") for c in children)
        takes = keyed or any(getattr(c, "bar_wide", False) or getattr(c, "bar_phone_wide", False)
                             or getattr(c, "bar_search", False) or c.has_css_class("lumaui-ac-prompt") for c in children)
        for child in children:
            words = getattr(child, "bar_words", None)
            if words is not None:
                words.set_visible(not self._phone or child.has_css_class("keep-label"))
                lumaui.set_css_class(child, "glyph-only", self._phone and not child.has_css_class("keep-label"))
            if not child.has_css_class("lumaui-bar-button"):
                continue
            if getattr(child, "_panel_original", None) is not None:
                share = False  # Close remains an icon key, including a row with no search.
            elif child.has_css_class("fill"):
                share = self._phone and grown
            elif self.has_css_class("document-toolbar"):
                share = False  # A document panel grows above its fixed-width tools.
            elif spaced or child.has_css_class("dropdown"):
                share = False  # a picker keeps its own width; the spacer takes the rest
            elif child.has_css_class("keep-label"):
                share = self._phone and grown  # the labelled key takes the rest; icons stay 48
            elif self._fill and self._phone and (child.has_css_class("primary") or child.has_css_class("labelled")):
                share = False  # a key keeps its size in a full-width bar unless it fills too
            else:
                share = self._phone and grown and not takes
            if share != getattr(child, "_bar_shared", False):
                child._bar_shared = share
                child.set_hexpand(share)

    # ── internals: one safe area above the bar (v71) ─────────────────────

    def _queue_safe_area(self) -> None:
        if not self._safe_pending:
            self._safe_pending = True
            GLib.idle_add(self._apply_safe_area)

    def _apply_safe_area(self) -> bool:
        self._safe_pending = False
        host = self._host
        wanted: dict[int, tuple[Gtk.Widget, int]] = {}
        if host is not None and self._phone and self.get_visible() and self.get_mapped():
            top = self._row_top(host)
            if top is not None:
                candidates = list(self._scrollers)
                if self._safe_auto and host.get_child() is not None:
                    candidates += [w for w in _scrollers_in(host.get_child()) if w not in candidates]
                for scroller in candidates:
                    if not scroller.get_mapped() or scroller.is_ancestor(self):
                        continue
                    ok, bounds = scroller.compute_bounds(host)
                    if not ok:
                        continue
                    height, bottom = bounds.get_height(), bounds.get_y() + bounds.get_height()
                    if height < 160 or bottom < top + 4:
                        continue
                    room = max(tokens.ACTION_CENTER["safe_gap"],
                               round(bottom - top + tokens.ACTION_CENTER["safe_gap"]))
                    target = _safe_target(scroller)
                    if target is not None:
                        wanted[id(target[0])] = (target, room)
        # Give back what no longer needs room; set what does (only when it changed: no layout loop).
        for key in list(self._safe):
            if key not in wanted:
                widget, how, original = self._safe.pop(key)
                _set_room(widget, how, original)
        for key, ((widget, how), room) in wanted.items():
            if key not in self._safe:
                self._safe[key] = (widget, how, _get_room(widget, how))
            if _get_room(widget, how) != room:
                _set_room(widget, how, room)
        return False

    def _row_top(self, host: Gtk.Widget) -> float | None:
        """The top of the bar on screen, a grown panel not counted (it floats over the page)."""
        if self._state == "editor":
            first = self.card
        elif self._state == "split":
            first = self.split
        elif self._panel_only is not None:
            first = self.foot_row
        else:
            first = next((c for c in (self.hold_row, self.context_line, self.head_row, self.bar_row, self.entry_row)
                          if c.get_visible()), None)
        if first is None:
            return None
        ok, point = first.compute_point(host, Graphene.Point())
        if not ok:
            return None
        pad = tokens.ACTION_CENTER["phone_bar_padding"] if self._state in ("bar", "double") else 0
        return point.y - pad

    # ── internals ─────────────────────────────────────────────────────────

    def _carry_draft_in(self) -> None:
        """A bar field's text becomes the composer's draft as the bar grows."""
        if self.editor is None or self.editor.text_view is None:
            return
        for child in _children(self.bar_row):
            if hasattr(child, "carry_draft"):
                text = child.carry_draft()
                if text:
                    self.editor.text_view.get_buffer().set_text(text)
                return

    def _carry_draft_out(self) -> Gtk.Widget | None:
        """The composer's draft goes back into the bar field as the editor folds; returns that field."""
        for child in _children(self.bar_row):
            if hasattr(child, "receive_draft"):
                if self.editor is not None and self.editor.text_view is not None:
                    child.receive_draft(self.editor.draft or "")
                return child
        return None

    def _make_prompt(self, prompt: BarPrompt) -> Gtk.Button:
        button = Gtk.Button(hexpand=True, valign=Gtk.Align.CENTER)
        button.add_css_class("lumaui-ac-prompt")
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        if prompt.icon:
            line = Gtk.Box(spacing=8)
            glyph = icons.image(prompt.icon)
            glyph.add_css_class("lumaui-ac-prompt-icon")
            line.append(glyph)
            line.append(label)
            button.set_child(line)
        else:
            button.set_child(label)
        button.connect("clicked", lambda _b: self._prompt_activated(prompt))
        button.prompt_label = label
        self.prompt_button = button
        self._refresh_prompt()
        return button

    def _prompt_activated(self, prompt: BarPrompt) -> None:
        if prompt.on_activate is not None:
            prompt.on_activate()
        if self.editor is not None:
            self.grow()

    def _refresh_prompt(self) -> None:
        if self.prompt_button is None or self._prompt is None:
            return
        draft = (self.editor.draft if self.editor is not None else None) or ""
        draft = " ".join(draft.split())
        text = f"Draft: {draft}" if draft else self._prompt.text
        self.prompt_button.prompt_label.set_label(text)
        lumaui.set_css_class(self.prompt_button, "has-draft", bool(draft))
        self.prompt_button.update_property([Gtk.AccessibleProperty.LABEL], [text])

    def _set_state(self, state: str) -> None:
        if state not in AC_STATES:
            raise ValueError(state)
        self._state = state
        self.set_visible(state != "hidden")
        self.bar.set_visible(state in ("bar", "double"))
        self.card.set_visible(state == "editor")
        self.split.set_visible(state == "split")
        for name in AC_STATES:
            lumaui.set_css_class(self, name, name == state)
        self._apply_width()
        self.emit("state-changed", state)

    def _morph(self, part: Gtk.Widget) -> None:
        part.remove_css_class("morph")
        lumaui.on_next_frame(part, lambda: part.add_css_class("morph"))

    def _host_size(self) -> tuple[int, int]:
        host = self._host or self.get_parent()
        if host is None:
            return 0, 0
        return host.get_width(), host.get_height()

    def layout(self, host_width: int | None = None) -> tuple[bool, int, int, int]:
        """(phone, width, margin top, margin bottom) for the current state in a host this wide.

        v70: the bar is as wide as its actions; with a prompt or a context line
        it is min(700, 100% − 48); the editor min(720, 100% − 48), 20 above and
        below. On a phone the editor is the full width from the bottom edge,
        60 below the top, and the bars span the width less 8 each side. A
        width of -1 means "as wide as the content".
        """
        metrics = tokens.ACTION_CENTER
        width = self._host_size()[0] if host_width is None else host_width
        phone = 0 < width <= tokens.PHONE_MAX_WIDTH
        state, side = self._state, getattr(self, "_side", None) or metrics["side_margin"]
        top, bottom = metrics["editor_top"], metrics["bar_bottom_edge" if getattr(self, "_edge", False) else "bar_bottom"]
        if getattr(self, "_tight", False):
            bottom = BAR_BOTTOM_TIGHT
        if phone and not getattr(self, "_edge", False):
            bottom = metrics["phone_bar_bottom"]  # v71: 34 above the foot on a phone
        wide = state == "double" or (state == "bar" and self.bar.has_css_class("wide"))
        grown = state in ("bar", "double") and (self._panel_key is not None or self._held is not None)
        if width > 0 and grown:
            # v71 rule 6: grown, a phone less 12 a side; a window min(380, window − 24). Never narrower than it was.
            self._panel_key, saved = None, self._panel_key
            held, self._held = self._held, None
            try:
                base = self.layout(width)[1]
            finally:
                self._panel_key, self._held = saved, held
            # A form with equal filling actions uses the phone frame gutter,
            # like its editor counterpart (v71 Clock's Cancel / Save).
            grow_side = metrics["frame_side"] if self._fill or self._all_filling_actions() else metrics["grow_side"]
            if self._phone_grown_inset is not None:
                grow_side = self._phone_grown_inset
            grow = width - 2 * grow_side if phone else min(metrics["grow_width"], width - 2 * metrics["grow_side"])
            return phone, max(base, grow), (metrics["frame_sheet_room"] if phone else top), bottom
        if width <= 0:
            wanted = -1
        elif state == "editor":
            # v71 rule 15: on a phone the editor is the bar grown: the 16 gutter, 34 up, the bar's 26 corner.
            own = getattr(self.editor, "width", None) or metrics["editor_width"]
            wanted = width - 2 * metrics["frame_side"] if phone else min(own, width - 2 * side)
            top, bottom = (metrics["phone_top"], metrics["frame_bottom"]) if phone else (metrics["editor_top"], metrics["editor_bottom"])
        elif state == "bar" and getattr(self, "_bar_span", None) and self._search is None:
            # (max, side[, phone side]): the item's own span, on a phone too (v70 Tasks is
            # min(600, 100% − 32) everywhere; a regular composer has the 16 px phone gutter).
            span_width, span_side, *phone_side = self._bar_span
            side_now = phone_side[0] if phone and phone_side else span_side
            wanted = min(span_width, width - 2 * side_now)
        elif phone and state == "bar" and getattr(self, "_tab_bar", None) is not None:
            wanted = width - 2 * metrics["phone_side_margin"]  # v71 .fbar.phtabs: left 12, right 12
        elif phone and state in ("bar", "double") and self._panel_only is not None:
            wanted = width - 2 * metrics["frame_side"]
        elif phone and state in ("bar", "double") and (self.foot_row.get_visible() or self.head_row.get_visible() or self._fill):
            wanted = width - 2 * metrics["frame_side"]  # v71 two-row and full-width bars: the 16 gutter
        elif phone and (wide or state == "split" or self._search is not None
                        or (state == "bar" and getattr(self, "_phone_wide", False))):
            wanted = width - 2 * metrics["phone_side_margin"]
        elif wide:
            wanted = min(metrics["double_width"], width - 2 * side)
        else:
            wanted = -1
        return phone, max(-1, wanted), top, bottom

    def _apply_width(self) -> None:
        """Classes and structure for the layout; the size and place are `_position`'s."""
        phone = self.layout()[0]
        changed = phone != self._phone
        self._phone = phone
        lumaui.set_css_class(self, "phone", phone)
        self.split.set_orientation(Gtk.Orientation.VERTICAL if phone else Gtk.Orientation.HORIZONTAL)
        self._shape_row()
        if self.editor is not None and hasattr(self.editor, "hint"):
            self.editor.hint.set_visible(not phone)   # Nick, 1 Oct: no keyboard hints on a phone, ever
        if self.editor is not None and getattr(self.editor, "revert", None) is not None:
            self.editor.set_phone(phone)
        if changed and getattr(self, "_tab_switch", None) is not None:
            GLib.idle_add(lambda: (self._sync_tabs(), self._host.queue_allocate() if self._host else None, False)[2])
        if changed:
            self._refit()
        if self._host is not None:
            self._host.queue_allocate()
        self._queue_safe_area()

    def _position(self, host: Gtk.Overlay, widget: Gtk.Widget, allocation: Gdk.Rectangle) -> bool:
        """Place the action center in its host (the overlay asks on every layout)."""
        if widget is not self:
            return False
        host_w, host_h = host.get_width(), host.get_height()
        phone, wanted, top, bottom = self.layout(host_w)
        if phone != self._phone or (self._state == "editor" and self._wanted_cap() != self._text_cap):
            GLib.idle_add(self._reapply)  # structure changes wait until this layout is done
        elif phone and host_w != self._fit_width:
            self._queue_fit()
        if phone or self._safe:
            self._queue_safe_area()
        if wanted < 0:
            natural = self.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            inset = tokens.ACTION_CENTER["bar_phone_inset" if phone else "bar_inset"]
            wanted = min(natural, max(0, host_w - inset))  # v70 .fbar max-width: 100% - 24 (a phone too; Notes' scrolling bar is its own case)
        minimum_w = self.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        width = max(minimum_w, wanted)
        minimum_h, natural_h, _b1, _b2 = self.measure(Gtk.Orientation.VERTICAL, width)
        height = max(minimum_h, min(natural_h, host_h - top - bottom))
        allocation.x = int((host_w - width) / 2)
        allocation.y = int(host_h - bottom - height)
        allocation.width, allocation.height = int(width), int(height)
        return True

    def _cap_text(self) -> None:
        """The editor's content is at most 40% of the window tall; the island caps the rest."""
        cap = self._wanted_cap()
        if self.editor is not None and cap and cap != self._text_cap:
            self._text_cap = cap
            self.editor.scroller.set_max_content_height(cap)

    def _wanted_cap(self) -> int:
        root = self.get_root()
        height = root.get_height() if root is not None else 0
        if height <= 0:
            return 0
        metrics = tokens.ACTION_CENTER
        return max(metrics["text_min_height"], int(height * metrics["text_max_pct"] / 100))

    def _reapply(self) -> bool:
        self._apply_width()
        if self._state == "editor":
            self._cap_text()
        return False

    def _editor_key(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int,
                    state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.fold()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (state & Gdk.ModifierType.CONTROL_MASK or
                (self.editor is not None and self.editor.submit_on_return and
                 not state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK))):
            return self.editor.run_primary() if self.editor is not None else False
        return False


def BarModes(*args, **kwargs) -> Gtk.Widget:
    """A switch in the bar (v71 rule 3): a ModeSwitch; on a phone a row of the bar's own buttons, the chosen
    one raised, never a frame inside a frame. Same arguments as `ModeSwitch`."""
    from .structure_placement import ModeSwitch
    return ModeSwitch(*args, **kwargs)


def _modes_item(item: Gtk.Widget, _size: str) -> Gtk.Widget:
    """A ModeSwitch among the items: a text segment (aspect, zoom level) in the bar.

    v71 (Clock, Phone): on a phone, places with icons are icon-only tabs, the chosen one the raised chip
    with its glyph lit; a count sits in the tab's corner."""
    if item.get_parent() is not None:
        item.get_parent().remove(item)
    item.add_css_class("in-bar")
    item.set_valign(Gtk.Align.CENTER)
    if not getattr(item, "labels_only", True) and not getattr(item, "_bar_icon_tabs", False):
        item._bar_icon_tabs = True
        own = item._labels

        def labels() -> None:
            own()
            for button in item.buttons.values():
                if item._narrow:
                    button.label_widget.set_visible(False)
                _corner_count(button, item._narrow)

        item._labels = labels
        labels()
    return item


def _places(switch: Gtk.Widget) -> list[tuple[str, str, str]] | None:
    """A ModeSwitch's places as (key, label, icon), or None when it isn't a place switch with icons."""
    buttons = getattr(switch, "buttons", None)
    if not buttons or getattr(switch, "labels_only", False):
        return None
    places = []
    for key, button in buttons.items():
        icon = _button_icon(button)
        words = button.label_widget.get_label() if hasattr(button, "label_widget") else _button_words(button)
        if not icon:
            return None
        places.append((key, words, icon))
    return places


def _choose_mode(switch: Gtk.Widget, key: str) -> None:
    if switch.current != key:
        switch._select(key, notify=True)


def _corner_count(button: Gtk.Widget, corner: bool) -> None:
    """A tab's count and its running dot in its top-right corner (icon tabs on a phone), or after its
    words (everywhere else)."""
    cornered = button.__dict__.setdefault("_cornered", set())
    for name in ("count_badge", "status_dot"):
        widget = getattr(button, name, None)
        if widget is None or (name in cornered) == corner:
            continue
        (cornered.add if corner else cornered.discard)(name)
        parent = widget.get_parent()
        if parent is not None:
            parent.remove_overlay(widget) if isinstance(parent, Gtk.Overlay) else parent.remove(widget)
        line = button.get_child()
        if corner:
            if not isinstance(line, Gtk.Overlay):
                button.set_child(None)
                holder = Gtk.Overlay(child=line)
                button.set_child(holder)
                line = holder
            widget.set_halign(Gtk.Align.END)
            widget.set_valign(Gtk.Align.START)
            widget.add_css_class("corner")
            line.add_overlay(widget)
        else:
            inner = line.get_child() if isinstance(line, Gtk.Overlay) else line
            if isinstance(line, Gtk.Overlay) and not cornered:
                line.set_child(None)
                button.set_child(inner)
            widget.remove_css_class("corner")
            widget.set_halign(Gtk.Align.CENTER if name == "status_dot" else Gtk.Align.FILL)
            widget.set_valign(Gtk.Align.CENTER if name == "status_dot" else Gtk.Align.FILL)
            inner.append(widget)


def _register_modes() -> None:
    from .structure_placement import ModeSwitch
    register_item(ModeSwitch, _modes_item)
    try:  # v71: TabBar(compact=True) sits in a bar beside search (Phone)
        from .structure_tabs import TabBar
    except ImportError:  # pragma: no cover - an older kit without K-NAV's tab bar
        return
    register_item(TabBar, _tabs_item)


def _tabs_item(item: Gtk.Widget, _size: str) -> Gtk.Widget:
    _detach(item)
    item.set_valign(Gtk.Align.CENTER)
    item.add_css_class("in-bar")
    return item


_register_modes()


def _children(box: Gtk.Widget) -> list[Gtk.Widget]:
    found, child = [], box.get_first_child()
    while child is not None:
        found.append(child)
        child = child.get_next_sibling()
    return found


def _clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def _detach(widget: Gtk.Widget) -> None:
    parent = widget.get_parent()
    if parent is None:
        return
    if isinstance(parent, Gtk.Viewport):
        parent.set_child(None)
    elif hasattr(parent, "remove"):
        parent.remove(widget)
    else:
        widget.unparent()


def _visible_sibling(widget: Gtk.Widget, step: int) -> Gtk.Widget | None:
    node = widget.get_prev_sibling() if step < 0 else widget.get_next_sibling()
    while node is not None and not node.get_visible():
        node = node.get_prev_sibling() if step < 0 else node.get_next_sibling()
    return node


def _buttons(widget: Gtk.Widget) -> list[Gtk.Button]:
    """The buttons inside a bar item (a switch's segments), in order."""
    if isinstance(widget, Gtk.Button):
        return [widget]
    found: list[Gtk.Button] = []
    child = widget.get_first_child()
    while child is not None:
        found += _buttons(child)
        child = child.get_next_sibling()
    return found


def _button_words(button: Gtk.Button) -> str:
    def label_in(widget: Gtk.Widget) -> str:
        if isinstance(widget, Gtk.Label) and widget.get_label():
            return widget.get_label()
        child = widget.get_first_child()
        while child is not None:
            text = label_in(child)
            if text:
                return text
            child = child.get_next_sibling()
        return ""
    return button.get_tooltip_text() or label_in(button) or (button.get_label() or "")


def _button_icon(button: Gtk.Button) -> str | None:
    def image_in(widget: Gtk.Widget) -> str | None:
        if isinstance(widget, Gtk.Image) and widget.get_icon_name():
            return widget.get_icon_name()
        child = widget.get_first_child()
        while child is not None:
            name = image_in(child)
            if name:
                return name
            child = child.get_next_sibling()
        return None
    name = image_in(button)
    if name and name.endswith("-symbolic") and name.startswith(icons.PREFIX):
        return name[len(icons.PREFIX):-len("-symbolic")]  # back to the Lucide name icons.image() takes
    return name


def _press(button: Gtk.Button) -> None:
    if isinstance(button, Gtk.ToggleButton):
        button.set_active(True)
    else:
        button.emit("clicked")


def _scrollers_in(widget: Gtk.Widget) -> list[Gtk.Widget]:
    found: list[Gtk.Widget] = []
    if isinstance(widget, Gtk.ScrolledWindow) and widget.get_policy()[1] != Gtk.PolicyType.NEVER:
        found.append(widget)  # a sideways-only strip is not a page
    child = widget.get_first_child()
    while child is not None:
        if child.get_visible() and not isinstance(child, ActionCenter):
            found += _scrollers_in(child)
        child = child.get_next_sibling()
    return found


def _safe_target(scroller: Gtk.Widget) -> tuple[Gtk.Widget, str] | None:
    """Where a scroller's bottom room goes: its content's margin (in a viewport) or a text view's bottom margin."""
    child = scroller.get_child() if isinstance(scroller, Gtk.ScrolledWindow) else scroller
    if isinstance(child, Gtk.Viewport) and child.get_child() is not None:
        return child.get_child(), "margin"
    if isinstance(child, Gtk.TextView):
        return child, "text"
    if isinstance(child, Gtk.Scrollable):
        return child, "margin"  # native scrollable canvases such as VTE own their viewport
    return None


def _get_room(widget: Gtk.Widget, how: str) -> int:
    return widget.get_bottom_margin() if how == "text" else widget.get_margin_bottom()


def _set_room(widget: Gtk.Widget, how: str, value: int) -> None:
    if how == "text":
        widget.set_bottom_margin(value)
    else:
        widget.set_margin_bottom(value)
