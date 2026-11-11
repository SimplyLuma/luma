# SPDX-License-Identifier: Apache-2.0
"""Terminal on a TV: the same Terminal, at the couch.

In TV posture (docs/developer-platform/tv-mode.md) the window keeps its own
sessions, panes and shells; only the drawing changes (Design Center
mockups/luma-tv-terminal.html):

- a TV stage: the room is the person's wallpaper, held down by a shade
  (tv.terminal.shade), with the shared top bar showing only the clock: the
  screen is the terminal, so it has no title and no places;
- the shell's text straight on the room inside the safe area, no card, at TV
  reading size (tv.terminal.text_size) in the TV palette (ink, ansi);
- the TV action bar at the foot: the session (its menu lists the others), New
  session, then the keys a remote has no buttons for (the phone's keys row:
  esc, tab, ⌃C, ↑, ↓).

The shell takes the typing: the system keyboard docks for it (its Done is
Enter) and a keyboard plugged into the TV types straight in; while the system
keyboard is up the text stops above it. Escape or Back from the shell goes down
to the bar, Up from the bar returns to the shell, Back from the bar leaves for
the TV home with Terminal still running.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from luma_appkit import lumaui_tokens  # noqa: E402
from luma_appkit.commands import Command, CommandGroup, CommandRegistry  # noqa: E402

from .model import PHONE_KEYS  # noqa: E402

TV = lumaui_tokens.TV
TERMINAL = TV["terminal"]
#: The desktop's mono at 1080 lines, which text_size replaces on a TV.
DESKTOP_MONO = 13.5
#: The keys row a remote reaches: the phone's, less the symbols the command line takes.
TV_KEYS = tuple(key for key in PHONE_KEYS if not key.field)


def tv_font_scale(height: int) -> float:
    """The shell's text at TV reading size: text_size TV units over the desktop's mono."""
    return max(1.0, TERMINAL["text_size"] * (height or TV["layout"]["base_height"])
               / TV["layout"]["base_height"] / DESKTOP_MONO)


def tv_colours() -> tuple[Gdk.RGBA, Gdk.RGBA, list[Gdk.RGBA]]:
    """Ink, a clear background and the 16-colour palette the shell draws with on a TV."""
    def rgba(value: str) -> Gdk.RGBA:
        colour = Gdk.RGBA()
        colour.parse(value)
        return colour
    return rgba(TERMINAL["ink"]), rgba("rgba(0,0,0,0)"), [rgba(c) for c in TERMINAL["ansi"]]


def _sheet(_opaque: bool) -> str:
    top, bottom = TERMINAL["shade"]
    return f"""
