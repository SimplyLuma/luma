# SPDX-License-Identifier: Apache-2.0
"""The setup session's side of the system: networks, where we are, Finish and signing in.

Everything here is asynchronous on the GLib main loop, so the sheet keeps
drawing. Without the services (a capture harness, a test) LUMA_SETUP_FIXTURE
stands them in with fixed data.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

from gi.repository import Gio, GLib  # noqa: E402

__all__ = ("Network", "WifiNetwork", "Applier", "sign_in", "detect_timezone", "set_live_layout",
           "set_accessibility", "accessibility", "installer_answers", "current_timezone", "fixture")

log = logging.getLogger("luma-setup")

APPLY = os.environ.get("LUMA_SETUP_APPLY", "/usr/libexec/luma-setup-apply")
INSTALLER_ANSWERS = Path(os.environ.get("LUMA_SETUP_INSTALLER", "/var/lib/luma-setup/installer.json"))
#: The desktop id GeoClue knows the setup by (data/geoclue: allowed as a system component).
GEOCLUE_ID = "org.projectluma.Setup"


def fixture() -> dict | None:
    """LUMA_SETUP_FIXTURE: a JSON file (or "1") standing in for the system."""
    value = os.environ.get("LUMA_SETUP_FIXTURE", "")
    if not value:
        return None
    if value == "1" or not os.path.isfile(value):
        return {}
    with open(value, encoding="utf-8") as handle:
        return json.load(handle)


# -- the installer's answers ----------------------------------------------------
def installer_answers() -> dict:
    """What the installer already asked (it wrote them into the new system): pages to leave out.

    {"answered": ["hello"], "locale": "es_ES.UTF-8", "layout": "es"}
    """
    try:
        with open(INSTALLER_ANSWERS, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def current_timezone() -> str:
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return "UTC"
    marker = "zoneinfo/"
    return target.split(marker, 1)[1] if marker in target else "UTC"


# -- networks -------------------------------------------------------------------
@dataclass
class WifiNetwork:
    ssid: str
    bars: int          # 1-3
    secured: bool
    connected: bool = False


class Network:
    """NetworkManager, as the Get online page needs it."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._fixture = fixture()
        self.client = None
        self._device = None
        self._scan_source = 0
        if self._fixture is not None:
            return
        try:
            gi.require_version("NM", "1.0")
            from gi.repository import NM
        except (ImportError, ValueError) as error:
            log.warning("no NetworkManager: %s", error)
            return
        self.NM = NM
        try:
            self.client = NM.Client.new(None)
        except GLib.Error as error:
            log.warning("NetworkManager is not running: %s", error.message)
            return
        self.client.connect("notify::primary-connection", lambda *_a: self._on_change())
        self.client.connect("device-added", lambda *_a: self._watch())
        self._watch()

    def _wifi_device(self):
        if self.client is None:
            return None
        for device in self.client.get_devices():
            if device.get_device_type() == self.NM.DeviceType.WIFI:
                return device
        return None

    def _watch(self) -> None:
        device = self._wifi_device()
        if device is not None and device is not self._device:
            self._device = device
            device.connect("access-point-added", lambda *_a: self._on_change())
            device.connect("access-point-removed", lambda *_a: self._on_change())
            device.connect("state-changed", lambda *_a: self._on_change())

    # -- what there is ----------------------------------------------------------
    def wired(self) -> bool:
        if self._fixture is not None:
            return bool(self._fixture.get("wired"))
        if self.client is None:
            return False
        for device in self.client.get_devices():
            if (device.get_device_type() == self.NM.DeviceType.ETHERNET
                    and device.get_state() == self.NM.DeviceState.ACTIVATED):
                return True
        return False

    def has_wifi(self) -> bool:
        if self._fixture is not None:
            return self._fixture.get("wifi", True)
        return self._wifi_device() is not None

    def connected_name(self) -> str:
        """The network this device is on: "" offline."""
        if self._fixture is not None:
            return self._fixture.get("connected", "")
        if self.client is None:
            return ""
        primary = self.client.get_primary_connection()
        if primary is None or primary.get_state() != self.NM.ActiveConnectionState.ACTIVATED:
            return ""
        return primary.get_id() or "network"

    def networks(self) -> list[WifiNetwork]:
        if self._fixture is not None:
            return [WifiNetwork(n[0], n[1], bool(n[2]), n[0] == self._fixture.get("connected"))
                    for n in self._fixture.get("networks", [])]
        device = self._wifi_device()
        if device is None:
            return []
        active = device.get_active_access_point()
        active_ssid = _ssid(active) if active is not None else ""
        best: dict[str, WifiNetwork] = {}
        for point in device.get_access_points():
            ssid = _ssid(point)
            if not ssid:
                continue
            strength = point.get_strength()
            bars = 3 if strength > 66 else 2 if strength > 33 else 1
            secured = bool(point.get_wpa_flags() or point.get_rsn_flags()
                           or point.get_flags() & getattr(self.NM, "80211ApFlags").PRIVACY)
            seen = best.get(ssid)
            if seen is None or bars > seen.bars:
                best[ssid] = WifiNetwork(ssid, bars, secured, ssid == active_ssid)
        return sorted(best.values(), key=lambda n: (not n.connected, -n.bars, n.ssid.casefold()))

    def scan(self) -> None:
        device = self._wifi_device()
        if device is not None:
            device.request_scan_async(None, lambda d, r: _ignore(lambda: d.request_scan_finish(r)))

    def start_scanning(self) -> None:
        """While Get online shows: a fresh list every 15 seconds."""
        self.scan()
        if not self._scan_source:
            self._scan_source = GLib.timeout_add_seconds(15, lambda: (self.scan(), True)[1])

    def stop_scanning(self) -> None:
        if self._scan_source:
            GLib.source_remove(self._scan_source)
            self._scan_source = 0

    # -- joining ----------------------------------------------------------------
    def join(self, ssid: str, password: str, done: Callable[[bool, str], None], *, hidden: bool = False) -> None:
        """Join a network; `done(ok, why)` once it is up or has failed."""
        if self._fixture is not None:
            ok = password != "wrong"

            def finish() -> bool:
                if ok:
                    self._fixture["connected"] = ssid
                done(ok, "" if ok else "That password didn’t work.")
                return False
            GLib.timeout_add(900, finish)
            return
        NM, device = self.NM, self._wifi_device()
        if device is None:
            done(False, "There is no Wi-Fi on this device.")
            return
        connection = NM.SimpleConnection.new()
        base = NM.SettingConnection.new()
        base.set_property(NM.SETTING_CONNECTION_ID, ssid)
        base.set_property(NM.SETTING_CONNECTION_TYPE, "802-11-wireless")
        base.set_property(NM.SETTING_CONNECTION_AUTOCONNECT, True)
        connection.add_setting(base)
        wireless = NM.SettingWireless.new()
        wireless.set_property(NM.SETTING_WIRELESS_SSID, GLib.Bytes.new(ssid.encode()))
        wireless.set_property(NM.SETTING_WIRELESS_HIDDEN, hidden)
        connection.add_setting(wireless)
        if password:
            security = NM.SettingWirelessSecurity.new()
            security.set_property(NM.SETTING_WIRELESS_SECURITY_KEY_MGMT, "wpa-psk")
            security.set_property(NM.SETTING_WIRELESS_SECURITY_PSK, password)
            connection.add_setting(security)
        point = None if hidden else self._point(device, ssid)
        path = point.get_path() if point is not None else None

        def added(client, result) -> None:
            try:
                active = client.add_and_activate_connection_finish(result)
            except GLib.Error as error:
                done(False, _join_problem(error.message))
                return
            self._follow(active, done)
        self.client.add_and_activate_connection_async(connection, device, path, None, added)

    @staticmethod
    def _point(device, ssid: str):
        best = None
        for point in device.get_access_points():
            if _ssid(point) == ssid and (best is None or point.get_strength() > best.get_strength()):
                best = point
        return best

    def _follow(self, active, done) -> None:
        NM = self.NM
        state = {"handler": 0, "timeout": 0}

        def finish(ok: bool, why: str) -> None:
            if state["handler"]:
                active.disconnect(state["handler"])
            if state["timeout"]:
                GLib.source_remove(state["timeout"])
            if not ok:
                remote = active.get_connection()
                if remote is not None:
                    remote.delete_async(None, None, None)
            done(ok, why)

        def changed(conn, new_state, reason) -> None:
            if new_state == NM.ActiveConnectionState.ACTIVATED:
                finish(True, "")
            elif new_state in (NM.ActiveConnectionState.DEACTIVATED, NM.ActiveConnectionState.DEACTIVATING):
                secrets = reason == NM.ActiveConnectionStateReason.NO_SECRETS
                finish(False, "That password didn’t work." if secrets else "Luma couldn’t join that network.")

        def timeout() -> bool:
            state["timeout"] = 0
            finish(False, "Luma couldn’t join that network.")
            return False
        state["handler"] = active.connect("state-changed", changed)
        state["timeout"] = GLib.timeout_add_seconds(40, timeout)


