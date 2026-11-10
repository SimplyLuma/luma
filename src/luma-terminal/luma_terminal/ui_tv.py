# SPDX-License-Identifier: MPL-2.0
"""Terminal on a television: the same app, read from across the room, driven by a remote.

Design: the tv-luma/ Terminal board and its handoff (9 Nov 2026). The decisions
it settled hold here:

* The log is the screen. The session rail (sessions, then Compare, Actions,
  Lock) is revealed above the log, pushing it down: Up from the top of the log,
  or OK (a raw log has no row to open).
* No splitting. Compare shows exactly two sessions, picked from the rail; Left
  and Right choose a pane, OK opens it full size, Back leaves.
* No typing, and nothing destructive within reach. Sessions start only from the
  saved scripts written on a computer (~/.config/luma-terminal/tv-scripts.json).
  Every write lives in Actions, the TV starts locked, and Stop is held, not
  confirmed.
* Left / Right jump to the previous / next failure. This Terminal's sessions
  are raw text, so a failure is a line that reads like one (error, failed,
  traceback, ...); a structured session would test its rows' verbs instead.
"""
from __future__ import annotations

import os
import signal
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from luma_appkit import lumaui_tokens  # noqa: E402

from .tv_scripts import load_scripts  # noqa: E402
from luma_appkit.tv_focus import TvFocus  # noqa: E402
from luma_appkit.tv_parts import TvButton, TvTopBar  # noqa: E402
from luma_appkit.tv_stage import TvStage, add_tv_style  # noqa: E402

# A line that reads like a failure (PCRE2, caseless, multiline as VTE requires).
FAILURE = r"(?i)\b(error|errors|failed|failure|fatal|traceback|exception|panic|denied|segfault)\b|✗|✘"
PCRE2_CASELESS, PCRE2_MULTILINE, PCRE2_UTF = 0x00000008, 0x00000400, 0x00080000
# Ten-foot measures and colours (TV units on a 1920x1080 stage): tv.terminal in
# config/shared/design-tokens.json.
T = lumaui_tokens.TV["terminal"]
HOLD_MS = T["hold_ms"]
LOG_FONT = T["log_size"]
SAFE_X = lumaui_tokens.TV["layout"]["safe_x"]
LOG_TOP, RAIL_H, RAIL_GAP, BOTTOM = T["log_top"], T["rail_height"], T["rail_gap"], T["bottom"]
STATE_WORDS = {"running": ("Running", "tmtv-ok"), "ended": ("Ended", "tmtv-quiet"),
               "failed": ("Failed", "tmtv-fail"), "stopped": ("Stopped", "tmtv-warn")}


def _sheet(_opaque: bool) -> str:
    return f"""
.tmtv {{ background-color: {T['panel']}; }}
.tmtv-title {{ font-size: 40tv; font-weight: 700; color: {T['ink']}; }}
.tmtv-chip {{ font-size: 22tv; font-weight: 650; padding: 4tv 14tv; border-radius: 14tv; background-color: {T['chip']}; }}
.tmtv label.tmtv-ok, label.tmtv-chip.tmtv-ok {{ color: {T['ok']}; }}
.tmtv label.tmtv-fail, label.tmtv-chip.tmtv-fail {{ color: {T['fail']}; }}
.tmtv label.tmtv-warn, label.tmtv-chip.tmtv-warn {{ color: {T['warn']}; }}
.tmtv label.tmtv-quiet, label.tmtv-chip.tmtv-quiet {{ color: {T['quiet']}; }}
.tmtv label.tmtv-lock {{ font-size: 22tv; font-weight: 650; color: {T['quiet']}; }}
.tmtv label.tmtv-lock.tmtv-unlocked {{ color: {T['pick']}; letter-spacing: 1tv; }}
.tmtv-pane-head {{ font-size: 26tv; font-weight: 650; color: {T['ink']}; }}
.tmtv-pane {{ border-radius: 20tv; border: 3tv solid transparent; padding: 6tv; background-color: {T['log']}; }}
.tmtv-pane.tmtv-picked {{ border-color: {T['pick']}; }}
.tmtv-hint {{ font-size: 22tv; color: {T['quiet']}; }}
.tmtv-empty-title {{ font-size: 46tv; font-weight: 700; color: {T['ink']}; }}
.tmtv-empty-body {{ font-size: 28tv; color: {T['quiet']}; }}
.tmtv-scrim {{ background-color: {T['scrim']}; }}
.tmtv-sheet {{ background-color: {T['sheet']}; border-radius: 36tv; padding: 36tv; border-spacing: 14tv; }}
.tmtv-sheet-title {{ font-size: 36tv; font-weight: 700; color: {T['ink']}; }}
.tmtv-sheet-body {{ font-size: 24tv; color: {T['quiet']}; }}
.tmtv-off {{ opacity: 0.38; }}
.tmtv-hold {{ min-height: 10tv; }}
.tmtv-hold trough {{ min-height: 10tv; border-radius: 5tv; background-color: {T['track']}; }}
.tmtv-hold progress {{ min-height: 10tv; border-radius: 5tv; background-color: {T['fail']}; }}
"""


