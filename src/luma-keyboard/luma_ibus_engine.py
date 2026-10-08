#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Native IBus bridge for Luma's AOSP LatinIME decoder.

This process owns composition and candidates only. Rendering stays inside
Luma Shell/GNOME Shell, and passwords, PINs, private fields, terminals, URLs,
email addresses, and fields that inhibit spellcheck bypass it completely.
"""

from __future__ import annotations

import os
import subprocess
import sys

import gi

gi.require_version("IBus", "1.0")
from gi.repository import Gio, GLib, IBus  # noqa: E402


ENGINE_NAME = "luma-latin"
APPROPRIATE_FOR_AUTOCORRECTION = 0x10000000
KIND_MASK = 0xFF
KIND_CORRECTION = 1
SENSITIVE_HINTS = (
    IBus.InputHints.HIDDEN_TEXT
    | IBus.InputHints.PRIVATE
    | IBus.InputHints.NO_SPELLCHECK
)
PASSTHROUGH_PURPOSES = {
    IBus.InputPurpose.PASSWORD,
    IBus.InputPurpose.PIN,
    IBus.InputPurpose.TERMINAL,
    IBus.InputPurpose.URL,
    IBus.InputPurpose.EMAIL,
    IBus.InputPurpose.PHONE,
    IBus.InputPurpose.DIGITS,
    IBus.InputPurpose.NUMBER,
}
TOUCH_DBUS_XML = """
<node>
  <interface name="org.project_luma.Keyboard1">
    <method name="Touch">
      <arg name="character" type="s" direction="in"/>
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
      <arg name="monotonic_ms" type="x" direction="in"/>
    </method>
    <method name="Key">
      <arg name="keyval" type="u" direction="in"/>
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
      <arg name="monotonic_ms" type="x" direction="in"/>
    </method>
    <method name="AcceptCandidate">
      <arg name="index" type="u" direction="in"/>
    </method>
    <method name="Compose">
      <arg name="character" type="s" direction="in"/>
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
      <arg name="monotonic_ms" type="x" direction="in"/>
      <arg name="accepted" type="b" direction="out"/>
    </method>
    <method name="Backspace">
      <arg name="tracked" type="b" direction="out"/>
    </method>
    <method name="Resolve">
      <arg name="autocorrect" type="b" direction="in"/>
      <arg name="replacement" type="s" direction="out"/>
      <arg name="original_length" type="u" direction="out"/>
    </method>
    <method name="SelectCandidate">
      <arg name="index" type="u" direction="in"/>
      <arg name="replacement" type="s" direction="out"/>
      <arg name="original_length" type="u" direction="out"/>
    </method>
    <property name="Candidates" type="as" access="read"/>
    <property name="Active" type="b" access="read"/>
    <property name="Focused" type="b" access="read"/>
    <property name="KeysEnqueued" type="t" access="read"/>
    <property name="KeysDispatched" type="t" access="read"/>
    <property name="BufferLength" type="u" access="read"/>
    <property name="DispatchErrors" type="t" access="read"/>
    <property name="LastDispatchError" type="s" access="read"/>
    <property name="ProcessKeyEvents" type="t" access="read"/>
    <property name="CandidateRefreshes" type="t" access="read"/>
    <property name="LastCandidateCount" type="u" access="read"/>
    <property name="Autocorrections" type="t" access="read"/>
    <property name="FallbackKeyEvents" type="t" access="read"/>
    <property name="FallbackResolutions" type="t" access="read"/>
    <signal name="CandidatesChanged">
      <arg name="candidates" type="as"/>
    </signal>
  </interface>