def _ssid(point) -> str:
    raw = point.get_ssid() if point is not None else None
    if raw is None:
        return ""
    try:
        return bytes(raw.get_data()).decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _join_problem(message: str) -> str:
    return "That password didn’t work." if "secret" in message.lower() else "Luma couldn’t join that network."


def _ignore(call) -> None:
    try:
        call()
    except GLib.Error:
        pass


# -- where we are ---------------------------------------------------------------
def detect_timezone(done: Callable[[str | None], None], seconds: int = 12) -> None:
    """Once online: the time zone where the device is (GeoClue, then GWeather's nearest city)."""
    data = fixture()
    if data is not None:
        GLib.idle_add(lambda: (done(data.get("timezone")), False)[1])
        return
    try:
        gi.require_version("Geoclue", "2.0")
        gi.require_version("GWeather", "4.0")
        from gi.repository import Geoclue, GWeather
    except (ImportError, ValueError):
        done(None)
        return
    answered = {"done": False}

    def answer(zone: str | None) -> None:
        if not answered["done"]:
            answered["done"] = True
            done(zone)

    def located(_source, result) -> None:
        try:
            simple = Geoclue.Simple.new_finish(result)
            where = simple.get_location()
            city = GWeather.Location.get_world().find_nearest_city(where.get_property("latitude"),
                                                                  where.get_property("longitude"))
            zone = city.get_timezone() if city is not None else None
            answer(zone.get_identifier() if zone is not None else None)
        except (GLib.Error, AttributeError, TypeError) as error:
            log.info("no location: %s", error)
            answer(None)
    Geoclue.Simple.new(GEOCLUE_ID, Geoclue.AccuracyLevel.CITY, None, located)
    GLib.timeout_add_seconds(seconds, lambda: (answer(None), False)[1])


