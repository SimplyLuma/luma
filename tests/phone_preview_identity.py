#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify Phone's real GApplication identity on an isolated session bus.

Run on the host after process-cap recovery, before any real-data preview launch:
    python3 tests/phone_preview_identity.py
Set LUMA_PHONE_IDENTITY_PYTHON to the installed preview/python directory to
check that copy too. No windows are created. XDG and sentinel state are temporary.
Missing GTK/kit dependencies or an identity mismatch are failures, never skips.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = "org.projectluma.Phone"
PREVIEW = PRODUCTION + ".LumaUIPreview"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def require_private_bus():
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    require("PHONE_IDENTITY_ORIGINAL_BUS" in os.environ and bool(address) and
            address != os.environ["PHONE_IDENTITY_ORIGINAL_BUS"],
            "Use the probe driver: internal modes require its private bus")


def sentinel(state):
    require_private_bus()
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    class InstalledSentinel(Gio.Application):
        def __init__(self):
            super().__init__(application_id=PRODUCTION,
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.activations = 0

        def do_startup(self):
            Gio.Application.do_startup(self)
            self.hold()
            GLib.timeout_add_seconds(20, self.expire)

        def expire(self):
            self.quit()
            return GLib.SOURCE_REMOVE

        def do_activate(self):
            self.activations += 1
            temporary = state.with_suffix(".tmp")
            temporary.write_text(str(self.activations))
            temporary.replace(state)

        def do_command_line(self, command_line):
            self.activate()
            return 0

    return InstalledSentinel().run(["phone-production-sentinel"])


def private_check(state):
    require_private_bus()
    installed_python = os.environ.get("LUMA_PHONE_IDENTITY_PYTHON")
    paths = ((Path(installed_python),) if installed_python else
             (ROOT / "src/prairie-core", ROOT / "src/luma-platform/appkit"))
    for path in reversed(paths):
        require(path.is_dir(), f"Phone runtime path is missing: {path}")
        sys.path.insert(0, str(path))
    from gi.repository import Gio, GLib
    from prairie_apps import phone
    expected_module = paths[0] / "prairie_apps/phone.py"
    require(expected_module.is_file() and
            Path(phone.__file__).resolve() == expected_module.resolve(),
            "Identity probe imported a different Phone copy")
    check_mute_routing(phone, GLib)
    check_audio_routing(phone)

    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def owner(name):
        return connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "GetNameOwner", GLib.Variant("(s)", (name,)),
            GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 5000, None
        ).unpack()[0]

    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                "--sentinel", str(state)])
    try:
        deadline = time.monotonic() + 10
        while not state.exists():
            require(process.poll() is None, "Production sentinel exited before registering")
            require(time.monotonic() < deadline, "Production sentinel did not register")
            time.sleep(.02)
        initial = int(state.read_text())
        production_owner = owner(PRODUCTION)
        require(phone.APPLICATION_ID == PRODUCTION and phone.APP_ID == PRODUCTION,
                "Production Phone ID changed")
        normal = phone.PhoneApplication()
        require(normal.get_application_id() == PRODUCTION,
                "Unmodified Phone no longer uses the production ID")
        require(normal.register(None), "Production control did not register")
        require(normal.get_is_remote(), "Production control failed to find the sentinel")
        require(normal.run(["phone-production-control"]) == 0,
                "Production control command failed")
        deadline = time.monotonic() + 5
        while int(state.read_text()) != initial + 1:
            require(time.monotonic() < deadline, "Production activation control was not observed")
            time.sleep(.02)
        del normal
        gc.collect()

        # This is the override used verbatim by sync-lumaui-preview.sh.
        phone.APP_ID = getattr(phone, "APP_ID", PRODUCTION) + ".LumaUIPreview"
        require(phone.APPLICATION_ID == PRODUCTION, "Preview changed the production constant")
        from unittest.mock import patch
        from prairie_apps.phone_system import ActiveCallIndicator

        class NotificationWire:
            def __init__(self):
                self.sent = []

            def connect(self, *args):
                pass

            def call_sync(self, method, parameters, *args):
                self.sent.append((method, parameters.unpack()))
                return GLib.Variant("(u)", (7,))

        notification_wire = NotificationWire()
        with patch.object(Gio.DBusProxy, "new_for_bus_sync", return_value=notification_wire):
            for identity in (PRODUCTION, PREVIEW):
                callbacks = dict(on_return=lambda: None, on_mute=lambda _: None,
                                 on_end=lambda: None)
                if identity == PREVIEW:
                    callbacks["application_id"] = phone.APP_ID
                indicator = ActiveCallIndicator(**callbacks)
                indicator.publish("Private identity probe", 0)
                method, payload = notification_wire.sent[-1]
                require(method == "Notify" and payload[6]["desktop-entry"] == identity,
                        "Active-call notification has the wrong application identity")
                require(payload[2] == PRODUCTION, "Production notification icon changed")
                indicator.withdraw()
        app = phone.PhoneApplication()
        require(app.get_application_id() == PREVIEW,
                "Launcher APP_ID override did not reach the actual GApplication")
        windows_attempted = []

        def forbidden_window(*args, **kwargs):
            windows_attempted.append(True)
            raise AssertionError("Identity probe must not create a Phone window")

        phone.PhoneWindow = forbidden_window
        activations = []

        def activated(application):
            application.stop_emission_by_name("activate")
            activations.append(application.get_application_id())
            application.quit()

        app.connect("activate", activated)
        require(app.register(None), "Preview registration failed")
        require(not app.get_is_remote(), "Preview would activate the production instance")
        preview_owner = owner(PREVIEW)
        require(preview_owner != production_owner, "Production and preview have the same bus owner")
        require(app.run(["phone-preview-control"]) == 0, "Preview command failed")
        require(activations == [PREVIEW], "Preview command did not activate the preview instance")
        require(int(state.read_text()) == initial + 1, "Preview activated the production sentinel")
        require(owner(PRODUCTION) == production_owner, "Preview replaced the production bus owner")
        require(not windows_attempted and not app.get_windows(), "Preview left a window open")
        module_path = Path(phone.__file__).resolve()
        result = dict(module_path=str(module_path),
                      module_sha256=hashlib.sha256(module_path.read_bytes()).hexdigest(),
                      production_id=PRODUCTION, preview_id=PREVIEW,
                      production_owner=production_owner, preview_owner=preview_owner,
                      production_control_activations=1, preview_activations=1,
                      production_activations_from_preview=0, windows_created=0,
                      windows_remaining=0, private_bus=True,
                      notification_identity_preserved=True, mute_routing_verified=True,
                      audio_routing_verified=True)
        del app
        gc.collect()
    finally:
        process.terminate()
        process.wait(timeout=5)
    require(process.poll() is not None, "Production sentinel remained running")
    result["sentinel_closed"] = True
    print(json.dumps(result, sort_keys=True))
    return 0


