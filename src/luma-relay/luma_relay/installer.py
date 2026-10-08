from __future__ import annotations

import platform
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from luma_appkit import AppWindow, CommandRegistry, Island, Toolbar

from .config import load_config
from .engine import WineEngine
from .errors import RelayError
from .package import WindowsPackage, host_supports, inspect_windows_package


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} bytes"


class InstallerWindow(AppWindow):
    def __init__(self, application: Adw.Application, path: Path) -> None:
        super().__init__(application=application, app_id="org.projectluma.RelayInstaller", title="Open Windows Application", icon_name="org.projectluma.RelayInstaller", commands=CommandRegistry(()), default_width=540, default_height=590, minimum_width=330, minimum_height=430)
        self.path = path
        self.config = load_config()
        self.engine = WineEngine(self.config)
        self.package: WindowsPackage | None = None

        toolbar = Adw.ToolbarView()
        clamp = Adw.Clamp(maximum_size=580, tightening_threshold=430)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for edge in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{edge}")(20 if edge in {"start", "end"} else 24)
        clamp.set_child(content)
        toolbar.set_content(clamp)
        island = Island()
        island.append(toolbar)
        self.set_body(island)

        icon = Gtk.Image.new_from_icon_name("application-x-executable")
        icon.set_pixel_size(58)
        content.append(icon)
        title = Gtk.Label(label=path.name)
        title.add_css_class("title-2")
        title.set_wrap(True)
        content.append(title)
        description = Gtk.Label(
            label=(
                "Relay gives this Windows application its own private environment. "
                "It cannot see your Luma files or other applications unless you grant access later."
            )
        )
        description.set_wrap(True)
        description.set_justify(Gtk.Justification.CENTER)
        description.add_css_class("dim-label")
        content.append(description)
        self.group = Adw.PreferencesGroup()
        content.append(self.group)
        self.status = Gtk.Label(label="Inspecting without running…")
        self.status.set_wrap(True)
        content.append(self.status)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.action = Gtk.Button(label="Continue")
        self.action.add_css_class("suggested-action")
        self.action.set_sensitive(False)
        self.action.connect("clicked", self.install)
        actions.append(cancel)
        actions.append(self.action)
        content.append(actions)
        threading.Thread(target=self.inspect, daemon=True).start()

    def add_row(self, title: str, subtitle: str) -> None:
        self.group.add(Adw.ActionRow(title=title, subtitle=subtitle))

    def inspect(self) -> None:
        try:
            package = inspect_windows_package(self.path, self.config.max_package_bytes)
            GLib.idle_add(self.inspected, package)
        except RelayError as error:
            GLib.idle_add(self.failed, str(error))

    def inspected(self, package: WindowsPackage) -> bool:
        self.package = package
        from . import fex
        supported, processor_note = host_supports(package, platform.machine().lower(), fex_available=fex.available())
        self.add_row("Type", "Windows Installer package" if package.package_format == "msi" else "Windows executable")
        self.add_row("Size", human_size(package.byte_size))
        self.add_row("Processor", f"{package.processor} · {processor_note}")
        identity = (
            "A Windows Authenticode signature is embedded; Windows will perform final validation"
            if package.authenticode_present
            else "No embedded Authenticode record was found; install only if you trust the source"
        )
        self.add_row("Publisher identity", identity)
        if package.dotnet_metadata_present:
            self.add_row("Compatibility", "Uses .NET metadata; Relay begins with the open Wine Mono runtime")
        self.add_row("Fingerprint", package.sha256[:24] + "…")
        if supported:
            self.status.set_label(
                "Ready to install" if package.suggested_action == "install" else "Ready to open and keep in Applications"
            )
            self.action.set_label("Install" if package.suggested_action == "install" else "Open")
            self.action.set_sensitive(True)
        else:
            self.failed(processor_note)
        return GLib.SOURCE_REMOVE

    def install(self, _button: Gtk.Button) -> None:
        self.action.set_sensitive(False)
        self.status.set_label("Preparing a private Windows environment…")
        threading.Thread(target=self.install_worker, daemon=True).start()

    def install_worker(self) -> None:
        assert self.package is not None
        try:
            manifest = self.engine.install(self.package)
            GLib.idle_add(self.installed, manifest["name"])
        except RelayError as error:
            GLib.idle_add(self.failed, str(error))
        except Exception as error:
            GLib.idle_add(self.failed, f"Relay could not complete the transaction: {error}")

    def installed(self, name: str) -> bool:
        self.status.set_label(f"{name} is now available in Applications.")
        self.action.set_label("Done")
        self.action.set_sensitive(True)
        self.action.disconnect_by_func(self.install)
        self.action.connect("clicked", lambda *_: self.close())
        return GLib.SOURCE_REMOVE

    def failed(self, message: str) -> bool:
        self.status.set_label(message)
        self.status.add_css_class("error")
        self.action.set_sensitive(False)
        return GLib.SOURCE_REMOVE


class InstallerApplication(Adw.Application):
    def __init__(self, path: Path) -> None:
        super().__init__(application_id="org.projectluma.RelayInstaller")
        self.path = path

    def do_activate(self) -> None:
        window = self.props.active_window or InstallerWindow(self, self.path)
        window.present()


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 1:
        print("usage: luma-relay-installer FILE.exe|FILE.msi", file=sys.stderr)
        return 2
    return InstallerApplication(Path(values[0])).run([])


if __name__ == "__main__":
    raise SystemExit(main())
