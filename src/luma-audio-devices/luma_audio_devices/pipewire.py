# SPDX-License-Identifier: MPL-2.0
"""The service's two connections to the sound system.

``GraphMonitor`` runs ``pw-dump --monitor`` and reports which outputs appear,
disappear or become usable. pw-dump prints fully parsed parameters (routes and
their availability), which WirePlumber's GObject introspection cannot hand to
Python, and it costs nothing while nothing changes.

``WirePlumberControl`` is a WirePlumber client (libwireplumber-0.5 through
GObject introspection). It follows the "default" metadata, sets the configured
default output the same way GNOME Settings does, removes the
luma.audio.manual-only-outputs value 1.luma.1-2 saved, and hosts the
libpipewire-module-raop-sink instance of every AirPlay receiver in use, so an
AirPlay output exists exactly as long as this service does.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib, GObject  # noqa: E402

from .graph import ChunkDecoder, Graph, OutputNode  # noqa: E402

__all__ = ("GraphMonitor", "WirePlumberControl", "PipeWireUnavailable")

log = logging.getLogger("luma-audio-devices")

MANUAL_ONLY_SETTING = "luma.audio.manual-only-outputs"
CONFIGURED_SINK_KEY = "default.configured.audio.sink"
DEFAULT_SINK_KEY = "default.audio.sink"
RAOP_SINK_MODULE = "libpipewire-module-raop-sink"


class PipeWireUnavailable(RuntimeError):
    pass


class GraphMonitor:
    """Follow the PipeWire graph through pw-dump --monitor.

    on_outputs(outputs: dict[str, OutputNode]) is called with the usable
    outputs after every settled burst of changes; on_ready() once, after the
    first complete dump.
    """

    RESTART_DELAY_MS = 2000
    COALESCE_MS = 120

    def __init__(self, on_outputs: Callable[[dict[str, OutputNode]], None],
                 on_ready: Callable[[], None], command: list[str] | None = None) -> None:
        self._on_outputs = on_outputs
        self._on_ready = on_ready
        self._command = command or ["pw-dump", "--monitor", "--no-colors"]
        self.graph = Graph()
        self._decoder = ChunkDecoder()
        self._process: Gio.Subprocess | None = None
        self._stream: Gio.InputStream | None = None
        self._cancellable: Gio.Cancellable | None = None
        self._flush_id = 0
        self._ready = False
        self._stopped = False

    def start(self) -> None:
        self._stopped = False
        self._spawn()

    def stop(self) -> None:
        self._stopped = True
        if self._cancellable is not None:
            self._cancellable.cancel()
        if self._process is not None:
            self._process.force_exit()
        self._process = None

    def _spawn(self) -> None:
        self.graph = Graph()
        self._decoder = ChunkDecoder()
        self._cancellable = Gio.Cancellable()
        try:
            self._process = Gio.Subprocess.new(
                self._command,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as error:
            log.warning("cannot run %s: %s", self._command[0], error.message)
            self._schedule_restart()
            return
        self._stream = self._process.get_stdout_pipe()
        self._process.wait_async(self._cancellable, self._on_exit)
        self._read()

    def _read(self) -> None:
        if self._stream is None:
            return
        self._stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, self._cancellable, self._on_bytes)

    def _on_bytes(self, stream, result) -> None:
        try:
            data = stream.read_bytes_finish(result)
        except GLib.Error:
            return
        if data.get_size() == 0:
            return
        for document in self._decoder.feed(data.get_data().decode("utf-8", "replace")):
            if isinstance(document, list):
                self.graph.apply(document)
                if not self._ready:
                    self._ready = True
                    self._on_ready()
                self._schedule_flush()
        self._read()

    def _schedule_flush(self) -> None:
        if self._flush_id:
            return
        self._flush_id = GLib.timeout_add(self.COALESCE_MS, self._flush)

    def _flush(self) -> bool:
        self._flush_id = 0
        outputs = {name: node for name, node in self.graph.output_nodes().items() if node.available}
        try:
            self._on_outputs(outputs)
        except Exception:  # keep following the graph whatever one update does
            log.exception("handling a graph change failed")
        return GLib.SOURCE_REMOVE

    def _on_exit(self, process, result) -> None:
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        if self._stopped:
            return
        log.info("pw-dump exited; following the graph again shortly")
        self._stream = None
        self._schedule_restart()

    def _schedule_restart(self) -> None:
        if self._stopped:
            return

        def restart() -> bool:
            if not self._stopped:
                self._spawn()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(self.RESTART_DELAY_MS, restart)


class WirePlumberControl:
    """A WirePlumber client for writes, settings and in-process modules."""

    RECONNECT_DELAY_MS = 2000

    def __init__(self, on_default_changed: Callable[[str | None], None],
                 on_connected: Callable[[], None]) -> None:
        try:
            gi.require_version("Wp", "0.5")
            from gi.repository import Wp
        except (ImportError, ValueError) as error:
            raise PipeWireUnavailable(f"WirePlumber introspection data is missing: {error}") from error
        self.Wp = Wp
        Wp.init(Wp.InitFlags.ALL)
        self._on_default_changed = on_default_changed
        self._on_connected = on_connected
        self._core = None
        self._objects = None
        self._metadata = None
        self._settings = None
        self._modules: dict[str, object] = {}
        self._legacy_cleared = False
        self.connected = False

    # Connection -------------------------------------------------------------

    def connect(self) -> None:
        Wp = self.Wp
        self._core = Wp.Core.new(None, None, None)
        # WpCore.connect() is the PipeWire connection; signals need GObject's.
        GObject.Object.connect(self._core, "disconnected", self._on_disconnected)
        if not self._core.connect():
            log.info("PipeWire is not reachable yet")
            self._schedule_reconnect()
            return
        self._objects = Wp.ObjectManager.new()
        interest = Wp.ObjectInterest.new_type(Wp.Metadata)
        self._objects.add_interest_full(interest)
        self._objects.request_object_features(Wp.Metadata, Wp.OBJECT_FEATURES_ALL)
        self._objects.connect("object-added", self._on_object_added)
        self._objects.connect("object-removed", self._on_object_removed)
        self._core.install_object_manager(self._objects)

        self._settings = Wp.Settings.new(self._core, "sm-settings")
        self._settings.activate(Wp.OBJECT_FEATURES_ALL, None, self._on_settings_ready)
        self.connected = True
        self._on_connected()

    def _on_disconnected(self, _core) -> None:
        log.warning("lost the connection to PipeWire")
        self.connected = False
        self._metadata = None
        self._settings = None
        self._modules.clear()
        self._objects = None
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        def retry() -> bool:
            self.connect()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(self.RECONNECT_DELAY_MS, retry)

    def _on_object_added(self, _om, obj) -> None:
        props = self.Wp.GlobalProxy.get_global_properties(obj)
        if props is None or props.get("metadata.name") != "default":
            return
        self._metadata = obj
        # wp_metadata_find() and the item iterator do not survive GObject
        # introspection (the out strings come back as garbage), so the value
        # present at start comes from the pw-dump graph and changes from here.
        obj.connect("changed", self._on_metadata_changed)

    def _on_object_removed(self, _om, obj) -> None:
        if obj is self._metadata:
            self._metadata = None

    def _on_metadata_changed(self, _metadata, subject, key, _type, value) -> None:
        if subject == 0 and key == DEFAULT_SINK_KEY:
            try:
                self._on_default_changed(_name_from_json(value))
            except Exception:
                log.exception("handling a default output change failed")

    def _on_settings_ready(self, settings, result) -> None:
        try:
            settings.activate_finish(result)
        except GLib.Error as error:
            log.warning("WirePlumber settings are unavailable: %s", error.message)
            return
        self._clear_legacy_manual_only(settings)

    # Defaults ---------------------------------------------------------------

    def set_default_sink(self, node_name: str) -> bool:
        """Make node_name the configured default output, as a person choosing
        it in Settings does. WirePlumber records the choice in its history."""
        if self._metadata is None:
            log.warning("cannot switch to %s: no default metadata", node_name)
            return False
        self._metadata.set(0, CONFIGURED_SINK_KEY, "Spa:String:JSON",
                           json.dumps({"name": node_name}))
        return True

    # Settings ---------------------------------------------------------------

    def _clear_legacy_manual_only(self, settings) -> None:
        """1.luma.1-2 saved "Don't ask again" devices in WirePlumber's
        persistent settings. Nothing reads them now; remove the saved value
        once so no trace of the prompts is left behind."""
        if self._legacy_cleared:
            return
        self._legacy_cleared = True
        try:
            current = settings.get(MANUAL_ONLY_SETTING)
            saved = settings.get_saved(MANUAL_ONLY_SETTING) if hasattr(settings, "get_saved") else current
            empty = lambda value: value is None or value.to_string().replace(" ", "") in ("[]", "")
            if empty(current) and empty(saved):
                return
            # Empty for this session, and nothing saved for the next one.
            settings.set(MANUAL_ONLY_SETTING, self.Wp.SpaJson.new_from_string("[]"))
            if not (hasattr(settings, "delete") and settings.delete(MANUAL_ONLY_SETTING)):
                settings.save(MANUAL_ONLY_SETTING)
            current = saved if empty(current) else current
            log.info("removed the \"Don't ask again\" outputs saved by an earlier version: %s",
                     current.to_string())
        except Exception as error:  # a stale inert value must never stop the service
            log.warning("cannot remove %s: %s", MANUAL_ONLY_SETTING, error)

    # Modules ----------------------------------------------------------------

    def load_raop_sink(self, handle: str, args: dict) -> bool:
        self.unload(handle)
        if not self.connected:
            return False
        module = self.Wp.ImplModule.load(self._core, RAOP_SINK_MODULE, json.dumps(args), None)
        if module is None:
            log.warning("PipeWire refused the AirPlay sink for %s", handle)
            return False
        self._modules[handle] = module
        return True

    def unload(self, handle: str) -> None:
        # Dropping the last reference destroys the pw_impl_module, which
        # removes the sink node from the graph.
        self._modules.pop(handle, None)

    def loaded(self, handle: str) -> bool:
        return handle in self._modules

    def module_alive(self, handle: str) -> bool:
        """False once module-raop-sink destroyed itself (the receiver refused
        the stream or stopped answering)."""
        module = self._modules.get(handle)
        # A gpointer property: PyGObject hands back the address, 0 once
        # WpImplModule saw the module's free event.
        return module is not None and bool(module.get_property("pw-impl-module"))


def _name_from_json(value) -> str | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None