def check_mute_routing(phone, GLib):
    """Exercise the real window methods without creating widgets or touching audio."""
    from types import MethodType, SimpleNamespace
    from unittest.mock import patch

    requests, updates, scheduled = [], [], []
    call = SimpleNamespace(muted=False)
    window = SimpleNamespace(
        closed=False, fixture=False, call=call,
        voice=SimpleNamespace(mute=lambda value, *, confirmed: requests.append((value, confirmed))),
        _publish_shell=lambda: updates.append("shell"),
        _call_view=lambda: updates.append("view"))
    window._set_muted = MethodType(phone.PhoneWindow._set_muted, window)
    window._set_muted(True)
    require(len(requests) == 1 and requests[0][0] is True and call.muted is False and not updates,
            "Mute state changed before the native service confirmed it")
    requests.pop()[1](True)
    require(call.muted is True and updates == ["shell", "view"],
            "Confirmed mute did not update the call and shell")
    with patch.object(GLib, "idle_add", side_effect=lambda callback: scheduled.append(callback)):
        phone.PhoneWindow._notification_mute(window, False)
        require(len(scheduled) == 1, "Notification mute did not queue its requested state")
        scheduled.pop()()
        require(len(requests) == 1 and requests[0][0] is False,
                "Notification mute did not preserve the requested Boolean")
        confirmed = requests.pop()[1]
        window.call = SimpleNamespace(muted=True)
        confirmed(False)
        require(window.call.muted is True, "Stale mute completion changed a different call")
        phone.PhoneWindow._notification_mute(window, False)
        window.call = SimpleNamespace(muted=True)
        require(scheduled.pop()() is False and not requests,
                "Queued notification mute acted on a different call")
    window.closed = True
    require(window._set_muted(False) is False and not requests,
            "Closed Phone sent a native mute request")