# -- Finish ---------------------------------------------------------------------
class Applier:
    """Runs the root helper through pkexec and reports each step as it lands."""

    def __init__(self, on_step: Callable[[str, bool], None], on_done: Callable[[bool, str], None]) -> None:
        self._on_step = on_step
        self._on_done = on_done
        self._error = ""

    def run(self, payload: dict) -> None:
        if fixture() is not None:
            steps = ["account", "language", "timezone", "name", "keyring"]

            def tick() -> bool:
                if steps:
                    self._on_step(steps.pop(0), True)
                    return True
                self._on_done(True, "")
                return False
            GLib.timeout_add(450, tick)
            return
        flags = Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
        try:
            process = Gio.Subprocess.new(["pkexec", APPLY], flags)
        except GLib.Error as error:
            self._on_done(False, error.message)
            return
        process.get_stdin_pipe().write_bytes(GLib.Bytes.new((json.dumps(payload) + "\n").encode()), None)
        process.get_stdin_pipe().close(None)
        self._process = process
        self._lines = Gio.DataInputStream.new(process.get_stdout_pipe())
        self._lines.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line)

    def _line(self, stream, result) -> None:
        line, _length = stream.read_line_finish_utf8(result)
        if line is None:
            self._process.wait_async(None, self._exited)
            return
        words = line.split(" ", 3)
        if words[0] == "step" and len(words) >= 3:
            self._on_step(words[1], words[2] == "ok")
        elif words[0] == "error":
            self._error = line[6:]
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line)

    def _exited(self, process, result) -> None:
        process.wait_finish(result)
        ok = process.get_successful()
        self._on_done(ok, "" if ok else (self._error or "The account could not be created."))


