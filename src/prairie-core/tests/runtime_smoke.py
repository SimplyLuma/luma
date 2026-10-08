#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
from types import SimpleNamespace

from gi.repository import GLib

from prairie_apps.camera import CameraWindow
from prairie_apps.calendar import CalendarWindow
from prairie_apps.contacts import ContactsWindow
from prairie_apps.messages import MessagesApplication, MessagesWindow
from prairie_apps.notes import NotesWindow
from prairie_apps.phone import PhoneWindow
from prairie_apps.photos import PhotosWindow
from prairie_apps.tasks import TasksWindow
from prairie_apps.voice_memos import VoiceMemosWindow
from prairie_apps.weather import WeatherWindow
from prairie_apps.clock import ClockWindow


def settle() -> None:
    context = GLib.MainContext.default()
    for _ in range(80):
        while context.pending():
            context.iteration(False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expected-collapsed",
        choices=("true", "false"),
        required=True,
    )
    parser.add_argument(
        "--expected-decorated",
        choices=("true", "false"),
        required=True,
    )
    args = parser.parse_args()

    application = MessagesApplication()
    assert application.register(None)
    window = MessagesWindow(application)
    window.present()
    # Breakpoints re-evaluate on size-allocate, a main-loop turn after the
    # window reaches its real width; a single settle can read the small first
    # layout, where the compact breakpoint still applies. Wait for the width,
    # then let the breakpoint settle before reading it.
    target = min(window.get_default_size()[0], 900)
    for _ in range(300):
        settle()
        if window.get_width() >= target or window.is_maximized():
            break
    for _ in range(20):
        settle()
    expected = args.expected_collapsed == "true"
    actual = bool(window.split.props.collapsed)
    if actual is not expected:
        raise AssertionError(
            f"collapsed={actual}, expected {expected} (width={window.get_width()})"
        )
    decorated = args.expected_decorated == "true"
    if window.get_decorated() is not decorated:
        raise AssertionError(
            f"decorated={window.get_decorated()}, expected {decorated}"
        )
    window.composer_view.emit("preedit-changed", "hello")
    settle()
    if window.composer_placeholder.get_visible():
        raise AssertionError("composer placeholder remained visible during preedit")
    if window.composer_action.get_visible_child_name() != "send":
        raise AssertionError("composer did not expose Send during preedit")
    window.composer_view.emit("preedit-changed", "")
    settle()
    if not window.composer_placeholder.get_visible():
        raise AssertionError("composer placeholder did not return after preedit")
    # The compact layout offers to record a voice message when there is
    # nothing to send; the wide layout has no recorder, so its action stays
    # Send. What is under test is that the resting action returns after
    # preedit, in whichever layout the window is in.
    resting_action = "voice" if getattr(window, "_compact", False) else "send"
    if window.composer_action.get_visible_child_name() != resting_action:
        raise AssertionError(
            f"composer did not restore the {resting_action} action after preedit"
        )
    if window.thread_identity_cluster.get_spacing() != 9:
        raise AssertionError("thread identity must retain the measured 9px avatar gap")
    phone_window = PhoneWindow(application)
    placed_calls: list[str] = []
    phone_window._start_call = lambda kind: placed_calls.append(phone_window.number) if kind == "voice" else None
    phone_window._capability(SimpleNamespace(available=True, reason="", no_modem=False))
    phone_window.start_dial_address("tel:+15550100")
    if placed_calls != ["+15550100"]:
        raise AssertionError("Phone did not own the authorized one-tap call")
    stage_windows = (
        CameraWindow(application), phone_window,
        ContactsWindow(application), CalendarWindow(application), TasksWindow(application),
        NotesWindow(application), VoiceMemosWindow(application),
        PhotosWindow(application), WeatherWindow(application), ClockWindow(application),
    )
    for stage_window in stage_windows:
        stage_window.present()
    settle()
    if any(stage_window.get_decorated() is not decorated for stage_window in stage_windows):
        raise AssertionError("core app presentation mode diverged")
    for stage_window in stage_windows:
        stage_window.close()
    window.close()
    print(
        "prairie-runtime-smoke: "
        f"collapsed={str(actual).lower()} decorated={str(decorated).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
