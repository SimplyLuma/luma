# SPDX-License-Identifier: Apache-2.0
"""LumaUI bar: BarEntry, a field you type into right in the resting bar.

The action center's bar usually holds actions, or a prompt that grows into
the editor (Charlie's reply). Some apps act by typing: Messages writes a
message, Tasks and Calendar add a thing in words, Terminal runs a command,
Ari takes a question. BarEntry is that field, in the same well as Charlie's
prompt (36 tall at 12, 13.5 text) and in the same wide bar (v70 `.mrow`,
`.aricomp`, `#tk-bar.adding`, `#tm-bar .tfield`, `#cal-bar .cadd`):

- **compose** (Messages, Ari): several lines, growing to 140 then
  scrolling. Return sends and Shift+Return is a new line; while an input
  method is composing, Return belongs to it. The key after the field is
  Send once there is text, and the microphone before (when the app records
  voice). With an editor on the action center, the well carries a grow
  button, and the draft moves into the editor on grow() and back on fold().
- **quick** (Tasks, Calendar): one line with a lead glyph, and chips at the
  end for what the app understood ("Tomorrow", "3 pm"). Return adds; Esc
  clears, then closes.
- **command** (Terminal): one line in the monospace face after a prompt
  prefix (`prefix=(cwd, branch)` draws the folder, the branch tag and `$`),
  a ghost suggestion that Tab or → accepts, and a ⏎ hint. `busy` names what
  is running.

The bar is as wide as the kind wants (`span`): narrow min(600, 100% − 32)
for quick, regular min(720, 100% − 56) for compose, wide min(860, 100% − 64)
for command; an app may name another span (Calendar's quick entry is broad,
min(880, 100% − 48)).
`close_label="Done"` adds the word key after the field that calls on_close.

    entry = BarEntry("compose", placeholder="Message Priya", on_submit=send, voice=record,
                     tools=[BarAction("smile", tooltip="Emoji", on_activate=emoji)])
    center.show_bar([BarAction("plus", tooltip="Attach", on_activate=attach), entry])
    entry.clear()                                   # after sending

    add = BarEntry("quick", icon="plus", placeholder="Add a task, like “Call Theo tomorrow 3pm”",
                   on_change=lambda t: add.set_chips(parse(t)), on_submit=create, on_close=done)
    run = BarEntry("command", prefix=("~/Projects/luma", "main"), on_change=complete, on_submit=run_command)
    run.set_suggestion("git status")                # shown after what is typed; Tab takes it

The app says what the field is for; the kit owns its size, colour, keys,
motion and accessibility. CSS: luma-appkit-bar.css, `/* LumaUI: Bar entry */`.
"""
from __future__ import annotations

from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import bar_tokens, icons, lumaui  # noqa: E402
from .action_center import ActionCenter, BarAction, make_control, register_item  # noqa: E402

__all__ = ["BarEntry", "ENTRY_KINDS", "ENTRY_SPANS", "chip_parts", "accept_suggestion", "is_submit", "prefix_parts"]

#: What a bar entry is for.
ENTRY_KINDS = ("compose", "quick", "command")
#: How wide the bar with a field is; each kind has its own by default.
ENTRY_SPANS = ("narrow", "regular", "wide", "broad")
_KIND_SPAN = {"compose": "regular", "quick": "narrow", "command": "wide"}


# ── pure helpers ────────────────────────────────────────────────────────────

def chip_parts(chip: object) -> tuple[str | None, str]:
    """(icon, label) for a chip given as "label" or (icon, "label")."""
    if isinstance(chip, str):
        return None, chip
    if isinstance(chip, tuple) and len(chip) == 2 and isinstance(chip[1], str):
        return chip[0], chip[1]
    raise TypeError(f"a chip is 'label' or (icon, 'label'): {chip!r}")


def prefix_parts(prefix: object) -> tuple[str, str | None, bool]:
    """(folder, branch, prompt sign?) for a command prefix: "text", or (cwd, branch) which ends in `$`."""
    if isinstance(prefix, str):
        return prefix, None, False
    if isinstance(prefix, tuple) and len(prefix) == 2 and isinstance(prefix[0], str):
        return prefix[0], prefix[1] or None, True
    raise TypeError(f"a command prefix is 'text' or (cwd, branch): {prefix!r}")


