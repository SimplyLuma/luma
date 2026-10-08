from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .apk import local_android_package_from_uri, supports_content_type
from .appearance import settings as appearance_settings
from .appearance import accessibility_settings, surface_treatment, treatment_settings
from .config import load_device_profile, load_runtime_config
from .engine import WaydroidEngine
from .activation import activate_for_caller
from .errors import LumaAndroidError


LAUNCHER_STARTUP_RECONCILE_SECONDS = 5


INSTALLER_XML = """
<node>
  <interface name="org.projectluma.ApplicationInstaller1">
    <method name="Supports">
      <arg type="s" name="content_type" direction="in"/>
      <arg type="b" name="supported" direction="out"/>
    </method>
    <method name="RequestInstall">
      <arg type="s" name="uri" direction="in"/>
    </method>
  </interface>
</node>
"""

RUNTIME_XML = """
<node>
  <interface name="org.projectluma.Android1">
    <method name="GetStatus"><arg type="s" name="status_json" direction="out"/></method>
    <method name="Launch"><arg type="s" name="package" direction="in"/></method>
    <method name="ActivateExisting">
      <arg type="s" name="package" direction="in"/>
      <arg type="b" name="restored" direction="out"/>
    </method>
    <method name="CloseExisting">
      <arg type="s" name="package" direction="in"/>
      <arg type="b" name="requested" direction="out"/>
    </method>
    <method name="Pause"/>
    <method name="Resume"/>
  </interface>
</node>
"""