class Session:
    def __init__(self, tv: "TerminalTV", script: dict) -> None:
        self.tv = tv
        self.script = script
        self.name = script["name"]
        self.state = "running"
        self.pid = 0
        self.vte = tv.Vte.Terminal()
        vte = self.vte
        vte.set_input_enabled(False)
        vte.set_focusable(False)
        vte.set_can_target(False)
        vte.set_scrollback_lines(20000)
        vte.set_scroll_on_output(True)
        vte.set_mouse_autohide(True)
        vte.add_css_class("tmtv-log")
        vte.connect("child-exited", self._exited)
        vte.connect("realize", lambda *_a: self.tv.style_vte(vte))
        regex = tv.Vte.Regex.new_for_search(FAILURE, -1, PCRE2_MULTILINE | PCRE2_CASELESS | PCRE2_UTF)
        vte.search_set_regex(regex, 0)
        vte.search_set_wrap_around(False)
        self.pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.pane.add_css_class("tmtv-pane")
        vte.set_hexpand(True)
        vte.set_vexpand(True)
        self.pane.append(vte)
        self.spawn()

    def spawn(self) -> None:
        cwd = os.path.expanduser(self.script.get("cwd") or "~")
        if not Path(cwd).is_dir():
            cwd = str(Path.home())
        self.state = "running"

        def spawned(_vte, pid, error, *_unused):
            if error is not None or pid <= 0:
                self.state = "failed"
                self.vte.feed(f"\r\nThis script could not start: {error.message if error else 'unknown'}\r\n".encode())
            else:
                self.pid = pid
            self.tv.refresh()

        self.vte.spawn_async(pty_flags=self.tv.Vte.PtyFlags.DEFAULT, working_directory=cwd,
                             argv=["/bin/bash", "-lc", self.script["command"]], envv=None,
                             spawn_flags=GLib.SpawnFlags.DEFAULT, child_setup=None, timeout=-1,
                             cancellable=None, callback=spawned)

    def _exited(self, _vte, status: int) -> None:
        self.pid = 0
        if self.state != "stopped":
            code = os.waitstatus_to_exitcode(status) if status >= 0 else status
            self.state = "ended" if code == 0 else "failed"
            self.vte.feed(f"\r\n\x1b[2m— ended{'' if code == 0 else f' with exit {code}'} —\x1b[0m\r\n".encode())
        self.tv.refresh()

    def stop(self) -> None:
        if self.pid > 0:
            for sig in (signal.SIGHUP, signal.SIGTERM):
                try:
                    os.killpg(os.getpgid(self.pid), sig)
                except (ProcessLookupError, PermissionError):
                    pass
        self.state = "stopped"
        self.vte.feed(b"\r\n\x1b[33m\xe2\x80\x94 stopped from the TV \xe2\x80\x94\x1b[0m\r\n")
        self.tv.refresh()

    # the log: follow, scroll, failures
    def at_top(self) -> bool:
        adj = self.vte.get_vadjustment()
        return adj.get_value() <= adj.get_lower() + 0.5

    def at_bottom(self) -> bool:
        adj = self.vte.get_vadjustment()
        return adj.get_value() >= adj.get_upper() - adj.get_page_size() - 0.5

    def scroll(self, lines: float) -> None:
        adj = self.vte.get_vadjustment()
        top = adj.get_upper() - adj.get_page_size()
        adj.set_value(max(adj.get_lower(), min(top, adj.get_value() + lines)))

    def follow(self, on: bool) -> None:
        self.vte.set_scroll_on_output(on)
        if on:
            self.scroll(1e9)