def check_audio_routing(phone):
    """Old menu actions and acknowledgements must not reach a different call."""
    from types import MethodType, SimpleNamespace
    from unittest.mock import patch

    class Menu:
        def __init__(self, rows, **kwargs):
            self.rows, self.closes = rows, 0

        def popup(self, anchor):
            return self

        def close(self):
            self.closes += 1

    requests = []
    call = SimpleNamespace(output="earpiece")
    window = SimpleNamespace(
        closed=False, fixture=False, call=call, audio_menu=None, call_center=object(),
        voice=SimpleNamespace(provider=None,
                              audio=lambda route, *, confirmed: requests.append((route, confirmed))),
        _find=lambda *args: object())
    window._audio = MethodType(phone.PhoneWindow._audio, window)
    with patch.object(phone, "FloatingMenu", Menu):
        phone.PhoneWindow._audio_menu(window)
        old_menu = window.audio_menu
        window.call = SimpleNamespace(output="earpiece")
        old_menu.rows[0].on_activate()
        require(not requests, "Old Audio menu sent a request for a different call")
        phone.PhoneWindow._audio_menu(window)
        require(old_menu.closes == 1, "Replacing the Audio menu left the old menu open")
        menu, call = window.audio_menu, window.call
        menu.rows[0].on_activate()
        require(len(requests) == 1 and requests[0][0] == "speaker" and
                call.output == "earpiece" and menu.closes == 1 and window.audio_menu is None,
                "Audio selection did not close its menu and wait for confirmation")
        confirmed = requests.pop()[1]
        window.call = SimpleNamespace(output="earpiece")
        confirmed("speaker")
        require(window.call.output == "earpiece", "Stale Audio completion changed a different call")
        phone.PhoneWindow._audio_menu(window)
        menu = window.audio_menu
        window.call_timer_source = window.video_idle_source = 0
        window.shell_surface = window.active_indicator = None
        window.sidebar = SimpleNamespace(set_sensitive=lambda _: None, set_opacity=lambda _: None)
        window.call_center = SimpleNamespace(hide_bar=lambda: None)
        window._all = lambda: None
        window._toast = lambda _: None
        phone.PhoneWindow._end_view(window, "Private probe ended")
        require(menu.closes == 1 and window.audio_menu is None and window.call is None,
                "Ending a call left its Audio menu open")
        menu.rows[0].on_activate()
        window._audio("speaker")
        phone.PhoneWindow._audio_menu(window)
        require(not requests and window.audio_menu is None, "Ended call sent an Audio request")
        window.call = SimpleNamespace(output="earpiece")
        window._audio("speaker")
        confirmed = requests.pop()[1]
        window.closed = True
        confirmed("speaker")
        require(window.call.output == "earpiece", "Closed Phone accepted an Audio completion")
        window._audio("speaker")
        require(not requests, "Closed Phone sent an Audio request")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--sentinel":
        return sentinel(Path(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--private":
        return private_check(Path(sys.argv[2]))
    with tempfile.TemporaryDirectory(prefix="phone-preview-identity-") as folder:
        environment = os.environ.copy()
        environment["PHONE_IDENTITY_ORIGINAL_BUS"] = environment.get("DBUS_SESSION_BUS_ADDRESS", "")
        environment.pop("LUMA_PHONE_APPLICATION_ID", None)
        environment["LUMA_PHONE_FIXTURE"] = str(ROOT / "tests/fixtures/phone-v70.json")
        environment["LUMA_PHONE_STYLE_PATH"] = str(ROOT / "src/prairie-core/style/phone.css")
        environment["GSETTINGS_BACKEND"] = "memory"
        environment["GTK_A11Y"] = "none"
        environment["NO_AT_BRIDGE"] = "1"
        # GTK still needs the host display for startup, even without windows.
        # A relative Wayland socket must keep its original absolute location
        # when the probe moves XDG_RUNTIME_DIR into its private temporary tree.
        wayland = environment.get("WAYLAND_DISPLAY", "")
        original_runtime = environment.get("XDG_RUNTIME_DIR", "")
        if wayland and original_runtime and not os.path.isabs(wayland):
            environment["WAYLAND_DISPLAY"] = str(Path(original_runtime) / wayland)
        for key, directory in (("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"),
                               ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state"),
                               ("XDG_RUNTIME_DIR", "runtime")):
            path = Path(folder) / directory
            path.mkdir(mode=0o700)
            environment[key] = str(path)
        # dbus-run-session owns and closes this bus; nothing reaches Nick's session bus.
        completed = subprocess.run(
            ["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()),
             "--private", str(Path(folder) / "sentinel-activations")],
            env=environment, timeout=30, check=False)
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