.tm-tv-shade {{ background-image: linear-gradient(180deg, rgba(0, 0, 0, {top}), rgba(0, 0, 0, {bottom})); }}
"""


class TerminalOnTv:
    """The window's TV presentation; `active` while the window is in TV posture."""

    def __init__(self, window) -> None:
        self.window = window
        self.stage = None
        self.focus = None
        self.bar = None
        self._shell = None
        self._keyboard_up = False
        window.connect("notify::tv-posture", lambda *_a: self.follow())

    @property
    def active(self) -> bool:
        return self.stage is not None

    def follow(self) -> None:
        """Enter or leave the TV presentation with the window's posture."""
        window = self.window
        if bool(getattr(window, "tv_posture", False)) == self.active:
            return
        if self.active:
            self._leave()
        else:
            self._enter()
        window.tv_presentation_changed()

    # ── the stage ───────────────────────────────────────────────────────

    def _enter(self) -> None:
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_media import TvRoom
        from luma_appkit.tv_parts import TvTopBar
        from luma_appkit.tv_stage import TvStage, add_tv_style
        add_tv_style(_sheet)
        window = self.window
        stage = TvStage()
        room = TvRoom()
        room.set_wallpaper()
        stage.place(room, fill=True)
        shade = Gtk.Box(can_target=False)
        shade.add_css_class("tm-tv-shade")
        stage.place(shade, fill=True)
        top = TvTopBar()
        top.track.set_visible(False)   # one place, no tabs: the clock alone, where it is on every TV screen
        stage.place(top, **TvTopBar.PIN)
        window.center.set_visible(False)   # the desktop's bar; the TV's is the action bar
        window.set_body(stage)
        stage.place(window.host, **self._text_pin())
        stage.raise_child(top)
        stage.connect("notify::docked", lambda *_a: self._docked())
        self.stage = stage
        self.focus = TvFocus(stage, on_back=self._back)
        self.show_bar()
        self.follow_shell()

    def _leave(self) -> None:
        window = self.window
        stage, self.stage, self.focus, self.bar, self._shell = self.stage, None, None, None, None
        stage.remove(window.host)
        window.set_body(window.host)
        window.center.set_visible(True)

    def _text_pin(self) -> dict:
        safe = TV["layout"]["safe_x"]
        if self._keyboard_up:
            from luma_appkit.tv_keyboard import tv_keyboard_state
            _visible, height = tv_keyboard_state()
            bottom = height / (self.stage.unit if self.stage else 1.0) + TERMINAL["keyboard_gap"]
        else:
            bottom = TERMINAL["text_bottom"]
        return {"left": safe, "right": safe, "top": TERMINAL["text_top"], "bottom": bottom}

    def _docked(self) -> None:
        """The system keyboard docks for the shell: the text stops above it, the bar steps aside."""
        self._keyboard_up = self.stage.docked == "keyboard"
        self.stage.place(self.window.host, **self._text_pin())
        if self.bar is not None:
            self.bar.set_visible(not self._keyboard_up)

    # ── the shell under the remote ──────────────────────────────────────

    def follow_shell(self) -> None:
        """The active pane's shell is the console the remote types into; it has the focus."""
        if not self.active:
            return
        shell = self.window._active_terminal()
        if shell is self._shell:
            return
        if self._shell is not None:
            self.focus.remove(self._shell)
        self._shell = shell
        if shell is not None:
            self.focus.add(shell, group="shell", console=True)
            self.focus.focus(shell)

    def _back(self) -> bool:
        """Back from the shell comes down to the bar; from the bar it falls through to the TV home."""
        if self._shell is not None and self.focus.focused is self._shell and self.bar is not None:
            items = self.bar.items()
            if items:
                self.focus.focus(items[0])
                return True
        return False

    # ── the action bar ──────────────────────────────────────────────────

    def show_bar(self) -> None:
        """The session, New session | esc tab ⌃C ↑ ↓, redrawn as the session changes."""
        if not self.active:
            return
        from luma_appkit.tv_parts import TvActionBar
        window = self.window
        was_on_bar = self.bar is not None and self.focus.focused in self.bar.items()
        if self.bar is not None:
            self.focus.clear("bar")
            self.stage.remove(self.bar)
        bar = TvActionBar("Terminal")
        session = window.model.current
        sessions = bar.add_item(session.display_name, icon=window._session_icon())
        self.focus.add(sessions, group="bar", on_activate=lambda: self._open_sessions(sessions),
                       menu=self._sessions_menu)
        self.focus.add(bar.add_item("New session", icon="plus"), group="bar", on_activate=window.new_session)
        bar.add_divider()
        for key in TV_KEYS:
            item = bar.add_item("" if key.icon else key.label, icon=key.icon, label_for=key.name, mono=not key.icon)
            self.focus.add(item, group="bar", on_activate=lambda k=key: window._feed(k.send))
        self.stage.place(bar, **TvActionBar.PIN)
        bar.set_visible(not self._keyboard_up)
        self.bar = bar
        if was_on_bar:
            self.focus.focus(bar.items()[0])

    def _sessions_menu(self) -> CommandRegistry:
        """The sessions, the current one checked, then New session (the desktop's sessions menu)."""
        window = self.window
        rows = tuple(
            Command(f"term.tv.session-{s.id}", s.display_name, lambda sid=s.id: window.activate_session(sid),
                    "house" if s.display_name == "Home" else "folder",
                    checked=lambda sid=s.id: sid == window.model.current_id)
            for s in window.model.sessions)
        return CommandRegistry((
            CommandGroup(None, rows),
            CommandGroup(None, (Command("term.tv.new", "New session", window.new_session, "plus"),)),
        ))

    def _open_sessions(self, anchor: Gtk.Widget) -> None:
        from luma_appkit.tv_menu import TvMenu
        TvMenu.open(self.focus, anchor, self._sessions_menu())