class TerminalTV:
    def __init__(self, window) -> None:
        self.window = window
        self.Vte = window._vte()
        add_tv_style(_sheet)
        self.stage = TvStage()
        self.stage.add_css_class("tmtv")
        self.sessions: list[Session] = []
        self.current: Session | None = None
        self.compare: list[Session] | None = None
        self.picked = 0
        self.locked = True
        self.following = True
        self.rail_open = False
        self.sheet = None
        self._hold = None

        # the head (session name, state, lock) where a TV app's tabs sit; weather | time at the right
        self.bar = TvTopBar()
        self.bar.track.set_visible(False)
        head = Gtk.Box(spacing=18, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("tmtv-title")
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.chip = Gtk.Label()
        self.chip.add_css_class("tmtv-chip")
        self.lock_label = Gtk.Label()
        self.lock_label.add_css_class("tmtv-lock")
        head.append(self.title)
        head.append(self.chip)
        head.append(self.lock_label)
        self.bar.prepend(head)
        self.stage.place(self.bar, **TvTopBar.PIN)

        # the rail: sessions, then Compare, Actions, Lock
        self.rail = Gtk.Box(spacing=16)
        self.rail.set_visible(False)
        self.stage.place(self.rail, left=SAFE_X, right=SAFE_X, top=LOG_TOP, height=RAIL_H)

        self.log_holder = Gtk.Box()
        self.stage.place(self.log_holder, left=SAFE_X, right=SAFE_X, top=LOG_TOP, bottom=BOTTOM)
        self.empty = self._empty_state()
        self.stage.place(self.empty, left=SAFE_X, right=SAFE_X, top=T["empty_top"], height=T["empty_height"])
        self.hint = Gtk.Label(xalign=0)
        self.hint.add_css_class("tmtv-hint")
        self.stage.place(self.hint, left=SAFE_X, right=SAFE_X, bottom=T["hint_bottom"], height=T["hint_height"])

        self.focus = TvFocus(self.stage, on_back=self.back)
        self.focus.add_filter(self._filter)
        release = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        release.connect("key-released", self._released)
        window.add_controller(release)
        self.stage.connect("notify::unit", lambda *_a: [self.style_vte(s.vte) for s in self.sessions])
        self.scripts = load_scripts()
        self.refresh()

    # ── look ────────────────────────────────────────────────────────────

    def style_vte(self, vte) -> None:
        unit = self.stage.unit or 1.0
        font = Pango.FontDescription.from_string("Monospace")
        font.set_absolute_size(LOG_FONT * unit * Pango.SCALE)
        vte.set_font(font)
        # A palette for a dark panel at ten feet: the desktop's verb hues, lifted, never a dim blue.
        def rgba(value):
            colour = Gdk.RGBA()
            colour.parse(value)
            return colour
        vte.set_colors(rgba(T["log_ink"]), rgba(T["log"]), [rgba(c) for c in T["ansi"]])

    def _empty_state(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.START)
        title = Gtk.Label(label="Nothing is running", xalign=0)
        title.add_css_class("tmtv-empty-title")
        body = Gtk.Label(label="Press OK, then Actions, to run one of the scripts saved on your computer.",
                         xalign=0, wrap=True)
        body.add_css_class("tmtv-empty-body")
        box.append(title)
        box.append(body)
        return box

    def refresh(self) -> None:
        s = self.current
        if self.compare:
            self.title.set_label("Comparing")
            self.chip.set_visible(False)
        elif s is None:
            self.title.set_label("Terminal")
            self.chip.set_visible(False)
        else:
            self.title.set_label(s.name)
            word, css = STATE_WORDS[s.state]
            self.chip.set_label(word if self.following or s.state != "running" else word + " · frozen")
            for name in ("tmtv-ok", "tmtv-fail", "tmtv-warn", "tmtv-quiet"):
                self.chip.remove_css_class(name)
            self.chip.add_css_class(css)
            self.chip.set_visible(True)
        self.lock_label.set_label("Locked" if self.locked else "UNLOCKED")
        (self.lock_label.remove_css_class if self.locked else self.lock_label.add_css_class)("tmtv-unlocked")
        self.empty.set_visible(not self.sessions)
        if self.compare:
            self.hint.set_label("◀ ▶ pane   ·   OK open full size   ·   Back leave Compare")
        elif self.rail_open:
            self.hint.set_label("◀ ▶ choose   ·   OK open   ·   ▼ or Back close")
        elif self.sessions:
            self.hint.set_label("◀ ▶ previous / next failure   ·   ▲ ▼ scroll   ·   OK sessions   ·   ☰ Actions   ·   ⏯ follow")
        else:
            self.hint.set_label("OK sessions and Actions")
        if self.rail_open:
            self._build_rail()
        self._layout()

    def _layout(self) -> None:
        top = LOG_TOP + (RAIL_H + RAIL_GAP if self.rail_open else 0)
        self.rail.set_visible(self.rail_open)
        self.stage.place(self.log_holder, left=SAFE_X, right=SAFE_X, top=top, bottom=BOTTOM)
        while child := self.log_holder.get_first_child():
            self.log_holder.remove(child)
        shown = self.compare or ([self.current] if self.current else [])
        self.log_holder.set_spacing(32)
        self.log_holder.set_homogeneous(True)
        for index, session in enumerate(shown):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
            if self.compare:
                head = Gtk.Box(spacing=14)
                name = Gtk.Label(label=session.name, xalign=0)
                name.add_css_class("tmtv-pane-head")
                word, css = STATE_WORDS[session.state]
                chip = Gtk.Label(label=word)
                chip.add_css_class("tmtv-chip")
                chip.add_css_class(css)
                head.append(name)
                head.append(chip)
                column.append(head)
            parent = session.pane.get_parent()
            if parent is not None:
                parent.remove(session.pane)
            session.pane.set_vexpand(True)
            (session.pane.add_css_class if self.compare and index == self.picked
             else session.pane.remove_css_class)("tmtv-picked")
            column.append(session.pane)
            self.log_holder.append(column)

    # ── the rail ────────────────────────────────────────────────────────

    def _build_rail(self) -> None:
        self.focus.clear("rail")
        while child := self.rail.get_first_child():
            self.rail.remove(child)
        items = []
        for session in self.sessions:
            word, _css = STATE_WORDS[session.state]
            button = TvButton(f"{session.name} · {word}", icon="terminal")
            items.append((button, lambda s=session: self._show(s)))
        if len(self.sessions) >= 2:
            items.append((TvButton("Compare", icon="columns-2"), self._start_compare))
        items.append((TvButton("Actions", icon="list"), self.open_actions))
        items.append((TvButton("Unlock" if self.locked else "Lock", icon="lock-open" if self.locked else "lock"),
                      self._toggle_lock))
        first = None
        for button, action in items:
            self.rail.append(button)
            self.focus.add(button, group="rail", on_activate=action, on_key=self._rail_key)
            if first is None or (self.current and button.label.get_label().startswith(self.current.name + " ·")):
                first = button
        self._rail_first = first

    def _rail_key(self, direction: str) -> bool:
        if direction == "down":
            self.close_rail()
            return True
        return direction == "up"

    def open_rail(self) -> None:
        self.rail_open = True
        self.refresh()
        self.focus.focus(self._rail_first)

    def close_rail(self) -> None:
        self.rail_open = False
        self.focus.clear("rail")
        self.focus.focus(None)
        self.refresh()

    def _show(self, session: Session) -> None:
        self.compare = None
        self.current = session
        self.following = True
        session.follow(True)
        self.close_rail()

    def _start_compare(self) -> None:
        others = [s for s in self.sessions if s is not self.current]
        if not self.current or not others:
            return
        self.compare = [self.current, others[0]]
        self.picked = 0
        self.close_rail()

    def _toggle_lock(self) -> None:
        self.locked = not self.locked
        self.refresh()
        self.focus.focus(self._rail_first)

    # ── Actions ─────────────────────────────────────────────────────────

    def open_actions(self) -> None:
        s = self.current
        rows = [("Unlock" if self.locked else "Lock", "lock-open" if self.locked else "lock", False,
                 self._lock_from_sheet)]
        rows.append(("Run a saved script", "play", self.locked, self._open_scripts))
        rows.append(("Run this again", "rotate-cw", self.locked or s is None or s.state == "running", self._run_again))
        rows.append(("Stop this session · hold OK", "square", self.locked or s is None or s.state != "running", None))
        body = ("Locked: anyone in the room can watch, nobody can change anything." if self.locked
                else "Unlocked. Every action here re-runs safely, except Stop, which you hold.")
        self._sheet_open("Actions", body, rows)

    def _open_scripts(self) -> None:
        rows = [(script["name"], "terminal", False, lambda sc=script: self._run(sc)) for script in self.scripts]
        self._sheet_open("Run a saved script", "Saved from Terminal on a computer: Save for TV", rows)

    def _sheet_open(self, title: str, body: str, rows) -> None:
        self._sheet_close()
        scrim = Gtk.Box()
        scrim.add_css_class("tmtv-scrim")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("tmtv-sheet")
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("tmtv-sheet-title")
        text = Gtk.Label(label=body, xalign=0, wrap=True)
        text.add_css_class("tmtv-sheet-body")
        box.append(heading)
        box.append(text)
        buttons, first, self._stop_button = [], None, None
        self._hold_bar = Gtk.ProgressBar()
        self._hold_bar.add_css_class("tmtv-hold")
        self._hold_bar.set_visible(False)
        for label, icon, off, action in rows:
            button = TvButton(label, icon=icon, hexpand=True, danger=action is None)
            if off:
                button.add_css_class("tmtv-off")
            box.append(button)
            if action is None:
                self._stop_button = button
                box.append(self._hold_bar)
                activate = (lambda: None) if not off else (lambda: self._toast("Unlock first, or nothing is running"))
            elif off:
                activate = lambda: self._toast("Unlock first")
            else:
                activate = action
            self.focus.add(button, group="sheet", on_activate=activate)
            buttons.append(button)
            if first is None and not off:
                first = button
        self.stage.place(scrim, fill=True)
        self.stage.place(box, center_x=True, top=200, width=760)
        self.sheet = (scrim, box, buttons)
        self.focus.push_scope(buttons, on_back=self._sheet_back)
        self.focus.focus(first or buttons[0])

    def _sheet_back(self) -> bool:
        self._sheet_close()
        return False

    def _sheet_close(self) -> None:
        if not self.sheet:
            return
        scrim, box, buttons = self.sheet
        self.sheet = None
        self._cancel_hold()
        if self.focus.in_scope:
            self.focus.pop_scope()
        self.focus.clear("sheet")
        self.stage.remove(box)
        self.stage.remove(scrim)
        if self.rail_open:
            self.focus.focus(self._rail_first)

    def _lock_from_sheet(self) -> None:
        self.locked = not self.locked
        self.refresh()
        self.open_actions()

    def _run(self, script: dict) -> None:
        names = {s.name for s in self.sessions}
        script = dict(script)
        base, n = script["name"], 2
        while script["name"] in names:
            script["name"], n = f"{base} {n}", n + 1
        session = Session(self, script)
        self.sessions.append(session)
        self._sheet_close()
        self._show(session)

    def _run_again(self) -> None:
        if self.current and self.current.state != "running":
            self.current.vte.feed(b"\r\n\x1b[2m\xe2\x80\x94 run again \xe2\x80\x94\x1b[0m\r\n")
            self.current.spawn()
        self._sheet_close()
        self.refresh()

    def _toast(self, text: str, lead: str = "lock") -> None:
        from luma_appkit.tv_toast import TvToast
        TvToast.show(self.stage, text, lead=lead)

    # ── holding OK to stop ──────────────────────────────────────────────

    def _start_hold(self) -> None:
        if self._hold or not self.current or self.current.state != "running" or self.locked:
            return
        start = GLib.get_monotonic_time()
        self._hold_bar.set_visible(True)

        def tick() -> bool:
            done = min(1.0, (GLib.get_monotonic_time() - start) / 1000 / HOLD_MS)
            self._hold_bar.set_fraction(done)
            if done >= 1.0:
                self._hold = None
                self.current.stop()
                self._sheet_close()
                return False
            return True

        self._hold = GLib.timeout_add(16, tick)

    def _cancel_hold(self) -> None:
        if self._hold:
            GLib.source_remove(self._hold)
            self._hold = None
        if getattr(self, "_hold_bar", None) is not None:
            self._hold_bar.set_fraction(0)
            self._hold_bar.set_visible(False)

    def _released(self, _controller, keyval, _keycode, _state) -> None:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_Select):
            self._cancel_hold()

    # ── the log's keys ──────────────────────────────────────────────────

    def _filter(self, keyval: int, _state) -> bool:
        ok = keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_Select)
        menu = keyval in (Gdk.KEY_Menu, Gdk.KEY_MenuKB)
        if self.sheet:
            if ok and self._stop_button is not None and self.focus.focused is self._stop_button:
                self._start_hold()
                return True
            return False
        if menu:
            self.open_actions()
            return True
        if self.rail_open:
            return False
        if self.compare:
            if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
                self.picked = 1 - self.picked
                self._layout()
                return True
            if ok:
                self._show(self.compare[self.picked])
                return True
            return False
        s = self.current
        if ok:
            self.open_rail()
            return True
        if keyval in (Gdk.KEY_AudioPlay, Gdk.KEY_AudioPause, Gdk.KEY_p, Gdk.KEY_P) and s:
            self.following = not self.following
            s.follow(self.following)
            self.refresh()
            return True
        if s is None:
            if keyval == Gdk.KEY_Up:
                self.open_rail()
                return True
            return False
        rows = max(4, int(s.vte.get_row_count() * 0.6))
        if keyval == Gdk.KEY_Up:
            if s.at_top():
                self.open_rail()
            else:
                self.following = False
                s.follow(False)
                s.scroll(-rows)
                self.refresh()
            return True
        if keyval == Gdk.KEY_Down:
            s.scroll(rows)
            if s.at_bottom() and not self.following:
                self.following = True
                s.follow(True)
            self.refresh()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            self.following = False
            s.follow(False)
            found = s.vte.search_find_previous() if keyval == Gdk.KEY_Left else s.vte.search_find_next()
            if not found:
                self._toast("No earlier failure" if keyval == Gdk.KEY_Left else "No later failure", lead="search")
            self.refresh()
            return True
        return False

    def back(self) -> bool:
        if self.sheet:
            self._sheet_close()
            return True
        if self.rail_open:
            self.close_rail()
            return True
        if self.compare:
            self.compare = None
            self.refresh()
            return True
        return False
