"""Calculator's LumaUI window.

    window          AppWindow: frame, identity menu and controls
    body            app-only CalculatorDisplay above CalculatorKeypad
    display         recessed content surface, LumaUI type roles, live preview and paper tape
    keys            app-only calculator keys in basic and scientific banks

The app owns arithmetic, key meanings and the calculator-only surfaces, using
LumaUI tokens and parts under the updated app-only rule in GLOBAL-HANDOFF §2.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    AppWindow, BarAction, Command, CommandGroup, CommandRegistry, add_style_sheet, bar_menu,
    install_appkit, install_lumaui,
)
from luma_appkit import lumaui  # noqa: E402

from .engine import Calculator, format_decimal
from .calc_ui import CalculatorKey, CalculatorKeypad, make_display


APP_ID = "org.projectluma.Calculator"

# v70's order, with a semantic kind for the kit to render.
BASIC_KEYS = (
    ("ac", "AC", "function"), ("neg", "±", "function"), ("%", "%", "function"), ("÷", "÷", "operator"),
    ("7", "7", "digit"), ("8", "8", "digit"), ("9", "9", "digit"), ("×", "×", "operator"),
    ("4", "4", "digit"), ("5", "5", "digit"), ("6", "6", "digit"), ("−", "−", "operator"),
    ("1", "1", "digit"), ("2", "2", "digit"), ("3", "3", "digit"), ("+", "+", "operator"),
    ("0", "0", "digit"), (".", ".", "digit"), ("=", "=", "equals"),
)

# v71 on a phone: bottom left is the mode key, so 0 is one key wide (the double-wide 0 gave up its left half).
PHONE_BASIC_KEYS = BASIC_KEYS[:16] + (
    ("mode", "", "function"), ("0", "0", "digit"), (".", ".", "digit"), ("=", "=", "equals"),
)

SCIENTIFIC_KEYS = (
    ("(", "(", "scientific"), (")", ")", "scientific"), ("2nd", "2nd", "scientific"),
    ("deg", "Deg", "scientific"), ("rand", "Rand", "scientific"),
    ("²", "x²", "scientific"), ("³", "x³", "scientific"), ("^", "xʸ", "scientific"),
    ("exp", "eˣ", "scientific"), ("p10", "10ˣ", "scientific"),
    ("⁻¹", "1/x", "scientific"), ("sqrt", "√x", "scientific"), ("cbrt", "∛x", "scientific"),
    ("root", "ʸ√x", "scientific"), ("!", "x!", "scientific"),
    ("sin", "sin", "scientific"), ("cos", "cos", "scientific"), ("tan", "tan", "scientific"),
    ("ln", "ln", "scientific"), ("log", "log", "scientific"),
    ("π", "π", "scientific"), ("e", "e", "scientific"), ("E", "EE", "scientific"),
    ("abs", "|x|", "scientific"), ("mod", "mod", "scientific"),
)

KEY_NAMES = {"mode": "mode", "+": "add", "−": "subtract", "×": "multiply", "÷": "divide", "=": "equals",
             "%": "percent", ".": "point", "(": "open", ")": "close", "!": "factorial",
             "²": "square", "³": "cube", "^": "power", "⁻¹": "reciprocal", "π": "pi"}

KEY_LABELS = {"neg": "Plus or minus", "%": "Percent", "÷": "Divide", "×": "Multiply",
              "−": "Subtract", "+": "Add", ".": "Decimal point", "=": "Equals",
              "2nd": "Second functions", "deg": "Degrees or radians", "rand": "Random number",
              "E": "Times ten to the power", "mode": "Basic or scientific"}


class CalculatorWindow(AppWindow):
    def __init__(self, application: Adw.Application) -> None:
        path = os.environ.get("LUMA_CALC_FIXTURE")
        self.calculator = Calculator.from_fixture(path) if path else Calculator()
        if path and os.environ.get("LUMA_CALC_SCIENTIFIC") == "1":
            self.calculator.scientific = True
        self._keys: dict[str, Gtk.Widget] = {}
        # v71: the phone calculator is the whole screen. The device decides, not the window's width: a
        # narrow desktop window keeps the desktop layout.
        self.on_phone = lumaui.mobile_form_factor()
        self._mode_panel = None
        super().__init__(application=application, app_id=APP_ID, title="Calculator", icon_name="luma-v3-calculator",
                         commands=self._commands(),
                         default_width=740 if self.calculator.scientific and not self.on_phone else 360,
                         default_height=600,
                         minimum_width=320, minimum_height=500, size_class="utility",
                         identity_menu_variant="compact")
        self.identity.set_name("calc-identity")
        self.identity.update_property([Gtk.AccessibleProperty.LABEL], ["Calculator menu"])
        self.identity.set_tooltip_text("Calculator menu")
        self.body_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16 if self.on_phone else 13,
                                   hexpand=True, vexpand=True)
        self.body_column.set_name("calc-body")
        if self.on_phone:
            # v71 .cabody on a phone: 44 under the clock, the 16px gutter, a 40px foot.
            self.body_column.set_margin_top(self.status_inset)
            self.body_column.set_margin_start(16)
            self.body_column.set_margin_end(16)
            self.body_column.set_margin_bottom(40)
        else:
            self.body_column.set_margin_top(0)
            self.body_column.set_margin_start(4)
            self.body_column.set_margin_end(4)
            self.body_column.set_margin_bottom(4)
        self.set_body(self.body_column)
        self.display = make_display(phone=self.on_phone, on_swipe=self._swipe_delete,
                               expression=self.calculator.display,
                               preview=self.calculator.preview,
                               tape=self.calculator.tape,
                               mode="deg" if self.calculator.degrees else "rad",
                               on_reuse=self._reuse_tape)

        self.display.set_name("calc-display")
        self.display.tape_view.set_name("calc-tape")
        self.display.set_vexpand(True)
        self.body_column.append(self.display)
        self.keypad = None
        self._keypad_container: Gtk.Widget | None = None
        self._build_keypad()
        controller = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._key_pressed)
        self.add_controller(controller)
        if os.environ.get("LUMA_CALC_MENU") == "1":
            self._open_fixture_menu()

    def _open_fixture_menu(self) -> None:
        if self.on_phone:
            GLib.idle_add(lambda: (self._open_mode_panel(), GLib.SOURCE_REMOVE)[1])
            return
        """Open the menu after the capture window reaches its requested size."""
        size = os.environ.get("LUMAUI_CONFORM_SIZE", "")
        try:
            width, height = (int(part) for part in size.lower().split("x"))
        except ValueError:
            GLib.idle_add(self.identity.set_active, True)
            return

        def open_when_sized() -> bool:
            if not self.get_mapped() or (self.get_width(), self.get_height()) != (width, height):
                return GLib.SOURCE_CONTINUE
            self.identity.set_active(True)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(50, open_when_sized)

    def _commands(self) -> CommandRegistry:
        return CommandRegistry((
            CommandGroup("", (
                Command("calc.basic", "Basic", lambda: self._set_scientific(False),
                        shortcut=("Ctrl", "1"), checked=lambda: not self.calculator.scientific),
                Command("calc.scientific", "Scientific", lambda: self._set_scientific(True),
                        shortcut=("Ctrl", "2"), checked=lambda: self.calculator.scientific),
            )),
            CommandGroup("", (
                Command("calc.copy", "Copy result", self._copy_result,
                        enabled=lambda: bool(self.calculator.expression or self.calculator.tape)),
                Command("calc.clear-tape", "Clear tape", self._clear_tape,
                        enabled=lambda: bool(self.calculator.tape)),
            )),
        ))

    def _build_keypad(self) -> None:
        self._keys = {}

        phone = self.on_phone
        self._ac_shown = self._ac_state()

        def make(spec):
            action, label, kind = spec
            icon = None
            if action == "ac" and self.calculator.expression:
                label = "C"
            if action == "ac" and phone and self._typing():
                icon = "delete"
            if action == "mode":
                icon = "square-function"
            if action == "deg":
                label = "Deg" if self.calculator.degrees else "Rad"
            if action in ("sin", "cos", "tan") and self.calculator.second:
                action, label = "a" + action, action + "⁻¹"
            if action == "ln" and self.calculator.second:
                action, label = "log2", "log₂"
            key = CalculatorKey(label, action=action, kind=kind,
                      active=(action == "deg" and self.calculator.degrees) or
                             (action == "2nd" and self.calculator.second),
                      wide=action == "0" and not phone,
                      accessible_label=("Delete" if icon == "delete" else
                                        "Clear" if action == "ac" and phone else KEY_LABELS.get(action)),
                      on_activate=self._press, phone=phone, icon=icon)
            key.set_name("calc-key-" + KEY_NAMES.get(action, action))
            self._keys[action] = key
            return key

        basic = [make(spec) for spec in (PHONE_BASIC_KEYS if phone else BASIC_KEYS)]
        scientific = [make(spec) for spec in SCIENTIFIC_KEYS]
        replacement = CalculatorKeypad(keys=basic, scientific_keys=scientific,
                             scientific=self.calculator.scientific, phone=phone)
        replacement.set_name("calc-keys")
        replacement.basic_bank.set_name("calc-basic")
        replacement.scientific_bank.set_name("calc-scientific")
        if self._keypad_container is not None:
            self.body_column.remove(self._keypad_container)
        self.keypad = replacement
        if self.calculator.scientific and not phone:
            viewport = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           hexpand=True)
            viewport.set_overlay_scrolling(True)
            viewport.set_propagate_natural_width(False)
            viewport.set_child(replacement)
            self._keypad_container = viewport
        else:
            self._keypad_container = replacement
        self.body_column.append(self._keypad_container)

    def _ac_state(self) -> str:
        return "back" if self._typing() else "C" if self.calculator.expression else "AC"

    def _typing(self) -> bool:
        """Something is being typed (not an answer): on a phone AC is the delete key meanwhile."""
        return bool(self.calculator.expression) and not self.calculator.fresh

    def _press(self, action: str) -> None:
        if action == "mode":
            self._open_mode_panel()
            return
        if action == "ac" and self.on_phone and self._typing():
            action = "back"
        self.calculator.press(action)
        if action in ("2nd", "deg"):
            self._build_keypad()
        self._refresh()

    def _swipe_delete(self) -> None:
        """v71: a sideways swipe on the display deletes the last digit."""
        self._press("back")

    def _open_mode_panel(self, *_args) -> None:
        """The phone's mode key grows a small panel: Basic, Scientific, Copy result, Clear history."""
        key = self._keys.get("mode")
        if key is None:
            return
        calculator = self.calculator
        key.add_css_class("on")

        def choose(scientific: bool):
            return lambda: self._set_scientific(scientific)

        panel = bar_menu(key, [
            BarAction("calculator", "Basic", on_activate=choose(False), active=not calculator.scientific),
            BarAction("square-function", "Scientific", on_activate=choose(True), active=calculator.scientific),
            None,
            BarAction("copy", "Copy result", on_activate=self._copy_result,
                      sensitive=bool(calculator.expression or calculator.tape)),
            BarAction("history", "Clear history", on_activate=self._clear_tape, sensitive=bool(calculator.tape)),
        ], label="Calculator mode", where="anchor", width_px=240)
        self._mode_panel = panel
        if hasattr(panel, "on_closed"):
            panel.on_closed = lambda: self._mode_closed(key)

    def _mode_closed(self, key: Gtk.Widget) -> None:
        self._mode_panel = None
        key.remove_css_class("on")

    def _refresh(self) -> None:
        self.display.set_expression(self.calculator.display)
        self.display.set_preview(self.calculator.preview)
        self.display.set_tape(self.calculator.tape)
        self.display.set_mode("deg" if self.calculator.degrees else "rad")
        self.display.set_error(self.calculator.error)
        if self.on_phone:
            if self._ac_state() != self._ac_shown:
                self._build_keypad()   # AC becomes the delete key while you type, and back
        elif "ac" in self._keys:
            self._keys["ac"].set_label("C" if self.calculator.expression else "AC")
        active = self.calculator.expression[-1:] if not self.calculator.fresh else ""
        for action in ("+", "−", "×", "÷"):
            if action in self._keys:
                if action == active:
                    self._keys[action].add_css_class("active")
                else:
                    self._keys[action].remove_css_class("active")

    def _reuse_tape(self, index: int) -> None:
        self.calculator.use_tape(index)
        self._refresh()

    def _set_scientific(self, enabled: bool) -> None:
        self.calculator.scientific = enabled
        if not self.on_phone:
            self.set_default_size(740 if enabled else 360, 600)
        self._build_keypad()
        self._refresh()

    def _copy_result(self) -> None:
        value = self.calculator.tape[0][1] if self.calculator.fresh and self.calculator.tape else None
        if value is None:
            try:
                from .engine import evaluate
                value = evaluate(self.calculator.expression, degrees=self.calculator.degrees)
            except Exception:
                return
        self.get_clipboard().set(format_decimal(value))

    def _clear_tape(self) -> None:
        self.calculator.tape.clear()
        self._refresh()

    def _key_pressed(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        key = Gdk.keyval_name(keyval) or ""
        if state & Gdk.ModifierType.CONTROL_MASK:
            if key in ("1", "2"):
                self._set_scientific(key == "2")
                return True
            return False
        if key.isdigit() and len(key) == 1:
            action = key
        elif key.startswith("KP_") and key[3:].isdigit() and len(key) == 4:
            action = key[3:]
        else:
            action = {"plus": "+", "minus": "−", "asterisk": "×", "x": "×", "slash": "÷",
                      "Return": "=", "KP_Enter": "=", "equal": "=", "period": ".", "comma": ".",
                      "BackSpace": "back", "Escape": "ac", "percent": "%",
                      "parenleft": "(", "parenright": ")", "asciicircum": "^",
                      "exclam": "!", "p": "π", "KP_Add": "+", "KP_Subtract": "−",
                      "KP_Multiply": "×", "KP_Divide": "÷", "KP_Decimal": "."}.get(key)
        if action is None:
            return False
        self._press(action)
        return True


class CalculatorApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        install_appkit()
        install_lumaui()
        icon_root = Path(__file__).resolve().parents[1] / "icons"
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(icon_root))
        css = os.environ.get("LUMA_CALC_STYLE_PATH") or str(Path(__file__).resolve().parents[1] / "style/calculator.css")
        add_style_sheet(css)

    def do_activate(self) -> None:
        (self.props.active_window or CalculatorWindow(self)).present()


def main() -> int:
    return CalculatorApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
