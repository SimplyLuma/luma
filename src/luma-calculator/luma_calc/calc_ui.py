"""Calculator-only display and key banks, composed with LumaUI surfaces and type roles."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Sequence
import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from luma_appkit import Island, apply_type
from luma_appkit import lumaui_tokens as tokens

from .engine import format_decimal

# v71 phone (the "Calculator on a phone" block). Its CSS says the answer is 80 (56 beside the scientific
# bank), but caFit() sets an inline 72 and shrinks from there, so the screen shows the display role.
PHONE_KEY_ICON = 28
PHONE_KEY_ASPECT = 1 / 0.9       # width over height of a basic key on the phone
PHONE_KEY_GAP = 12               # between basic keys and between the banks
PHONE_SCIENTIFIC_GAP = 8
PHONE_SCIENTIFIC_HEIGHTS = (42, 62)   # scientific key, then basic key, beside the scientific bank


class FittingExpression(Gtk.ScrolledWindow):
    """Keep exact expression fragments visible at a legible size and scroll extremes."""

    def __init__(self) -> None:
        super().__init__(hexpand=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                         vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_overlay_scrolling(True)
        self.set_propagate_natural_width(False)
        self._row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._row.add_css_class("calc-expression")
        self._row.append(Gtk.Box(hexpand=True))
        self.set_child(self._row)
        self._labels: list[Gtk.Label] = []
        self._base = int(tokens.TYPE_SCALE["display"]["size"])
        self._size = self._base
        self._fit_width = -1
        self._scroll_pending = False

    def set_fragments(self, fragments: Sequence[str]) -> None:
        while (child := self._row.get_last_child()) and child is not self._row.get_first_child():
            self._row.remove(child)
        self._labels.clear()
        self._size = self._base
        self._fit_width = -1
        for index, part in enumerate(fragments):
            label = apply_type(Gtk.Label(label=part), "display")
            previous = fragments[index - 1].strip() if index else ""
            unary_minus = part == "−" and (not previous or previous in "+−×÷^" or
                                           previous.endswith(("(", "E")))
            if part in "+−×÷^" and not unary_minus:
                label.add_css_class("calc-expression-operator")
            self._row.append(label)
            self._labels.append(label)
        self._fit_to_width(self.get_width())
        self._queue_scroll_end()
        self.queue_resize()

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self._fit_to_width(width)
        Gtk.ScrolledWindow.do_size_allocate(self, width, height, baseline)
        self._queue_scroll_end()

    def _fit_to_width(self, width: int) -> None:
        if width <= 0 or not self._labels or width == self._fit_width:
            return
        maximum = self._base   # v71 caFit starts at the display role (72) on a phone too
        minimum = int(tokens.TYPE_SCALE["title_1"]["size"])
        low, high, chosen = minimum, maximum, minimum
        while low <= high:
            size = (low + high) // 2
            self._set_size(size)
            needed = sum(label.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
                         for label in self._labels)
            if needed <= width:
                chosen = size
                low = size + 1
            else:
                high = size - 1
        self._set_size(chosen)
        self._fit_width = width

    def _set_size(self, size: int) -> None:
        base = self._base
        if size == self._size:
            if size == base and all(label.get_attributes() is None for label in self._labels):
                return
            if size != base and all(label.get_attributes() is not None for label in self._labels):
                return
        self._size = size
        for label in self._labels:
            if size == base:
                label.set_attributes(None)
            else:
                attributes = Pango.AttrList()
                attributes.insert(Pango.attr_size_new_absolute(size * Pango.SCALE))
                attributes.insert(Pango.attr_letter_spacing_new(
                    round(size * tokens.TYPE_SCALE["display"]["tracking_em"] * Pango.SCALE)))
                label.set_attributes(attributes)

    def _queue_scroll_end(self) -> None:
        if not self._scroll_pending:
            self._scroll_pending = True
            GLib.idle_add(self._show_latest)

    def _show_latest(self) -> bool:
        self._scroll_pending = False
        adjustment = self.get_hadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE


class _DisplayContent:
    """The screen's contents: tape, expression, preview and angle mode (a mixin for its two surfaces)."""

    #: Margins (top, bottom, sides) of the stack, and the angle badge's top and start.
    _STACK = (12, 16, 20)
    _BADGE = (14, 18)
    #: v71 .caline button { margin-right: -6px }: the tape's answers run 6px past the expression's edge.
    _TAPE_BLEED = 6
    phone = False

    def __init__(self, *, expression: str, preview: str = "",
                 tape: Sequence[tuple[str, Decimal]] = (), mode: str = "deg", error: str = "",
                 on_reuse: Callable[[int], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("calc-display")
        self.set_margin_top(0)
        self.set_vexpand(True)
        self._on_reuse = on_reuse
        self.set_margin_start(0)
        self.set_margin_end(0)

        overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        self.append(overlay)
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        top, bottom, side = self._STACK
        stack.set_margin_top(top)
        stack.set_margin_bottom(bottom)
        stack.set_margin_start(side)
        bleed = self._TAPE_BLEED
        stack.set_margin_end(side - bleed)
        overlay.set_child(stack)

        self.tape_view = Gtk.ScrolledWindow(vexpand=True,
                                            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                            vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.tape_view.set_overlay_scrolling(True)
        self.tape_view.set_propagate_natural_width(False)
        self.tape_view.add_css_class("calc-tape")
        self.tape_lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                                  valign=Gtk.Align.END)
        # The tape ends above the expression, leaving the result hit area clear.
        self.tape_lines.set_margin_bottom(4)
        self.tape_view.set_child(self.tape_lines)
        stack.append(self.tape_view)

        self.expression_label = FittingExpression()
        self.expression_label.set_margin_end(bleed)
        self.expression_label.set_name("calc-expression")
        self.expression_label.add_css_class("calc-expression")
        stack.append(self.expression_label)

        self.preview_label = apply_type(Gtk.Label(xalign=1, hexpand=True,
                                                   ellipsize=Pango.EllipsizeMode.START), "body")
        self.preview_label.set_name("calc-preview")
        self.preview_label.add_css_class("calc-preview")
        self.preview_label.set_margin_end(bleed)
        stack.append(self.preview_label)

        self.mode_label = apply_type(Gtk.Label(label="Rad", halign=Gtk.Align.START,
                                               valign=Gtk.Align.START), "label")
        self.mode_label.add_css_class("calc-angle-mode")
        badge_top, badge_start = self._BADGE
        self.mode_label.set_margin_top(badge_top)
        self.mode_label.set_margin_start(badge_start)
        overlay.add_overlay(self.mode_label)

        self.set_expression(expression)
        self.set_preview(preview)
        self.set_tape(tape)
        self.set_mode(mode)
        self.set_error(error)

    def set_expression(self, expression: str) -> None:
        self.expression_label.set_fragments(self._fragments(expression or "0"))

    @staticmethod
    def _fragments(expression: str) -> list[str]:
        parts = [part.strip() for part in re.split(r"([+−×÷^])", expression)
                 if part.strip()]
        result: list[str] = []
        space_before_next = False
        for index, part in enumerate(parts):
            if part in "+−×÷^":
                previous = parts[index - 1] if index else ""
                unary = part == "−" and (not previous or previous in "+−×÷^" or
                                         previous.endswith(("(", "E")))
                if not unary and result:
                    result[-1] += " "
                result.append((" " if unary and space_before_next else "") + part)
                space_before_next = not unary
            else:
                result.append((" " if space_before_next else "") + part)
                space_before_next = False
        return result

    def set_preview(self, preview: str) -> None:
        self.preview_label.set_label(preview)

    def set_tape(self, tape: Sequence[tuple[str, Decimal]]) -> None:
        while child := self.tape_lines.get_first_child():
            self.tape_lines.remove(child)
        for index in range(len(tape) - 1, -1, -1):
            expression, result = tape[index]
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                           halign=Gtk.Align.END)
            line.add_css_class("calc-tape-line")
            calculation = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            calculation.add_css_class("calc-tape-expression")
            for part in self._fragments(expression):
                calculation.append(apply_type(Gtk.Label(label=part), "body"))
            line.append(calculation)
            reuse = Gtk.Button(label="= " + format_decimal(result))
            reuse.add_css_class("flat")
            reuse.add_css_class("calc-tape-result")
            reuse.set_size_request(32, 21)
            reuse.set_tooltip_text("Use this answer")
            reuse.update_property([Gtk.AccessibleProperty.LABEL],
                                  ["Use this answer"])
            reuse.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                  ["Result " + format_decimal(result)])
            reuse.set_name(f"calc-tape-{index}")
            if self._on_reuse is not None:
                reuse.connect("clicked", lambda _button, i=index: self._on_reuse(i))
            line.append(reuse)
            self.tape_lines.append(line)
        GLib.idle_add(self._show_tape_end)

    def _show_tape_end(self) -> bool:
        adjustment = self.tape_view.get_hadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return GLib.SOURCE_REMOVE

    def set_mode(self, mode: str) -> None:
        self.mode_label.set_visible(mode == "rad")

    def set_error(self, error: str) -> None:
        if error:
            self.preview_label.set_label(error)
            self.preview_label.add_css_class("error")
        else:
            self.preview_label.remove_css_class("error")


