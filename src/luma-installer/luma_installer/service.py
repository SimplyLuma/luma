from __future__ import annotations

import subprocess
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .errors import InstallerError
from .model import kind_for_path, supports_content_type

INTERFACE_XML = """
<node>
  <interface name="org.projectluma.ApplicationInstaller2">
    <method name="Supports">
      <arg type="s" name="content_type" direction="in"/>
      <arg type="b" name="supported" direction="out"/>
    </method>
    <method name="RequestUninstall">
      <arg type="s" name="application_id" direction="in"/>
    </method>
    <method name="RequestInstall">
      <arg type="s" name="uri" direction="in"/>
    </method>
  </interface>
</node>
"""


def local_path_from_uri(uri: str) -> Path:
    file = Gio.File.new_for_uri(uri)
    path = file.get_path()
    if path is None or not file.is_native():
        raise InstallerError("Only local application packages can be installed.")
    candidate = Path(path).absolute()
    if not candidate.is_file() or candidate.is_symlink():
        raise InstallerError("The application package must be a regular local file.")
    if kind_for_path(candidate) is None:
        raise InstallerError("Luma does not recognize this application package format.")
    return candidate


class Service:
    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        self.registration = 0

    def on_method_call(self, _connection, _sender, _path, _interface, method, parameters, invocation) -> None:
        try:
            if method == "Supports":
                invocation.return_value(GLib.Variant("(b)", (supports_content_type(parameters.unpack()[0]),)))
                return
            if method == "RequestUninstall":
                identity = parameters.unpack()[0]
                if not identity or len(identity) > 255 or "/" in identity or "\\" in identity:
                    raise InstallerError("Invalid application identity.")
                subprocess.Popen(["/usr/bin/luma-install", "--remove", identity], start_new_session=True, close_fds=True)
                invocation.return_value(None)
                return
            if method == "RequestInstall":
                path = local_path_from_uri(parameters.unpack()[0])
                subprocess.Popen(["/usr/bin/luma-install", str(path)], start_new_session=True, close_fds=True)
                invocation.return_value(None)
                return
            raise InstallerError("Unknown installer request.")
        except (InstallerError, OSError) as error:
            invocation.return_dbus_error("org.projectluma.Installer.Error", str(error))

    def bus_acquired(self, connection: Gio.DBusConnection, _name: str) -> None:
        node = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
        self.registration = connection.register_object(
            "/org/projectluma/ApplicationInstaller2", node.interfaces[0], self.on_method_call, None, None
        )

    def run(self) -> int:
        owner = Gio.bus_own_name(
            Gio.BusType.SESSION, "org.projectluma.ApplicationInstaller2",
            Gio.BusNameOwnerFlags.NONE, self.bus_acquired, None, lambda *_args: self.loop.quit(),
        )
        try:
            self.loop.run()
        finally:
            Gio.bus_unown_name(owner)
        return 0


def main() -> int:
    return Service().run()


if __name__ == "__main__":
    raise SystemExit(main())
