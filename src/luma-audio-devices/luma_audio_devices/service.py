# SPDX-License-Identifier: MPL-2.0
"""luma-audio-devices: Luma's user-session sound output devices service.

One resident process per session (started with graphical-session.target and
D-Bus activatable as org.projectluma.AudioDevices) that

* decides silently where sound goes when output devices come and go: displays
  and docks keep the current output, headphones and Bluetooth audio the person
  connects take it, manual choices are remembered per device (routing.py);
  it never asks and never shows a notification about output devices;
* runs the opt-in AirPlay picker and keeps remembered receivers' outputs in
  step with the network (airplay.py, avahi.py);
* publishes org.projectluma.AudioDevices1 for the Shell's sound menu and
  Settings (data/dbus/org.projectluma.AudioDevices1.xml).

It holds no timers while nothing happens: pw-dump, Avahi and D-Bus signals
wake it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import sys
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import __version__, classify, migration, routing  # noqa: E402
from .airplay import AirPlayManager  # noqa: E402
from .avahi import AirPlayDiscovery  # noqa: E402
from .pipewire import GraphMonitor, PipeWireUnavailable, WirePlumberControl  # noqa: E402
from .secrets import PasswordStore  # noqa: E402
from .state import Store  # noqa: E402

__all__ = ("main", "APP_ID", "OBJECT_PATH", "INTERFACE")

log = logging.getLogger("luma-audio-devices")

APP_ID = "org.projectluma.AudioDevices"
OBJECT_PATH = "/org/projectluma/AudioDevices"
INTERFACE = "org.projectluma.AudioDevices1"
ERROR_PREFIX = INTERFACE + ".Error."
SETTINGS_SCHEMA = "org.projectluma.audio-devices"
# 1.luma.1-2 asked about new outputs in a notification with this id.
LEGACY_PROMPT_NOTIFICATION = "new-output"
FAILURE_NOTIFICATION = "airplay-failed"
SAVE_DELAY_MS = 1500


def _interface_xml() -> str:
    candidates = [
        Path(os.environ.get("LUMA_AUDIO_DEVICES_DATA_DIR", "")) / "org.projectluma.AudioDevices1.xml",
        Path(__file__).resolve().parent.parent / "data" / "dbus" / "org.projectluma.AudioDevices1.xml",
        Path("/usr/share/dbus-1/interfaces/org.projectluma.AudioDevices1.xml"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError("org.projectluma.AudioDevices1.xml is not installed")


class _Preferences:
    """GSettings when the schema is installed, defaults otherwise."""

    def __init__(self, on_changed) -> None:
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(SETTINGS_SCHEMA, True) if source is not None else None
        self._settings = Gio.Settings.new(SETTINGS_SCHEMA) if schema is not None else None
        if self._settings is not None:
            self._settings.connect("changed", lambda _s, _key: on_changed())

    @property
    def switch_to_headphones(self) -> bool:
        return self._settings.get_boolean("switch-to-headphones") if self._settings else True


class AudioDevicesService(Gio.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.IS_SERVICE)
        self._registration_id = 0
        self._connection: Gio.DBusConnection | None = None
        self._browse_watches: dict[str, int] = {}
        self._tick_id = 0
        self._save_id = 0
        self._started = False
        self._default: str | None = None
        self._hidden_outputs: list[str] = []
        self._output_nodes: dict[str, classify.Output] = {}
        self._last_properties: dict[str, GLib.Variant] = {}

        self.store = Store.load()
        self.preferences = _Preferences(self._preferences_changed)
        self.policy = routing.RoutingPolicy(self.store.policy,
                                            switch_to_personal=self.preferences.switch_to_headphones,
                                            on_state_changed=lambda _state: self._schedule_save(),
                                            startup_grace=_env_seconds("LUMA_AUDIO_DEVICES_STARTUP_GRACE",
                                                                       routing.STARTUP_GRACE))
        self.control = WirePlumberControl(self._default_changed, self._pipewire_connected)
        self.graph = GraphMonitor(self._outputs_changed, lambda: None)
        self.passwords = PasswordStore()
        self.discovery = AirPlayDiscovery(self._discovery_changed)
        self.airplay = AirPlayManager(self.store, self.control, self.passwords, self.discovery,
                                      self._airplay_changed, self._airplay_sink_failed)
        action = Gio.SimpleAction.new("sound-settings", None)
        action.connect("activate", lambda *_: self._open_sound_settings())
        self.add_action(action)

    # GApplication -----------------------------------------------------------

    def do_dbus_register(self, connection, object_path) -> bool:
        info = Gio.DBusNodeInfo.new_for_xml(_interface_xml()).lookup_interface(INTERFACE)
        self._registration_id = connection.register_object(
            OBJECT_PATH, info, self._method_call, self._get_property, None)
        self._connection = connection
        return Gio.Application.do_dbus_register(self, connection, object_path)

    def do_dbus_unregister(self, connection, object_path) -> None:
        if self._registration_id:
            connection.unregister_object(self._registration_id)
            self._registration_id = 0
        Gio.Application.do_dbus_unregister(self, connection, object_path)

    def do_startup(self) -> None:
        Gio.Application.do_startup(self)
        self.hold()
        migration.remove_temporary_airplay_override()
        self.control.connect()
        self.graph.start()
        self.discovery.start()
        self.discovery.set_watched(self.airplay.watched())
        # A prompt left on screen by 1.luma.1-2 goes away with them.
        self.withdraw_notification(LEGACY_PROMPT_NOTIFICATION)
        if self.store.legacy_answers:
            log.info("dropping prompt answers saved by an earlier version")
            self._schedule_save()

    def do_activate(self) -> None:
        pass

    def do_shutdown(self) -> None:
        self.graph.stop()
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
            self._save()
        Gio.Application.do_shutdown(self)

    # Sound graph ------------------------------------------------------------

    def _pipewire_connected(self) -> None:
        self.airplay.pipewire_reconnected()

    def _outputs_changed(self, nodes) -> None:
        now = time.time()
        described = {name: classify.describe(node.props, node.route) for name, node in nodes.items()}
        actions: list = []
        if not self._started:
            self._started = True
            self._default = self.graph.graph.default_sink()
            self.policy.start([o for o in described.values() if o.local], self._default, now)
        else:
            for name in list(self._output_nodes):
                if name not in described or described[name].key != self._output_nodes[name].key:
                    log.debug("output removed: %s", name)
                    actions += self.policy.output_removed(name, now, self.graph.graph.default_sink(configured=True))
            for name, output in described.items():
                if name not in self._output_nodes or self._output_nodes[name].key != output.key:
                    log.debug("output added: %s (%s)", name, output.key)
                    actions += self.policy.output_added(output, now)
        self._output_nodes = described
        # WirePlumber's metadata signal can arrive late (or only when something
        # else wakes the loop); pw-dump reports every default change, after the
        # outputs of the same burst, so an unplug is never read as a choice.
        graph_default = self.graph.graph.default_sink()
        if self._started and graph_default is not None and graph_default != self._default:
            log.debug("default output is now %s (graph)", graph_default)
            self._default = graph_default
            actions += self.policy.default_changed(graph_default, now)
        self.airplay.outputs_changed(set(described))
        hidden = sorted(name for name, node in nodes.items() if classify.is_hidden_output(node.props))
        if hidden != self._hidden_outputs:
            self._hidden_outputs = hidden
            self._emit_properties_changed()
        self._perform(actions)
        self._emit_signal("OutputDevicesChanged")

    def _default_changed(self, node_name: str | None) -> None:
        if node_name == self._default:
            return
        log.debug("default output is now %s", node_name)
        self._default = node_name
        if not self._started:
            return
        self._perform(self.policy.default_changed(node_name, time.time()))

    def _preferences_changed(self) -> None:
        log.info("switching to headphones and Bluetooth audio %s",
                 "on" if self.preferences.switch_to_headphones else "off")
        self.policy.switch_to_personal = self.preferences.switch_to_headphones

    # Policy actions ---------------------------------------------------------

    def _perform(self, actions) -> None:
        for action in actions:
            if isinstance(action, routing.Decision):
                _record_decision(action)
                if action.event == "remembered-choice":
                    self._emit_signal("OutputDevicesChanged")
            elif isinstance(action, routing.SwitchTo):
                self.control.set_default_sink(action.node_name)
        self._schedule_tick()

    def _schedule_tick(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        deadline = self.policy.next_deadline()
        if deadline is None:
            return
        delay = max(0, int((deadline - time.time()) * 1000) + 10)
        self._tick_id = GLib.timeout_add(delay, self._tick)

    def _tick(self) -> bool:
        self._tick_id = 0
        self._perform(self.policy.tick(time.time()))
        return GLib.SOURCE_REMOVE

    def _open_sound_settings(self) -> None:
        info = Gio.DesktopAppInfo.new("gnome-sound-panel.desktop")
        try:
            if info is not None:
                info.launch([], None)
            else:
                Gio.Subprocess.new(["gnome-control-center", "sound"], Gio.SubprocessFlags.NONE)
        except GLib.Error as error:
            log.warning("cannot open Sound settings: %s", error.message)

    def _schedule_save(self) -> None:
        if not self._save_id:
            self._save_id = GLib.timeout_add(SAVE_DELAY_MS, self._save)

    def _save(self) -> bool:
        self._save_id = 0
        try:
            self.store.prune(time.time())
            self.store.save()
        except OSError as error:
            log.warning("cannot save %s: %s", self.store.path, error)
        return GLib.SOURCE_REMOVE

    # AirPlay ----------------------------------------------------------------

    def _discovery_changed(self) -> None:
        self.airplay.reconcile()
        self._emit_properties_changed()

    def _airplay_changed(self) -> None:
        self._emit_signal("AirPlayReceiversChanged")
        self._emit_properties_changed()

    def _airplay_sink_failed(self, name: str) -> None:
        notification = Gio.Notification.new(f"Can’t play to {name}")
        notification.set_body("It stopped responding, so sound is playing on this computer again.")
        notification.set_icon(Gio.ThemedIcon.new("luma-network-speaker-symbolic"))
        notification.set_default_action("app.sound-settings")
        self.send_notification(FAILURE_NOTIFICATION, notification)

    # D-Bus ------------------------------------------------------------------

    def _method_call(self, connection, sender, _path, _interface, method, parameters, invocation) -> None:
        try:
            if method == "StartAirPlayBrowsing":
                self._watch_browser(connection, sender)
                self.airplay.start_browsing(sender)
                invocation.return_value(None)
            elif method == "StopAirPlayBrowsing":
                self._unwatch_browser(connection, sender)
                self.airplay.stop_browsing(sender)
                invocation.return_value(None)
            elif method == "GetAirPlayReceivers":
                invocation.return_value(GLib.Variant("(aa{sv})", ([_to_vardict(e) for e in self.airplay.receivers()],)))
            elif method == "ConnectAirPlayReceiver":
                receiver_id, password = parameters.unpack()

                def reply(error) -> None:
                    if error is None:
                        invocation.return_value(None)
                    else:
                        invocation.return_dbus_error(ERROR_PREFIX + error.code, error.message)

                self.airplay.connect(receiver_id, password or None, reply)
            elif method == "ForgetAirPlayReceiver":
                (receiver_id,) = parameters.unpack()
                if self.airplay.forget(receiver_id):
                    invocation.return_value(None)
                else:
                    invocation.return_dbus_error(ERROR_PREFIX + "NotFound", "That receiver isn’t remembered.")
            elif method == "GetOutputDevices":
                invocation.return_value(GLib.Variant("(aa{sv})", (self._output_devices(),)))
            elif method == "ForgetOutputDevice":
                (key,) = parameters.unpack()
                self._perform(self.policy.forget_device(key, time.time()))
                self._emit_signal("OutputDevicesChanged")
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as error:  # a bug must not take the session service down
            log.exception("%s failed", method)
            invocation.return_dbus_error(ERROR_PREFIX + "Failed", str(error))

    def _get_property(self, _connection, _sender, _path, _interface, name) -> GLib.Variant | None:
        return self._properties().get(name)

    def _properties(self) -> dict[str, GLib.Variant]:
        return {
            "Version": GLib.Variant("u", 1),
            "AirPlayAvailable": GLib.Variant("b", self.discovery.available),
            "AirPlayBrowsing": GLib.Variant("b", self.airplay.browsing),
            "HiddenOutputs": GLib.Variant("as", self._hidden_outputs),
        }

    def _emit_properties_changed(self) -> None:
        if self._connection is None:
            return
        current = self._properties()
        changed = {k: v for k, v in current.items() if self._last_properties.get(k) != v}
        self._last_properties = current
        if not changed:
            return
        self._connection.emit_signal(None, OBJECT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                     GLib.Variant("(sa{sv}as)", (INTERFACE, changed, [])))

    def _emit_signal(self, name: str) -> None:
        if self._connection is not None:
            self._connection.emit_signal(None, OBJECT_PATH, INTERFACE, name, None)

    def _watch_browser(self, connection, sender: str) -> None:
        if sender in self._browse_watches:
            return

        def vanished(_connection, name) -> None:
            log.info("AirPlay picker client %s left the bus", name)
            self._unwatch_browser(connection, sender)
            self.airplay.stop_browsing(sender)

        self._browse_watches[sender] = Gio.bus_watch_name_on_connection(
            connection, sender, Gio.BusNameWatcherFlags.NONE, None, vanished)

    def _unwatch_browser(self, _connection, sender: str) -> None:
        watch = self._browse_watches.pop(sender, 0)
        if watch:
            Gio.bus_unwatch_name(watch)

    def _output_devices(self) -> list[dict]:
        present = self.policy.present_keys()
        devices = []
        for key, memory in sorted(self.store.policy.devices.items(), key=lambda item: item[1].name.casefold()):
            if key.startswith(("airplay:", "network:")):
                continue
            devices.append(_to_vardict({
                "key": key, "name": memory.name or key, "icon": memory.icon or "audio-card-symbolic",
                "auto_switch": memory.auto_switch, "present": key in present, "last_seen": int(memory.last_seen),
            }))
        return devices


def _record_decision(decision: routing.Decision) -> None:
    """One structured journal entry per routing decision, for Luma Vitals
    (ADR-026) and for anyone asking "why did my sound move?"."""
    current = f" (sound was on {decision.current})" if decision.current else ""
    summary = f"sound output {decision.event}: {decision.name}: {decision.reason}{current}"
    details = {"event": decision.event, "device": decision.key, "name": decision.name,
               "reason": decision.reason, "current": decision.current}
    try:
        from systemd import journal as systemd_journal  # optional, as in luma-vitals
        systemd_journal.send(summary, SYSLOG_IDENTIFIER="luma-audio-devices", PRIORITY=6,
                             LUMA_VITALS_KIND="audio-routing", LUMA_VITALS_UNIT="luma-audio-devices.service",
                             LUMA_VITALS_DETAILS=json.dumps(details))
    except ImportError:
        log.info("%s [LUMA_VITALS_KIND=audio-routing %s]", summary, json.dumps(details))


def _env_seconds(name: str, default: float) -> float:
    """Test harnesses shorten the startup grace; nothing else reads this."""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _to_vardict(entry: dict) -> dict[str, GLib.Variant]:
    result = {}
    for key, value in entry.items():
        if isinstance(value, bool):
            result[key] = GLib.Variant("b", value)
        elif isinstance(value, int):
            result[key] = GLib.Variant("x", value)
        elif isinstance(value, GLib.Variant):
            result[key] = value
        else:
            result[key] = GLib.Variant("s", str(value))
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(level=os.environ.get("LUMA_AUDIO_DEVICES_LOG", "INFO").upper(),
                        format="%(levelname)s %(message)s")
    if len(argv) > 1 and argv[1] not in ("--gapplication-service",) and not argv[1].startswith("--"):
        from .cli import main as cli_main
        return cli_main(argv[1:])
    if "--version" in argv:
        print(__version__)
        return 0
    try:
        service = AudioDevicesService()
    except PipeWireUnavailable as error:
        log.error("%s", error)
        return 1
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (service.quit(), GLib.SOURCE_REMOVE)[1])
    return service.run([argv[0]])