def accept_suggestion(text: str, suggestion: str | None) -> str:
    """What the field holds once the ghost suggestion is taken.

    A suggestion is either the rest of the command (shown after the text) or
    a whole command that starts with the text; anything else is ignored.
    """
    if not suggestion:
        return text
    if suggestion.startswith(text):
        return suggestion
    return text + suggestion


def ghost_tail(text: str, suggestion: str | None) -> str:
    """The part of the suggestion drawn after the typed text."""
    if not suggestion or not text:
        return ""
    return suggestion[len(text):] if suggestion.startswith(text) else suggestion


def is_submit(kind: str, keyval: int, state: Gdk.ModifierType, composing: bool) -> bool:
    """Does this key send? Return (not Shift+Return) when no input method is composing."""
    if composing or keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
        return False
    if kind == "compose" and state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK):
        return False
    return True


# ── the part ────────────────────────────────────────────────────────────────

class BarEntry:
    """A field in the resting bar. The object keeps the text; the bar draws it (see the module docstring).

    Callbacks: `on_change(text)` on every edit, `on_submit(text)` on Return
    (the field is not cleared for you: call clear() once it is done),
    `on_close()` on Esc (quick: after clearing), `on_stop()` for the Stop key
    while `busy` (compose), and `voice()` for the microphone key when the
    field is empty (compose).
    """

    def __init__(self, kind: str = "compose", *, placeholder: str = "", text: str = "", label: str | None = None,
                 icon: str | None = None, chips: Sequence[object] = (), prefix: object = None,
                 suggestion: str | None = None, busy: bool | str = False, tools: Sequence[BarAction] = (),
                 grows: bool = True, voice: Callable[[], None] | None = None, span: str | None = None,
                 flat: bool = False,
                 close_label: str | None = None,
                 on_change: Callable[[str], None] | None = None, on_submit: Callable[[str], None] | None = None,
                 on_close: Callable[[], None] | None = None, on_stop: Callable[[], None] | None = None,
                 on_accept_suggestion: Callable[[str], None] | None = None) -> None:
        if kind not in ENTRY_KINDS:
            raise ValueError(f"a bar entry is one of {ENTRY_KINDS}: {kind!r}")
        if not (label or placeholder or prefix):
            raise ValueError("a bar entry needs a label or a placeholder to name it")
        if prefix is not None and kind != "command":
            raise ValueError("only a command entry has a prompt prefix")
        if voice is not None and kind != "compose":
            raise ValueError("only a compose entry records voice")
        if flat and kind != "compose":
            raise ValueError("only a compose entry can lie flat in an action bar")
        for tool in tools:
            if not isinstance(tool, BarAction) or tool.label:
                raise TypeError("the tools inside a bar entry are icon-only BarActions")
        for chip in chips:
            chip_parts(chip)
        if prefix is not None:
            prefix_parts(prefix)
        span = span or _KIND_SPAN[kind]
        if span not in ENTRY_SPANS:
            raise ValueError(f"a bar entry spans one of {ENTRY_SPANS}: {span!r}")
        if close_label is not None and on_close is None:
            raise ValueError("a close key needs on_close")
        self.span, self.close_label, self.on_accept_suggestion = span, close_label, on_accept_suggestion
        self.kind, self.placeholder, self.label, self.icon = kind, placeholder, label or placeholder or "Command", icon
        self.flat = flat
        self.prefix, self.tools, self.grows, self.voice = prefix, list(tools), grows, voice
        self.on_change, self.on_submit, self.on_close, self.on_stop = on_change, on_submit, on_close, on_stop
        self._text, self._chips, self._suggestion, self._busy = text, list(chips), suggestion, busy
        self.widget: _EntryWidget | None = None

    # ── state the app changes ─────────────────────────────────────────────

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        """Replace the text (without calling `on_change`)."""
        self._text = text
        if self.widget is not None:
            self.widget.show_text(text)

    def clear(self) -> None:
        self.set_text("")

    @property
    def chips(self) -> list[object]:
        return list(self._chips)

    def set_chips(self, chips: Sequence[object]) -> None:
        """What the app understood from the text, as chips at the field's end (quick)."""
        for chip in chips:
            chip_parts(chip)
        self._chips = list(chips)
        if self.widget is not None:
            self.widget.show_chips()

    @property
    def suggestion(self) -> str | None:
        return self._suggestion

    def set_suggestion(self, suggestion: str | None) -> None:
        """The ghost completion after the text (command); Tab or → at the end takes it."""
        self._suggestion = suggestion
        if self.widget is not None:
            self.widget.show_ghost()

    @property
    def busy(self) -> bool | str:
        return self._busy

    def set_busy(self, busy: bool | str) -> None:
        """Compose: the send key becomes Stop. Command: the field says what is running."""
        self._busy = busy
        if self.widget is not None:
            self.widget.show_key()
            self.widget.show_placeholder()

    def set_placeholder(self, placeholder: str) -> None:
        self.placeholder = placeholder
        if self.widget is not None:
            self.widget.show_placeholder()

    def focus(self) -> None:
        if self.widget is not None:
            self.widget.focus_text()

    # ── what the widget reports ───────────────────────────────────────────

    def _edited(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        if self.on_change is not None:
            self.on_change(text)

    def _submit(self) -> None:
        if self._busy and self.kind == "compose":
            return
        if self.on_submit is not None and self._text.strip():
            self.on_submit(self._text)

    def _escape(self) -> bool:
        if self.kind == "quick" and self._text:
            self._edited("")
            self.set_text("")
            return True
        if self.on_close is not None:
            self.on_close()
            return True
        return False


class _EntryWidget(Gtk.Box):
    """The drawn field: the well, and for compose the send key after it."""

    __gtype_name__ = "LumaUIBarEntry"

    def __init__(self, entry: BarEntry) -> None:
        super().__init__(valign=Gtk.Align.CENTER, hexpand=True)
        bar_tokens.install()
        self.entry = entry
        self.bar_wide = True
        self.bar_span = tuple(int(bar_tokens.metric("entry", f"span_{entry.span}{part}"))
                              for part in ("", "_side", "_phone_side"))
        self.add_css_class("lumaui-bar-entry-row")
        self.add_css_class(entry.kind)
        if entry.flat:
            self.add_css_class("flat")
        self._syncing = False
        self._composing = False
        self.key: Gtk.Button | None = None
        self.grow_button: Gtk.Button | None = None

        self.well = bar_tokens.Well(hexpand=True)
        self.well.add_css_class("lumaui-bar-entry")
        if entry.flat:
            self.well.add_css_class("flat")
        self.well.add_css_class(entry.kind)
        self.append(self.well)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_a: self.focus_text())
        self.well.add_controller(click)

        if entry.icon:
            lead = Gtk.Button(valign=Gtk.Align.CENTER, tooltip_text=entry.label)
            lead.add_css_class("lumaui-bar-entry-lead")
            lead.set_child(icons.image(entry.icon))
            lead.update_property([Gtk.AccessibleProperty.LABEL], [entry.label])
            lead.set_focusable(False)
            lead.connect("clicked", lambda _b: entry._submit() if entry.text.strip() else self.focus_text())
            self.well.append(lead)
        if entry.prefix is not None:
            folder, branch, sign = prefix_parts(entry.prefix)
            line = Gtk.Box(valign=Gtk.Align.CENTER)
            line.add_css_class("lumaui-bar-entry-prefix-line")
            prefix = Gtk.Label(label=folder, ellipsize=Pango.EllipsizeMode.START, max_width_chars=28)
            prefix.add_css_class("lumaui-bar-entry-prefix")
            line.append(prefix)
            if branch:
                tag = Gtk.Box(valign=Gtk.Align.CENTER)
                tag.add_css_class("lumaui-bar-entry-branch")
                tag.append(icons.image("git-branch"))
                tag.append(Gtk.Label(label=branch))
                line.append(tag)
            if sign:
                dollar = Gtk.Label(label="$")
                dollar.add_css_class("lumaui-bar-entry-sign")
                line.append(dollar)
            self.well.append(line)
            self.prefix_label = prefix

        if entry.kind == "compose":
            self._build_compose()
        else:
            self._build_line()

        self.chip_box = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.chip_box.add_css_class("lumaui-bar-entry-chips")
        self.well.append(self.chip_box)
        for tool in entry.tools:
            button = make_control(tool, size="tool")
            button.add_css_class("lumaui-bar-entry-tool")
            button.set_valign(Gtk.Align.END)
            self.well.append(button)
        if entry.kind == "compose" and entry.grows:
            self.grow_button = make_control(BarAction("maximize-2", tooltip="Formatting and more room",
                                                      on_activate=self._grow), size="tool")
            self.grow_button.add_css_class("lumaui-bar-entry-tool")
            self.grow_button.set_valign(Gtk.Align.END)
            self.grow_button.set_visible(False)
            self.well.append(self.grow_button)
        if entry.kind == "command":
            hint = Gtk.Label(label="⏎", valign=Gtk.Align.CENTER, xalign=0)
            hint.add_css_class("lumaui-bar-entry-key-hint")
            hint.set_tooltip_text("Return to run")
            self.well.append(hint)
        if entry.close_label:
            close = Gtk.Button(label=entry.close_label, valign=Gtk.Align.CENTER)
            close.add_css_class("lumaui-bar-button")
            close.add_css_class("bar")
            close.add_css_class("lumaui-bar-entry-close")
            close.connect("clicked", lambda _b: entry.on_close())
            self.append(close)
            self.close_button = close
        if entry.kind == "compose":
            self.key = Gtk.Button(valign=Gtk.Align.END)
            self.key.add_css_class("lumaui-bar-send")
            self.key.connect("clicked", self._key_clicked)
            self.append(self.key)

        entry.widget = self
        self.show_text(entry.text)
        self.show_chips()
        self.show_placeholder()
        self.connect("map", self._mapped)

    # ── building ──────────────────────────────────────────────────────────

    def _build_compose(self) -> None:
        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                 valign=Gtk.Align.CENTER)
        pad_x, pad_y = (int(bar_tokens.metric("entry", k)) for k in ("compose_padding_x", "compose_padding_y"))
        self.view.set_left_margin(pad_x)
        self.view.set_right_margin(pad_x)
        self.view.set_top_margin(pad_y)
        self.view.set_bottom_margin(pad_y)
        self.view.add_css_class("lumaui-bar-entry-text")
        self.view.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.MULTI_LINE],
                                  [self.entry.label, True])
        self.view.connect("preedit-changed", self._preedit)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.view.add_controller(keys)
        self.buffer = self.view.get_buffer()
        self.buffer.connect("changed", self._buffer_changed)
        self.ph = Gtk.Label(xalign=0, valign=Gtk.Align.CENTER, halign=Gtk.Align.START, can_target=False,
                            ellipsize=Pango.EllipsizeMode.END)
        self.ph.add_css_class("lumaui-bar-entry-placeholder")
        overlay = Gtk.Overlay(child=self.view)
        overlay.add_overlay(self.ph)
        # The scroller only scrolls past the cap: a GTK scrollbar has a minimum
        # length that would hold a one-line field open (see _fit).
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                           hexpand=True, valign=Gtk.Align.CENTER)
        self.scroller.add_css_class("lumaui-bar-entry-scroll")
        self.scroller.set_child(overlay)
        self._fit_source = 0
        self.view.connect("notify::height", lambda *_a: self._queue_fit())
        self.well.append(self.scroller)
        self.text_widget: Gtk.Widget = self.view

    def _build_line(self) -> None:
        self.line = Gtk.Text(hexpand=True, valign=Gtk.Align.CENTER)
        self.line.add_css_class("lumaui-bar-entry-text")
        self.line.update_property([Gtk.AccessibleProperty.LABEL], [self.entry.label])
        if self.entry.kind == "command":
            self.line.set_input_hints(Gtk.InputHints.NO_SPELLCHECK | Gtk.InputHints.NO_EMOJI)
            self.line.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        self.line.connect("changed", lambda t: self._line_changed(t.get_text()))
        self.line.connect("activate", lambda _t: self.entry._submit())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.line.add_controller(keys)
        if self.entry.kind == "command":
            overlay = Gtk.Overlay(child=self.line, hexpand=True)
            ghost = Gtk.Box(can_target=False, valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
            self.ghost_typed = Gtk.Label()
            self.ghost_typed.add_css_class("lumaui-bar-entry-ghost-typed")
            self.ghost_rest = Gtk.Label()
            self.ghost_rest.add_css_class("lumaui-bar-entry-ghost")
            ghost.append(self.ghost_typed)
            ghost.append(self.ghost_rest)
            overlay.add_overlay(ghost)
            self.well.append(overlay)
        else:
            self.well.append(self.line)
        self.text_widget = self.line

    # ── showing the model ─────────────────────────────────────────────────

    def current_text(self) -> str:
        if self.entry.kind == "compose":
            return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)
        return self.line.get_text()

    def show_text(self, text: str) -> None:
        if self.current_text() == text:
            self._after_text()
            return
        self._syncing = True
        try:
            if self.entry.kind == "compose":
                self.buffer.set_text(text)
            else:
                self.line.set_text(text)
                self.line.set_position(-1)
        finally:
            self._syncing = False
        self._after_text()

    def show_placeholder(self) -> None:
        busy = self.entry.busy
        text = busy if isinstance(busy, str) and busy and self.entry.kind == "command" else self.entry.placeholder
        if self.entry.kind == "compose":
            self.ph.set_label(text)
            self.view.update_property([Gtk.AccessibleProperty.PLACEHOLDER], [text])
        else:
            self.line.set_placeholder_text(text)
            self.line.update_property([Gtk.AccessibleProperty.PLACEHOLDER], [text])
        lumaui.set_css_class(self, "busy", bool(busy))

    def show_chips(self) -> None:
        child = self.chip_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.chip_box.remove(child)
            child = following
        for chip in self.entry.chips:
            icon, label = chip_parts(chip)
            box = Gtk.Box(valign=Gtk.Align.CENTER)
            box.add_css_class("lumaui-bar-entry-chip")
            if icon:
                box.append(icons.image(icon))
            box.append(Gtk.Label(label=label))
            self.chip_box.append(box)
        self.chip_box.set_visible(bool(self.entry.chips))
        if self.entry.chips:
            names = ", ".join(chip_parts(c)[1] for c in self.entry.chips)
            self.text_widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [names])

    def show_ghost(self) -> None:
        if self.entry.kind != "command":
            return
        text = self.current_text()
        tail = ghost_tail(text, self.entry.suggestion)
        self.ghost_typed.set_label(text)
        self.ghost_rest.set_label(tail)
        self.ghost_rest.get_parent().set_visible(bool(tail))

    def show_key(self) -> None:
        if self.key is None:
            return
        entry, has_text = self.entry, bool(self.current_text().strip())
        if entry.busy:
            icon, name, go, sensitive = "square", "Stop", True, entry.on_stop is not None
        elif not has_text and entry.voice is not None:
            icon, name, go, sensitive = "mic", "Record a voice message", False, True
        else:
            icon, name, go, sensitive = "arrow-up", "Send", has_text, has_text
        self.key.set_child(icons.image(icon))
        self.key.set_tooltip_text(name)
        self.key.update_property([Gtk.AccessibleProperty.LABEL], [name])
        self.key.set_sensitive(sensitive)
        lumaui.set_css_class(self.key, "go", go)
        lumaui.set_css_class(self.key, "stop", bool(entry.busy))
        self.key.key_role = "stop" if entry.busy else "voice" if icon == "mic" else "send"

    def _queue_fit(self) -> None:
        if not self._fit_source:
            self._fit_source = GLib.idle_add(self._fit, priority=GLib.PRIORITY_LOW)

    def _fit(self) -> bool:
        """Grow with the text up to the cap, then scroll."""
        self._fit_source = 0
        cap = int(bar_tokens.metric("entry", "compose_max_height"))
        width = self.scroller.get_width()
        wanted = self.view.measure(Gtk.Orientation.VERTICAL, width if width > 0 else -1)[1]
        over = wanted > cap
        if wanted != getattr(self, "_fitted", None):
            self._fitted = wanted
            self.scroller.queue_resize()  # the text view measures lazily; make the well follow it
        policy = Gtk.PolicyType.AUTOMATIC if over else Gtk.PolicyType.NEVER
        if self.scroller.get_policy()[1] != policy:
            self.scroller.set_policy(Gtk.PolicyType.NEVER, policy)
            self.scroller.set_min_content_height(cap if over else -1)
            self.scroller.set_max_content_height(cap if over else -1)
            self.scroller.set_valign(Gtk.Align.FILL if over else Gtk.Align.CENTER)
        return False

    def _after_text(self) -> None:
        text = self.current_text()
        if self.entry.kind == "compose":
            self.ph.set_visible(not text)
            self._queue_fit()
        self.show_ghost()
        self.show_key()

    # ── events ────────────────────────────────────────────────────────────

    def _buffer_changed(self, _buffer: Gtk.TextBuffer) -> None:
        self._line_changed(self.current_text())

    def _line_changed(self, text: str) -> None:
        self._after_text()
        if not self._syncing:
            self.entry._edited(text)

    def _preedit(self, _view: Gtk.TextView, preedit: str) -> None:
        self._composing = bool(preedit)

    def _key(self, _controller: Gtk.EventControllerKey, keyval: int, _code: int,
             state: Gdk.ModifierType) -> bool:
        entry = self.entry
        if keyval == Gdk.KEY_Escape and not self._composing:
            return entry._escape()
        if entry.kind == "command" and keyval in (Gdk.KEY_Tab, Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_End):
            text = self.current_text()
            at_end = self.line.get_position() >= len(text)
            if ghost_tail(text, entry.suggestion) and at_end and not state & Gdk.ModifierType.SHIFT_MASK:
                taken = accept_suggestion(text, entry.suggestion)
                self.show_text(taken)
                entry._edited(taken)
                if entry.on_accept_suggestion is not None:
                    entry.on_accept_suggestion(taken)
                return True
            return False
        if entry.kind == "compose" and is_submit("compose", keyval, state, self._composing):
            entry._submit()
            return True
        return False

    def _key_clicked(self, _button: Gtk.Button) -> None:
        role = getattr(self.key, "key_role", "send")
        if role == "stop" and self.entry.on_stop is not None:
            self.entry.on_stop()
        elif role == "voice" and self.entry.voice is not None:
            self.entry.voice()
        else:
            self.entry._submit()

    def _center(self) -> ActionCenter | None:
        return self.get_ancestor(ActionCenter)

    def _grow(self) -> None:
        center = self._center()
        if center is not None and center.editor is not None:
            center.grow()

    def _mapped(self, _widget: Gtk.Widget) -> None:
        center = self._center()
        if self.grow_button is not None:
            self.grow_button.set_visible(center is not None and center.editor is not None)
        if self.entry.kind == "compose":
            # v70 .mrow: the bar's other keys stay at the foot as the field grows.
            parent = self.get_parent()
            child = parent.get_first_child() if parent is not None else None
            while child is not None:
                if child is not self:
                    child.set_valign(Gtk.Align.END)
                child = child.get_next_sibling()

    def focus_text(self) -> None:
        self.text_widget.grab_focus()
        if self.entry.kind != "compose":
            self.line.set_position(-1)

    # ── the action center's draft protocol ────────────────────────────────

    def carry_draft(self) -> str | None:
        return self.current_text() if self.entry.kind == "compose" else None

    def receive_draft(self, text: str) -> None:
        if self.entry.kind != "compose":
            return
        self.show_text(text)
        self.entry._edited(text)
        GLib.idle_add(lambda: (self.focus_text(), False)[1])

    def grab_focus(self) -> bool:  # fold() returns focus to the field, not the row
        self.focus_text()
        return True


register_item(BarEntry, lambda item, _size: _EntryWidget(item))