class CalculatorDisplay(_DisplayContent, Island):
    """A computer's screen: the recessed island the tape, expression and preview sit in."""


class PhoneCalculatorDisplay(_DisplayContent, Gtk.Box):
    """v71 on a phone: the display is the page itself, from the status bar down, with no island.

    A sideways swipe anywhere on it deletes the last digit (`on_swipe`), and a swipe that acts never
    also taps a tape line.
    """

    _STACK = (44, 0, 4)   # .cadisp is a .isle: the phone shell pads it 44 at the top, under the body's 44
    _BADGE = (8, 4)
    _TAPE_BLEED = 0
    phone = True
    SWIPE_MINIMUM = 36

    def __init__(self, *, on_swipe: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_css_class("calc-display-phone")
        self._on_swipe = on_swipe
        drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

    def _drag_end(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if abs(dx) > self.SWIPE_MINIMUM and abs(dx) > abs(dy) * 1.5:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if self._on_swipe is not None:
                self._on_swipe()


def make_display(*, phone: bool, on_swipe: Callable[[], None] | None = None, **kwargs):
    return PhoneCalculatorDisplay(on_swipe=on_swipe, **kwargs) if phone else CalculatorDisplay(**kwargs)


class CalculatorKey(Gtk.Button):
    """One app-specific key using LumaUI tokens and ordinary GTK accessibility."""

    def __init__(self, label: str, *, action: str, kind: str = "digit",
                 active: bool = False, wide: bool = False, accessible_label: str | None = None,
                 on_activate: Callable[[str], None] | None = None, phone: bool = False,
                 icon: str | None = None) -> None:
        super().__init__(label=label, hexpand=True)
        self.action = action
        self.kind = kind
        self.wide = wide
        self.phone = phone
        # A phone's keys take their height from the screen's width (the fractional keypad) or
        # from the scientific layout (`set_phone_height`), never from a fixed 58.
        if not phone:
            self.set_size_request(-1, tokens.CALC["key_height"])
        else:
            self.add_css_class("calc-key-phone")
        self.add_css_class("calc-key")
        self.add_css_class("calc-key-" + kind)
        apply_type(self, {
            "digit": "calc-digit", "function": "calc-function",
            "operator": "calc-operator", "equals": "calc-equals",
            "scientific": "calc-scientific",
        }[kind])
        if active:
            self.add_css_class("active")
        self._accessible_label = accessible_label
        self.update_property([Gtk.AccessibleProperty.LABEL], [accessible_label or label])
        if accessible_label is not None:
            self.set_tooltip_text(accessible_label)
        if icon is not None:
            self.set_icon(icon)
        elif wide:
            self.add_css_class("calc-key-wide")
            # v71 .cazero: the 0 sits at the start of its double-wide key, 28px in.
            content = Gtk.Label(label=label, xalign=0, hexpand=True)
            content.set_margin_start(11)   # + the key's own 17px padding = 28
            self.set_child(content)
        elif kind == "scientific" and label in {"x²", "x³", "xʸ", "eˣ", "10ˣ", "ʸ√x"}:
            base, exponent = {"x²": ("x", "2"), "x³": ("x", "3"),
                              "xʸ": ("x", "y"), "eˣ": ("e", "x"),
                              "10ˣ": ("10", "x"), "ʸ√x": ("√x", "y")}[label]
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            superscript = Gtk.Label(label=exponent, valign=Gtk.Align.START)
            superscript.add_css_class("calc-superscript")
            if label == "ʸ√x":
                content.append(superscript)
            content.append(Gtk.Label(label=base))
            if label != "ʸ√x":
                content.append(superscript)
            self.set_child(content)
        if on_activate is not None:
            self.connect("clicked", lambda _button: on_activate(self.action))

    def set_icon(self, icon: str) -> None:
        """A glyph in place of the label (the phone's mode key and its delete key)."""
        from luma_appkit import icons
        glyph = icons.image(icon, pixel_size=PHONE_KEY_ICON)
        glyph.set_halign(Gtk.Align.CENTER)
        self.set_child(glyph)

    def set_phone_height(self, height: int) -> None:
        self.set_size_request(-1, height)

    def set_label(self, label: str, accessible_label: str | None = None) -> None:
        Gtk.Button.set_label(self, label)
        self.update_property([Gtk.AccessibleProperty.LABEL], [accessible_label or self._accessible_label or label])


class _PhoneBasicLayout(Gtk.LayoutManager):
    """Round the five-row bank once, instead of rounding each aspect-frame key."""

    def do_get_request_mode(self, _widget):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, widget, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum = 0
            child = widget.get_first_child()
            while child is not None:
                minimum = max(minimum, child.measure(orientation, -1)[0])
                child = child.get_next_sibling()
            width = minimum * 4 + PHONE_KEY_GAP * 3
            return width, width, -1, -1
        width = for_size if for_size >= 0 else 328
        row_height = (width - PHONE_KEY_GAP * 3) / 4 / PHONE_KEY_ASPECT
        height = round(row_height * 5 + PHONE_KEY_GAP * 4)
        return height, height, -1, -1

    def do_allocate(self, widget, width, _height, _baseline):
        key_width = (width - PHONE_KEY_GAP * 3) / 4
        key_height = key_width / PHONE_KEY_ASPECT
        child = widget.get_first_child()
        index = 0
        while child is not None:
            row, column = divmod(index, 4)
            x = round(column * (key_width + PHONE_KEY_GAP))
            y = round(row * (key_height + PHONE_KEY_GAP))
            right = round(column * (key_width + PHONE_KEY_GAP) + key_width)
            bottom = round(row * (key_height + PHONE_KEY_GAP) + key_height)
            transform = Gsk.Transform.new().translate(Graphene.Point().init(x, y))
            child.allocate(right - x, bottom - y, -1, transform)
            child = child.get_next_sibling()
            index += 1


class CalculatorKeypad(Gtk.Box):
    """Calculator-only bank geometry; keys themselves keep their semantic kinds."""

    def __init__(self, *, keys: Sequence[CalculatorKey], scientific_keys: Sequence[CalculatorKey] = (),
                 scientific: bool = False, phone: bool = False) -> None:
        if phone:
            self._init_phone(keys, scientific_keys, scientific)
            return
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=14, hexpand=True)
        self.add_css_class("calc-keypad")
        self.basic_bank = Gtk.Grid(row_spacing=8, column_spacing=8, hexpand=True)
        self.basic_bank.set_column_homogeneous(True)
        self.basic_bank.add_css_class("calc-basic-bank")
        self.scientific_bank = Gtk.Grid(row_spacing=8, column_spacing=8, hexpand=True)
        self.scientific_bank.set_column_homogeneous(True)
        self.scientific_bank.add_css_class("calc-scientific-bank")
        if scientific:
            self.append(self.scientific_bank)
        self.append(self.basic_bank)
        self._fill(self.basic_bank, keys, columns=4)
        self._fill(self.scientific_bank, scientific_keys, columns=5)
        if scientific:
            self._constrain_scientific_banks()

    def _init_phone(self, keys, scientific_keys, scientific: bool) -> None:
        """v71's phone: the banks stack (scientific above basic) on the 16px gutter, 12 apart."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=PHONE_KEY_GAP, hexpand=True)
        self.add_css_class("calc-keypad")
        self.add_css_class("calc-keypad-phone")
        self.scientific_bank = Gtk.Grid(row_spacing=PHONE_SCIENTIFIC_GAP, column_spacing=PHONE_SCIENTIFIC_GAP,
                                        hexpand=True, column_homogeneous=True)
        self.scientific_bank.add_css_class("calc-scientific-bank")
        self.basic_bank = Gtk.Grid(row_spacing=PHONE_KEY_GAP, column_spacing=PHONE_KEY_GAP,
                                   hexpand=True, column_homogeneous=True)
        self.basic_bank.add_css_class("calc-basic-bank")
        if scientific:
            sci_height, basic_height = PHONE_SCIENTIFIC_HEIGHTS
            for key in scientific_keys:
                key.set_phone_height(sci_height)
                key.add_css_class("calc-key-phone-sci")
            for key in keys:
                key.set_phone_height(basic_height)
                key.add_css_class("calc-key-phone-beside")
            self.append(self.scientific_bank)
        self.append(self.basic_bank)
        # Basic keys use one fractional grid; scientific mode keeps its fixed-height bank.
        self._fill(self.basic_bank, keys, columns=4)
        if not scientific:
            self.basic_bank.set_layout_manager(_PhoneBasicLayout())
        self._fill(self.scientific_bank, scientific_keys, columns=5)

    def _constrain_scientific_banks(self) -> None:
        """Match v70's 1.3:1 flex split at every scientific window width."""
        layout = Gtk.ConstraintLayout()
        gap = self.get_spacing()
        self.set_layout_manager(layout)
        attribute = Gtk.ConstraintAttribute
        relation = Gtk.ConstraintRelation.EQ
        strength = Gtk.ConstraintStrength.REQUIRED
        ratio = 1.3 / 2.3
        for target, target_attr, source, source_attr, multiplier, constant in (
            (self.scientific_bank, attribute.LEFT, None, attribute.LEFT, 1, 0),
            (self.scientific_bank, attribute.TOP, None, attribute.TOP, 1, 0),
            (self.scientific_bank, attribute.BOTTOM, None, attribute.BOTTOM, 1, 0),
            (self.scientific_bank, attribute.WIDTH, None, attribute.WIDTH, ratio, -gap * ratio),
            (self.basic_bank, attribute.LEFT, self.scientific_bank, attribute.RIGHT, 1, gap),
            (self.basic_bank, attribute.RIGHT, None, attribute.RIGHT, 1, 0),
            (self.basic_bank, attribute.TOP, None, attribute.TOP, 1, 0),
            (self.basic_bank, attribute.BOTTOM, None, attribute.BOTTOM, 1, 0),
        ):
            layout.add_constraint(Gtk.Constraint.new(
                target, target_attr, relation, source, source_attr,
                multiplier, constant, strength))

    @staticmethod
    def _fill(grid: Gtk.Grid, keys: Sequence[CalculatorKey], *, columns: int) -> None:
        row = column = 0
        for key in keys:
            span = 2 if key.wide else 1
            if column + span > columns:
                row += 1
                column = 0
            grid.attach(key, column, row, span, 1)
            column += span
            if column == columns:
                row += 1
                column = 0