</node>
"""
TOUCH_EVENTS: list[tuple[str, int, int, int]] = []
KEY_EVENTS: list[tuple[int, int, int, int]] = []
KEY_DISPATCH_ID = 0
KEYS_ENQUEUED = 0
KEYS_DISPATCHED = 0
DISPATCH_ERRORS = 0
LAST_DISPATCH_ERROR = ""
PROCESS_KEY_EVENTS = 0
CANDIDATE_REFRESHES = 0
LAST_CANDIDATE_COUNT = 0
AUTOCORRECTIONS = 0
FALLBACK_KEY_EVENTS = 0
FALLBACK_RESOLUTIONS = 0
ACTIVE_ENGINE: LumaLatinEngine | None = None
TOUCH_CONNECTION: Gio.DBusConnection | None = None
PUBLISHED_CANDIDATES: list[str] = []
ENGINE_FOCUSED = False


def keyval_character(keyval: int) -> str:
    value = IBus.keyval_to_unicode(keyval)
    return value if isinstance(value, str) else chr(value)


def publish_candidates(candidates: list[str]) -> None:
    global PUBLISHED_CANDIDATES
    PUBLISHED_CANDIDATES = candidates[:3]
    if TOUCH_CONNECTION is not None:
        TOUCH_CONNECTION.emit_signal(
            None,
            "/org/project_luma/Keyboard",
            "org.project_luma.Keyboard1",
            "CandidatesChanged",
            GLib.Variant("(as)", (PUBLISHED_CANDIDATES,)),
        )


def dispatch_key_event() -> bool:
    global KEY_DISPATCH_ID, KEYS_DISPATCHED
    global DISPATCH_ERRORS, LAST_DISPATCH_ERROR
    if not KEY_EVENTS:
        KEY_DISPATCH_ID = 0
        return GLib.SOURCE_REMOVE
    keyval, x, y, monotonic_ms = KEY_EVENTS.pop(0)
    KEYS_DISPATCHED += 1
    try:
        if ACTIVE_ENGINE is not None:
            character = keyval_character(keyval)
            if (
                len(character) == 1
                and character.isalpha()
                and 0 <= x < 1000
                and 0 <= y < 400
            ):
                TOUCH_EVENTS.append((character, x, y, monotonic_ms))
                del TOUCH_EVENTS[:-64]
            handled = ACTIVE_ENGINE.do_process_key_event(keyval, 0, 0)
            if not handled:
                ACTIVE_ENGINE.forward_key_event(keyval, 0, 0)
                ACTIVE_ENGINE.forward_key_event(
                    keyval, 0, int(IBus.ModifierType.RELEASE_MASK)
                )
    except Exception as error:  # keep the ordered dispatcher alive for diagnosis
        DISPATCH_ERRORS += 1
        LAST_DISPATCH_ERROR = f"{type(error).__name__}: {error}"
    if KEY_EVENTS:
        return GLib.SOURCE_CONTINUE
    KEY_DISPATCH_ID = 0
    return GLib.SOURCE_REMOVE


class Decoder:
    def __init__(self, executable: str, dictionary: str) -> None:
        self._process = subprocess.Popen(
            [executable, dictionary],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if self._process.stdout.readline().strip() != "READY":
            raise RuntimeError("Luma LatinIME decoder did not become ready")

    def suggest(
        self,
        word: str,
        previous: str = "",
        touches: list[tuple[int, int] | None] | None = None,
    ) -> list[tuple[str, int, int]]:
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        safe_word = word.replace("\t", "").replace("\n", "")
        safe_previous = previous.replace("\t", "").replace("\n", "")
        encoded_touches = ";".join(
            f"{x},{y}" if point is not None else "-1,-1"
            for point in (touches or [])
            for x, y in [point if point is not None else (-1, -1)]
        )
        self._process.stdin.write(
            f"{safe_word}\t{safe_previous}\t{encoded_touches}\n"
        )
        self._process.stdin.flush()
        fields = self._process.stdout.readline().rstrip("\n").split("\t")
        if not fields or fields[0] != "R":
            return []
        candidates: list[tuple[str, int, int]] = []
        for field in fields[1:]:
            try:
                text, kind, score = field.rsplit(":", 2)
                candidates.append((text, int(kind), int(score)))
            except ValueError:
                continue
        return candidates

    def close(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()


class LumaLatinEngine(IBus.Engine):
    def __init__(self) -> None:
        global ACTIVE_ENGINE
        super().__init__()
        decoder = os.environ.get("LUMA_LATINIME_DECODER", "/usr/libexec/luma-latinime-decoder")
        dictionary = os.environ.get("LUMA_LATINIME_DICTIONARY", "/usr/share/luma-keyboard/main_en.dict")
        self._decoder = Decoder(decoder, dictionary)
        self._buffer = ""
        self._previous = ""
        self._candidates: list[tuple[str, int, int]] = []
        self._lookup = IBus.LookupTable.new(8, 0, True, False)
        self._purpose = IBus.InputPurpose.FREE_FORM
        self._hints = IBus.InputHints.NONE
        self._touches: list[tuple[int, int] | None] = []
        ACTIVE_ENGINE = self

    def _bypass(self) -> bool:
        return self._purpose in PASSTHROUGH_PURPOSES or bool(self._hints & SENSITIVE_HINTS)

    def _clear_ui(self) -> None:
        self.hide_preedit_text()
        self.hide_lookup_table()
        publish_candidates([])

    def _resolve_buffer(self, autocorrect: bool) -> tuple[str, str]:
        global AUTOCORRECTIONS
        if not self._buffer:
            return "", ""
        original = self._buffer
        committed = self._buffer
        if autocorrect and self._candidates:
            candidate, kind, _score = self._candidates[0]
            if (
                kind & APPROPRIATE_FOR_AUTOCORRECTION
                and kind & KIND_MASK == KIND_CORRECTION
            ):
                committed = candidate
                if self._buffer[:1].isupper():
                    committed = committed[:1].upper() + committed[1:]
        if committed != self._buffer:
            AUTOCORRECTIONS += 1
        self._previous = committed.lower()
        self._buffer = ""
        self._candidates = []
        self._touches = []
        self._clear_ui()
        return original, committed

    def _commit_buffer(self, autocorrect: bool) -> None:
        _original, committed = self._resolve_buffer(autocorrect)
        if committed:
            self.commit_text(IBus.Text.new_from_string(committed))

    def _refresh(self, publish_ibus: bool = True) -> None:
        global CANDIDATE_REFRESHES, LAST_CANDIDATE_COUNT
        if not self._buffer:
            self._clear_ui()
            return
        if publish_ibus:
            self.update_preedit_text(
                IBus.Text.new_from_string(self._buffer), len(self._buffer), True
            )
        self._candidates = self._decoder.suggest(
            self._buffer.lower(), self._previous, self._touches
        )
        seen: set[str] = set()
        self._candidates = [
            candidate
            for candidate in self._candidates
            if not (candidate[0].lower() in seen or seen.add(candidate[0].lower()))
        ]
        CANDIDATE_REFRESHES += 1
        LAST_CANDIDATE_COUNT = min(len(self._candidates), 3)
        self._lookup.clear()
        for text, _kind, _score in self._candidates[:3]:
            if self._buffer[:1].isupper():
                text = text[:1].upper() + text[1:]
            self._lookup.append_candidate(IBus.Text.new_from_string(text))
        if self._lookup.get_number_of_candidates():
            if publish_ibus:
                self.update_lookup_table(self._lookup, True)
            publish_candidates([
                self._lookup.get_candidate(index).get_text()
                for index in range(self._lookup.get_number_of_candidates())
            ])
        else:
            if publish_ibus:
                self.hide_lookup_table()
            publish_candidates([])

    def compose_fallback(
        self, character: str, x: int, y: int, monotonic_ms: int
    ) -> bool:
        global FALLBACK_KEY_EVENTS
        if self._bypass() or len(character) != 1 or not character.isalpha():
            return False
        if len(self._buffer) >= 47:
            return False
        FALLBACK_KEY_EVENTS += 1
        TOUCH_EVENTS.clear()
        self._buffer += character
        point = (x, y) if 0 <= x < 1000 and 0 <= y < 400 else None
        self._touches.append(point)
        self._refresh(False)
        return True

    def backspace_fallback(self) -> bool:
        global FALLBACK_KEY_EVENTS
        if self._bypass() or not self._buffer:
            return False
        FALLBACK_KEY_EVENTS += 1
        self._buffer = self._buffer[:-1]
        if self._touches:
            self._touches.pop()
        self._refresh(False)
        return True

    def resolve_fallback(self, autocorrect: bool) -> tuple[str, int]:
        global FALLBACK_RESOLUTIONS
        if self._bypass():
            return "", 0
        original, committed = self._resolve_buffer(autocorrect)
        if not original:
            return "", 0
        FALLBACK_RESOLUTIONS += 1
        if committed == original:
            return "", 0
        return committed, len(original)

    def select_candidate_fallback(self, index: int) -> tuple[str, int]:
        global FALLBACK_RESOLUTIONS
        if self._bypass() or index >= len(self._candidates):
            return "", 0
        original = self._buffer
        selected = self._candidates[index][0]
        if original[:1].isupper():
            selected = selected[:1].upper() + selected[1:]
        self._previous = selected.lower()
        self._buffer = ""
        self._candidates = []
        self._touches = []
        self._clear_ui()
        FALLBACK_RESOLUTIONS += 1
        if selected == original:
            return "", 0
        return selected, len(original)

    def do_process_key_event(self, keyval: int, keycode: int, state: int) -> bool:
        global PROCESS_KEY_EVENTS
        PROCESS_KEY_EVENTS += 1
        del keycode
        if state & IBus.ModifierType.RELEASE_MASK or self._bypass():
            return False
        if state & (
            IBus.ModifierType.CONTROL_MASK
            | IBus.ModifierType.MOD1_MASK
            | IBus.ModifierType.SUPER_MASK
        ):
            self._commit_buffer(False)
            return False

        if keyval == IBus.KEY_BackSpace:
            if not self._buffer:
                return False
            self._buffer = self._buffer[:-1]
            if self._touches:
                self._touches.pop()
            self._refresh()
            return True

        if keyval == IBus.KEY_space:
            self._commit_buffer(True)
            self.commit_text(IBus.Text.new_from_string(" "))
            return True

        if keyval in (IBus.KEY_Return, IBus.KEY_KP_Enter, IBus.KEY_Tab):
            self._commit_buffer(True)
            return False

        character = keyval_character(keyval)
        if character and character.isalpha() and len(self._buffer) < 47:
            self._buffer += character
            self._touches.append(self._take_touch(character))
            self._refresh()
            return True

        if character and character in ".,!?;:":
            self._commit_buffer(True)
            self.commit_text(IBus.Text.new_from_string(character))
            return True

        self._commit_buffer(False)
        return False

    def _take_touch(self, character: str) -> tuple[int, int] | None:
        now_ms = GLib.get_monotonic_time() // 1000
        match = None
        while TOUCH_EVENTS:
            event = TOUCH_EVENTS.pop()
            if now_ms - event[3] > 180:
                continue
            if event[0].lower() == character.lower():
                match = (event[1], event[2])
                break
        TOUCH_EVENTS.clear()
        return match

    def do_candidate_clicked(self, index: int, button: int, state: int) -> None:
        del button, state
        if index >= len(self._candidates):
            return
        selected = self._candidates[index][0]
        if self._buffer[:1].isupper():
            selected = selected[:1].upper() + selected[1:]
        self.commit_text(IBus.Text.new_from_string(selected))
        self._previous = selected.lower()
        self._buffer = ""
        self._candidates = []
        self._touches = []
        self._clear_ui()

    def do_set_content_type(self, purpose: int, hints: int) -> None:
        self._commit_buffer(False)
        self._purpose = IBus.InputPurpose(purpose)
        self._hints = IBus.InputHints(hints)

    def do_reset(self) -> None:
        self._buffer = ""
        self._candidates = []
        self._touches = []
        self._clear_ui()

    def do_focus_out(self) -> None:
        global ENGINE_FOCUSED
        ENGINE_FOCUSED = False
        self._commit_buffer(False)

    def do_focus_in(self) -> None:
        global ENGINE_FOCUSED
        ENGINE_FOCUSED = True

    def do_destroy(self) -> None:
        global ACTIVE_ENGINE
        if ACTIVE_ENGINE is self:
            ACTIVE_ENGINE = None
            publish_candidates([])
        self._decoder.close()
        super().do_destroy()


def main() -> int:
    IBus.init()
    bus = IBus.Bus.new()
    if not bus.is_connected():
        print("Luma keyboard: IBus is not available", file=sys.stderr)
        return 1
    factory = IBus.Factory.new(bus.get_connection())
    factory.add_engine(ENGINE_NAME, LumaLatinEngine)
    component = IBus.Component.new(
        "org.project_luma.IBus.Latin",
        "Luma native mobile input",
        "1.0",
        "Apache-2.0",
        "Project Luma",
        "https://project-luma.local",
        sys.argv[0],
        "project-luma",
    )
    component.add_engine(
        IBus.EngineDesc.new(
            ENGINE_NAME,
            "Luma Keyboard",
            "Predictive Luma mobile keyboard",
            "en",
            "Apache-2.0",
            "Project Luma",
            "input-keyboard-symbolic",
            "us",
        )
    )
    bus.request_name("org.project_luma.IBus.Latin", 0)
    if not bus.register_component(component):
        print("Luma keyboard: failed to register IBus component", file=sys.stderr)
        return 1
    loop = GLib.MainLoop()
    touch_registration = 0
    touch_connection: Gio.DBusConnection | None = None

    def on_touch_method(
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        del connection, sender, object_path, interface_name
        global KEY_DISPATCH_ID, KEYS_ENQUEUED
        if method_name == "Key":
            keyval, x, y, monotonic_ms = parameters.unpack()
            KEY_EVENTS.append((keyval, x, y, monotonic_ms))
            KEYS_ENQUEUED += 1
            if not KEY_DISPATCH_ID:
                KEY_DISPATCH_ID = GLib.idle_add(dispatch_key_event)
            invocation.return_value(None)
            return
        if method_name == "AcceptCandidate":
            index = parameters.unpack()[0]
            if ACTIVE_ENGINE is not None:
                ACTIVE_ENGINE.do_candidate_clicked(index, 1, 0)
            invocation.return_value(None)
            return
        if method_name == "Compose":
            character, x, y, monotonic_ms = parameters.unpack()
            accepted = ACTIVE_ENGINE is not None and ACTIVE_ENGINE.compose_fallback(
                character, x, y, monotonic_ms
            )
            invocation.return_value(GLib.Variant("(b)", (accepted,)))
            return
        if method_name == "Backspace":
            tracked = ACTIVE_ENGINE is not None and ACTIVE_ENGINE.backspace_fallback()
            invocation.return_value(GLib.Variant("(b)", (tracked,)))
            return
        if method_name == "Resolve":
            autocorrect = parameters.unpack()[0]
            result = ("", 0) if ACTIVE_ENGINE is None else \
                ACTIVE_ENGINE.resolve_fallback(autocorrect)
            invocation.return_value(GLib.Variant("(su)", result))
            return
        if method_name == "SelectCandidate":
            index = parameters.unpack()[0]
            result = ("", 0) if ACTIVE_ENGINE is None else \
                ACTIVE_ENGINE.select_candidate_fallback(index)
            invocation.return_value(GLib.Variant("(su)", result))
            return
        if method_name != "Touch":
            invocation.return_dbus_error(
                "org.project_luma.Error.UnknownMethod", "Unknown keyboard request"
            )
            return
        character, x, y, monotonic_ms = parameters.unpack()
        if len(character) == 1 and 0 <= x < 1000 and 0 <= y < 400:
            TOUCH_EVENTS.append((character, x, y, monotonic_ms))
            del TOUCH_EVENTS[:-64]
        invocation.return_value(None)

    def on_get_property(
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        property_name: str,
    ) -> GLib.Variant | None:
        del connection, sender, object_path, interface_name
        if property_name == "Candidates":
            return GLib.Variant("as", PUBLISHED_CANDIDATES)
        if property_name == "Active":
            return GLib.Variant("b", ACTIVE_ENGINE is not None)
        if property_name == "Focused":
            return GLib.Variant("b", ENGINE_FOCUSED)
        if property_name == "KeysEnqueued":
            return GLib.Variant("t", KEYS_ENQUEUED)
        if property_name == "KeysDispatched":
            return GLib.Variant("t", KEYS_DISPATCHED)
        if property_name == "BufferLength":
            return GLib.Variant(
                "u", len(ACTIVE_ENGINE._buffer) if ACTIVE_ENGINE is not None else 0
            )
        if property_name == "DispatchErrors":
            return GLib.Variant("t", DISPATCH_ERRORS)
        if property_name == "LastDispatchError":
            return GLib.Variant("s", LAST_DISPATCH_ERROR)
        if property_name == "ProcessKeyEvents":
            return GLib.Variant("t", PROCESS_KEY_EVENTS)
        if property_name == "CandidateRefreshes":
            return GLib.Variant("t", CANDIDATE_REFRESHES)
        if property_name == "LastCandidateCount":
            return GLib.Variant("u", LAST_CANDIDATE_COUNT)
        if property_name == "Autocorrections":
            return GLib.Variant("t", AUTOCORRECTIONS)
        if property_name == "FallbackKeyEvents":
            return GLib.Variant("t", FALLBACK_KEY_EVENTS)
        if property_name == "FallbackResolutions":
            return GLib.Variant("t", FALLBACK_RESOLUTIONS)
        return None

    def on_touch_bus(connection: Gio.DBusConnection, name: str) -> None:
        global TOUCH_CONNECTION
        nonlocal touch_connection, touch_registration
        del name
        touch_connection = connection
        TOUCH_CONNECTION = connection
        node = Gio.DBusNodeInfo.new_for_xml(TOUCH_DBUS_XML)
        touch_registration = connection.register_object(
            "/org/project_luma/Keyboard",
            node.interfaces[0],
            on_touch_method,
            on_get_property,
            None,
        )

    touch_owner = Gio.bus_own_name(
        Gio.BusType.SESSION,
        "org.project_luma.Keyboard",
        Gio.BusNameOwnerFlags.NONE,
        on_touch_bus,
        None,
        lambda *_args: loop.quit(),
    )
    loop.run()
    if touch_registration and touch_connection is not None:
        touch_connection.unregister_object(touch_registration)
    TOUCH_CONNECTION = None
    Gio.bus_unown_name(touch_owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