# -- signing in -----------------------------------------------------------------
SERVICE = "gdm-password"


def sign_in(username: str, password: str, problem: Callable[[str], None]) -> None:
    """Sign the new account in through GDM, once, with the password just chosen.

    The setup runs in GDM's initial-setup session, so it holds a greeter and a
    user verifier: begin verification for the account, answer the password
    question, and start the session as soon as it opens. GDM then closes the
    setup's display.
    """
    if fixture() is not None:
        log.info("fixture: would sign in @%s", username)
        return
    try:
        gi.require_version("Gdm", "1.0")
        from gi.repository import Gdm
    except (ImportError, ValueError) as error:
        problem(f"GDM is not available: {error}")
        return
    client = Gdm.Client.new()
    try:
        greeter = client.get_greeter_sync(None)
        verifier = client.get_user_verifier_sync(None)
    except GLib.Error as error:
        problem(error.message)
        return
    secret = {"password": password}

    def answer(verifier, service, _question) -> None:
        verifier.call_answer_query(service, secret.pop("password", ""), None, None, None)

    def opened(greeter, service, *_rest) -> None:
        greeter.call_start_session_when_ready(service, True, None, None, None)

    verifier.connect("secret-info-query", answer)
    verifier.connect("info-query", answer)
    verifier.connect("problem", lambda _v, _s, message: problem(message))
    greeter.connect("session-opened", opened)
    # Kept alive with the window: the session starts after this returns.
    sign_in.keep = (client, greeter, verifier)
    verifier.call_begin_verification_for_user(SERVICE, username, None, None, None)


# -- the setup session's own settings -----------------------------------------------
def _settings(schema_id: str) -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema_id, True) is None:
        return None
    return Gio.Settings.new(schema_id)


def set_live_layout(layout: str) -> None:
    """The keyboard picked on Hello types in this session at once (Type here to try it)."""
    settings = _settings("org.gnome.desktop.input-sources")
    if settings is not None:
        settings.set_value("sources", GLib.Variant("a(ss)", [("xkb", layout)]))


#: The accessibility switches Hello offers, and the setting each one is.
ACCESSIBILITY = {
    "screen-reader": ("org.gnome.desktop.a11y.applications", "screen-reader-enabled"),
    "zoom": ("org.gnome.desktop.a11y.applications", "screen-magnifier-enabled"),
    "large-text": ("org.gnome.desktop.interface", "text-scaling-factor"),
}


def accessibility(name: str) -> bool:
    schema, key = ACCESSIBILITY[name]
    settings = _settings(schema)
    if settings is None:
        return False
    if key == "text-scaling-factor":
        return settings.get_double(key) > 1.0
    return settings.get_boolean(key)


def set_accessibility(name: str, on: bool) -> None:
    schema, key = ACCESSIBILITY[name]
    settings = _settings(schema)
    if settings is None:
        return
    if key == "text-scaling-factor":
        settings.set_double(key, 1.25 if on else 1.0)
    else:
        settings.set_boolean(key, on)
