"""Private compositor smoke check: real shell spawn and command delivery.

Run under a headless Wayland compositor; never on the user's live display.
"""
from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from luma_terminal.terminal import TerminalApplication


app = TerminalApplication()
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
status = 1


def inspect() -> bool:
    global status
    window = app.props.active_window
    terminal = window._active_terminal() if window is not None else None
    content = terminal.get_text_format(window._vte().Format.TEXT) if terminal is not None else ""
    if (content and "LUMA_TERM_SMOKE_OK" in content and "LUMA_TYPED_OK" in content
            and "LUMA_PASTED_OK" in content):
        print("VTE spawn and command delivery: OK")
        terminal.select_all()
        GLib.timeout_add(100, inspect_selection)
    else:
        print("VTE command output missing", file=sys.stderr)
        app.quit()
    return False


def inspect_selection() -> bool:
    global status
    window = app.props.active_window
    terminal = window._active_terminal() if window is not None else None
    if (terminal is not None and terminal.get_has_selection()
            and window._live_selection_pane_id == window.model.active_pane_id):
        print("VTE selection action bar: OK")
        window._clear_block()
        if not terminal.get_has_selection() and window._live_selection_pane_id is None:
            print("VTE selection dismissed: OK")
            window.split("row")
            if len(window.model.current.panes) == 2 and window._active_terminal() is not None:
                for pane in window.model.current.panes:
                    button = window._pane_close_buttons[pane.id]
                    controllers = window.pane_views[pane.id].observe_controllers()
                    motion = next((controllers.get_item(i) for i in range(controllers.get_n_items())
                                   if isinstance(controllers.get_item(i), Gtk.EventControllerMotion)), None)
                    if motion is None:
                        print("Pane hover controller missing", file=sys.stderr)
                        app.quit()
                        return False
                    motion.emit("enter", 5.0, 5.0)
                    if not button.get_visible():
                        print("Pane close did not reveal on hover", file=sys.stderr)
                        app.quit()
                        return False
                    motion.emit("leave")
                    if button.get_visible():
                        print("Pane close stayed visible after hover", file=sys.stderr)
                        app.quit()
                        return False
                print("Both pane close controls reveal on hover: OK")
                GLib.timeout_add(500, send_in_split)
                return False
    if status != 0:
        print("VTE selection action bar failed", file=sys.stderr)
    app.quit()
    return False


def send_in_split() -> bool:
    window = app.props.active_window
    window._send_command("printf LUMA_SPLIT_OK")
    GLib.timeout_add(600, inspect_split)
    return False


def inspect_split() -> bool:
    global status
    window = app.props.active_window
    terminal = window._active_terminal()
    content = terminal.get_text_format(window._vte().Format.TEXT)
    if "LUMA_SPLIT_OK" not in content:
        print("Split VTE command output missing", file=sys.stderr)
        app.quit()
        return False
    print("Split VTE command delivery: OK")
    first = window.model.current.panes[0].id
    surviving = window._active_terminal()
    window._pane_close_buttons[first].emit("clicked")
    if len(window.model.current.panes) != 1 or window._active_terminal() is not surviving:
        print("Pane close replaced the surviving shell", file=sys.stderr)
        app.quit()
        return False
    print("Hover close preserves surviving shell: OK")
    cwd = window.model.active.cwd
    window.new_session()
    if (len(window.model.sessions) == 2 and window.model.active.cwd == cwd
            and window._active_terminal() is not None):
        print("New session inherits active folder: OK")
        status = 0
    else:
        print("New session failed", file=sys.stderr)
    app.quit()
    return False


def send() -> bool:
    window = app.props.active_window
    if window is None or window._active_terminal() is None:
        print("VTE window did not open", file=sys.stderr)
        app.quit()
        return False
    terminal = window._active_terminal()
    if window.get_focus() is not terminal:
        print("VTE does not own keyboard focus", file=sys.stderr)
        app.quit()
        return False
    if any(child.get_name() == "tm-command" for child in window._action_widgets.values()):
        print("Bottom command input is still present", file=sys.stderr)
        app.quit()
        return False
    if window.has_css_class("luma-treatment-dark"):
        color = terminal.get_color_background_for_draw()
        if tuple(round(value * 255) for value in (color.red, color.green, color.blue)) != (17, 19, 21):
            print(f"VTE pane has wrong v70 color: {color.to_string()}", file=sys.stderr)
            app.quit()
            return False
        print("Exact dark v70 VTE background: OK")
    print("VTE keyboard focus and input-only pane: OK")
    terminal.feed_child(b"printf LUMA_TYPED_OK")
    terminal.feed_child(b"\n")
    window._send_command("printf LUMA_TERM_SMOKE_OK")
    window.get_clipboard().set("printf LUMA_PASTED_OK")
    GLib.timeout_add(100, paste)
    return False


def paste() -> bool:
    window = app.props.active_window
    window.paste()
    GLib.timeout_add(200, enter_pasted_command)
    return False


def enter_pasted_command() -> bool:
    window = app.props.active_window
    window._active_terminal().feed_child(b"\n")
    GLib.timeout_add(900, inspect)
    return False


GLib.timeout_add(800, send)
app.run([])
raise SystemExit(status)