class Service:
    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        config = load_runtime_config()
        self.engine = WaydroidEngine(config.engine)
        self.multi_window = config.multi_window
        self.registrations: list[int] = []
        self.connection: Gio.DBusConnection | None = None
        self.launcher_sync_id = 0
        self.launcher_monitor: Gio.FileMonitor | None = None
        self.appearance_settings = appearance_settings()
        self.treatment_settings = treatment_settings()
        self.accessibility_settings = accessibility_settings()
        self.activation_slots = threading.BoundedSemaphore(2)
        self.activation_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="android-activation")
        self.appearance_lock = threading.Lock()
        self.appearance_pending = None
        self.appearance_worker = None
        if self.appearance_settings is not None:
            self.appearance_settings.connect("changed::color-scheme", self.on_appearance_changed)
        if self.treatment_settings is not None:
            self.treatment_settings.connect("changed::surface-treatment", self.on_appearance_changed)
            self.treatment_settings.connect("changed::reduce-transparency", self.on_appearance_changed)
        if self.accessibility_settings is not None:
            self.accessibility_settings.connect("changed::high-contrast", self.on_appearance_changed)
        self.engine.synchronize_launchers()
        self.monitor_launchers()
        # Waydroid may restore its generated launchers after the graphical
        # session target starts us. The directory monitor handles ordinary
        # changes; this bounded post-start pass closes the boot-order window
        # without introducing a resident polling loop.
        self.launcher_sync_id = GLib.timeout_add_seconds(
            LAUNCHER_STARTUP_RECONCILE_SECONDS,
            self.reconcile_launchers,
        )
        GLib.timeout_add_seconds(4, self.start_warm_session_if_needed)

    def on_appearance_changed(self, settings, _key) -> None:
        appearance = surface_treatment()
        with self.appearance_lock:
            self.appearance_pending = appearance
            if self.appearance_worker is None:
                self.appearance_worker = threading.Thread(
                    target=self.sync_appearance_changes, daemon=True,
                    name="android-appearance")
                self.appearance_worker.start()

    def sync_appearance_changes(self) -> None:
        # Coalesce events in the existing broker. Do not wake a stopped runtime
        # or block the session bus while Waydroid processes a property update.
        while True:
            with self.appearance_lock:
                appearance = self.appearance_pending
                self.appearance_pending = None
                if appearance is None:
                    self.appearance_worker = None
                    return
            try:
                if self.engine.status().get("session") == "RUNNING":
                    self.engine.sync_host_appearance(appearance)
            except (LumaAndroidError, OSError):
                pass

    def monitor_launchers(self) -> None:
        data_root = Path(
            os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
        )
        application_root = data_root / "applications"
        application_root.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            self.launcher_monitor = Gio.File.new_for_path(
                str(application_root)
            ).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            # The broker still reconciles at startup and after every supported
            # install. A missing monitor must not make the runtime unavailable.
            self.launcher_monitor = None
            return
        self.launcher_monitor.connect("changed", self.on_launcher_changed)

    def on_launcher_changed(
        self,
        _monitor: Gio.FileMonitor,
        file: Gio.File,
        other_file: Gio.File | None,
        _event: Gio.FileMonitorEvent,
    ) -> None:
        names = {file.get_basename(), other_file.get_basename() if other_file else None}
        if not any(name and name.startswith("waydroid.") and name.endswith(".desktop")
                   for name in names):
            return
        if self.launcher_sync_id:
            GLib.source_remove(self.launcher_sync_id)
        self.launcher_sync_id = GLib.timeout_add(250, self.reconcile_launchers)

    def reconcile_launchers(self) -> bool:
        self.launcher_sync_id = 0
        try:
            self.engine.synchronize_launchers()
        except (LumaAndroidError, OSError, UnicodeError):
            pass
        return GLib.SOURCE_REMOVE

    def start_warm_session_if_needed(self) -> bool:
        config = load_runtime_config()
        profile = load_device_profile()
        application_root = Path.home() / ".local/share/applications"
        if config.start_when_apps_installed and profile.keep_warm and any(
            application_root.glob("waydroid.*.desktop")
        ):
            subprocess.Popen(
                [
                    "/usr/bin/systemctl",
                    "--user",
                    "start",
                    "--no-block",
                    "luma-android-session.service",
                ],
                start_new_session=True,
                close_fds=True,
            )
        return False

    def on_method_call(
        self,
        connection: Gio.DBusConnection,
        sender: str,
        object_path: str,
        interface_name: str,
        method_name: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        del object_path
        try:
            if interface_name == "org.projectluma.ApplicationInstaller1":
                if method_name == "Supports":
                    content_type = parameters.unpack()[0]
                    invocation.return_value(
                        GLib.Variant("(b)", (supports_content_type(content_type),))
                    )
                    return
                if method_name == "RequestInstall":
                    path = local_android_package_from_uri(parameters.unpack()[0])
                    # The full inspector runs in the installer too. This bounded
                    # preflight prevents bus callers from using activation as a
                    # general-purpose process launcher for arbitrary files.
                    if not path.is_file() or path.is_symlink():
                        raise LumaAndroidError("The package is not a regular local file.")
                    subprocess.Popen(
                        ["/usr/bin/luma-android-installer", str(path)],
                        start_new_session=True,
                        close_fds=True,
                    )
                    invocation.return_value(None)
                    return
            elif interface_name == "org.projectluma.Android1":
                if method_name == "GetStatus":
                    invocation.return_value(
                        GLib.Variant("(s)", (json.dumps(self.engine.status(), sort_keys=True),))
                    )
                    return
                if method_name in {"Launch", "ActivateExisting", "CloseExisting"}:
                    self.request_activation(connection, sender, parameters.unpack()[0],
                                            invocation, launch=method_name == "Launch", close=method_name == "CloseExisting")
                    return
                if method_name == "Pause":
                    self.engine.stop_session()
                    invocation.return_value(None)
                    return
                if method_name == "Resume":
                    self.engine.ensure_ready(self.multi_window, detached=True)
                    invocation.return_value(None)
                    return
            raise LumaAndroidError("Unknown runtime request.")
        except LumaAndroidError as error:
            invocation.return_dbus_error("org.projectluma.Error.Failed", str(error))

    def request_activation(self, connection, sender, package, invocation, *, launch=False, close=False):
        # Reserve before submission: two workers and no unbounded waiting queue.
        if not self.activation_slots.acquire(blocking=False):
            invocation.return_dbus_error("org.projectluma.Error.Busy", "Android is handling another request.")
            return

        def complete(value=None, error=None):
            try:
                if error is not None:
                    invocation.return_dbus_error("org.projectluma.Error.Failed", error)
                else:
                    invocation.return_value(None if launch else GLib.Variant("(b)", (value,)))
            finally:
                self.activation_slots.release()
            return GLib.SOURCE_REMOVE

        def work():
            try:
                value = activate_for_caller(connection, sender, package, self.engine, launch=launch, close=close)
            except Exception:
                logging.getLogger(__name__).exception("Android host-window request failed")
                # Do not expose process inspection or registry diagnostics over the bus.
                GLib.idle_add(complete, None, "Android could not restore this application.")
            else:
                GLib.idle_add(complete, value)

        try:
            self.activation_workers.submit(work)
        except RuntimeError:
            self.activation_slots.release()
            invocation.return_dbus_error("org.projectluma.Error.Unavailable", "Android is shutting down.")

    def on_bus_acquired(self, connection: Gio.DBusConnection, name: str) -> None:
        del name
        if self.connection is not None:
            return
        self.connection = connection
        for xml, path in (
            (INSTALLER_XML, "/org/projectluma/ApplicationInstaller1"),
            (RUNTIME_XML, "/org/projectluma/Android1"),
        ):
            node = Gio.DBusNodeInfo.new_for_xml(xml)
            registration = connection.register_object(
                path, node.interfaces[0], self.on_method_call, None, None
            )
            self.registrations.append(registration)

    def run(self) -> int:
        owners = [
            Gio.bus_own_name(
                Gio.BusType.SESSION,
                name,
                Gio.BusNameOwnerFlags.NONE,
                self.on_bus_acquired,
                None,
                lambda *_args: self.loop.quit(),
            )
            for name in ("org.projectluma.ApplicationInstaller1", "org.projectluma.Android1")
        ]
        try:
            self.loop.run()
        finally:
            for owner in owners:
                Gio.bus_unown_name(owner)
        return 0


def main() -> int:
    return Service().run()


if __name__ == "__main__":
    raise SystemExit(main())
