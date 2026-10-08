from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from luma_appkit import AppWindow, CommandRegistry, Island

from .apk import (
    ApkInspection,
    architecture_notice,
    inspect_android_package,
    processor_summary,
    stage_android_package,
    staged_package_root,
)
from .config import load_runtime_config
from .engine import WaydroidEngine
from .errors import LumaAndroidError
from .receipts import write_install_receipt


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} bytes"


class InstallerWindow(AppWindow):
    def __init__(self, application: Adw.Application, path: Path) -> None:
        super().__init__(application=application, app_id="org.projectluma.AndroidInstaller", title="Install Android Application", icon_name="org.projectluma.AndroidInstaller", commands=CommandRegistry(()), default_width=520, default_height=520, minimum_width=320, minimum_height=360)
        self.path = path
        self.config = load_runtime_config()
        self.inspection: ApkInspection | None = None

        toolbar = Adw.ToolbarView(vexpand=True)
        clamp = Adw.Clamp(maximum_size=560, tightening_threshold=420)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(18)
        content.set_margin_end(18)
        clamp.set_child(content)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(clamp)
        toolbar.set_content(scroll)
        island = Island()
        island.append(toolbar)
        self.set_body(island)

        icon = Gtk.Image.new_from_icon_name("application-x-apk-symbolic")
        icon.set_pixel_size(64)
        content.append(icon)
        title = Gtk.Label(label=path.name)
        title.add_css_class("title-2")
        title.set_wrap(True)
        content.append(title)
        self.architecture_warning = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.architecture_warning.add_css_class("warning")
        content.append(self.architecture_warning)
        description = Gtk.Label(
            label=(
                "Android applications run in Android's per-app sandbox. "
                "This application will not receive access to your Luma files, "
                "contacts, camera, microphone, or location during installation. "
                "Prefer a universal APK or complete split bundle when available; "
                "Luma never silently translates an incompatible processor build."
            )
        )
        description.set_wrap(True)
        description.set_justify(Gtk.Justification.CENTER)
        description.add_css_class("dim-label")
        content.append(description)

        self.group = Adw.PreferencesGroup()
        content.append(self.group)
        self.status = Gtk.Label(label="Inspecting package…")
        self.status.set_wrap(True)
        content.append(self.status)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.install_button = Gtk.Button(label="Install")
        self.install_button.add_css_class("suggested-action")
        self.install_button.set_sensitive(False)
        self.install_button.connect("clicked", self.install)
        buttons.append(cancel)
        buttons.append(self.install_button)
        content.append(buttons)
        threading.Thread(target=self.inspect, daemon=True).start()

    def add_row(self, title: str, subtitle: str) -> None:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        self.group.add(row)

    def inspect(self) -> None:
        try:
            inspection = inspect_android_package(self.path, self.config.apk_max_bytes)
            GLib.idle_add(self.inspection_ready, inspection)
        except LumaAndroidError as error:
            GLib.idle_add(self.failed, str(error))

    def inspection_ready(self, inspection: ApkInspection) -> bool:
        self.inspection = inspection
        notice = architecture_notice(inspection.native_abis)
        self.architecture_warning.set_label(notice)
        self.architecture_warning.set_visible(bool(notice))
        self.add_row("Source", inspection.source_origin)
        self.add_row("Size", human_size(inspection.byte_size))
        package_kind = "APK" if inspection.package_format == "apk" else (
            f"{inspection.package_format.upper()} · {len(inspection.apk_entries)} APK parts"
        )
        self.add_row("Package type", package_kind)
        self.add_row("Processor support", processor_summary(inspection.native_abis))
        self.add_row("Package fingerprint", inspection.sha256[:20] + "…")
        signature = (
            "Signature metadata present; Android will verify it"
            if inspection.has_v1_signature_files
            else "Android will verify the package signature"
        )
        self.add_row("Identity", signature)
        self.status.set_label("Ready to install")
        self.install_button.set_sensitive(True)
        return False

    def install(self, _button: Gtk.Button) -> None:
        self.install_button.set_sensitive(False)
        self.status.set_label("Preparing Android…")
        threading.Thread(target=self.install_worker, daemon=True).start()

    def install_worker(self) -> None:
        assert self.inspection is not None
        staged_payloads: list[Path] = []
        try:
            staged_payloads = stage_android_package(self.path, self.inspection)
            engine = WaydroidEngine(self.config.engine)
            engine.ensure_ready(self.config.multi_window, timeout=180, detached=True)
            engine.install_package(staged_payloads, self.inspection.apk_entries)
            write_install_receipt(self.inspection, "installed")
            GLib.idle_add(self.installed)
        except LumaAndroidError as error:
            GLib.idle_add(self.failed, str(error))
        except Exception as error:
            # A worker must always resolve the visible transaction. Unexpected
            # host/runtime failures are recorded without exposing traceback
            # internals in the confirmation surface; leaving an endless
            # progress state would falsely imply that Android is still busy.
            GLib.idle_add(
                self.failed,
                f"Installation could not be prepared: {error}",
            )
        finally:
            if staged_payloads:
                shutil.rmtree(staged_package_root(staged_payloads), ignore_errors=True)

    def installed(self) -> bool:
        self.status.set_label("Installed. It is now available in Applications.")
        self.install_button.set_label("Done")
        self.install_button.set_sensitive(True)
        self.install_button.disconnect_by_func(self.install)
        self.install_button.connect("clicked", lambda *_: self.close())
        return False

    def failed(self, message: str) -> bool:
        self.status.set_label(message)
        self.status.add_css_class("error")
        self.install_button.set_sensitive(False)
        return False


class InstallerApplication(Adw.Application):
    def __init__(self, path: Path) -> None:
        super().__init__(application_id="org.projectluma.AndroidInstaller")
        self.path = path

    def do_activate(self) -> None:
        window = self.props.active_window or InstallerWindow(self, self.path)
        window.present()


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 1:
        print("usage: luma-android-installer FILE.apk|FILE.apks|FILE.xapk|FILE.apkm", file=sys.stderr)
        return 2
    return InstallerApplication(Path(values[0])).run([])


if __name__ == "__main__":
    raise SystemExit(main())
