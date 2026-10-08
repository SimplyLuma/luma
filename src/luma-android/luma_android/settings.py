from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .config import device_class, load_device_profile, load_runtime_config
from .engine import WaydroidEngine
from .errors import LumaAndroidError


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="Android Applications")
        self.set_default_size(680, 620)
        self.set_size_request(320, 420)
        config = load_runtime_config()
        self.engine = WaydroidEngine(config.engine)

        page = Adw.PreferencesPage(title="Android Applications", icon_name="phone-symbolic")
        self.add(page)
        runtime = Adw.PreferencesGroup(
            title="Runtime",
            description="Android applications share one isolated Android system.",
        )
        page.add(runtime)
        self.state = Adw.ActionRow(title="Status", subtitle="Checking…")
        runtime.add(self.state)
        self.safety = Adw.ActionRow(title="Hardware safety", visible=False)
        runtime.add(self.safety)
        profile = Adw.ActionRow(title="Resource profile", subtitle=device_class().capitalize())
        runtime.add(profile)
        lifecycle = load_device_profile()
        runtime.add(
            Adw.ActionRow(
                title="Startup",
                subtitle=(
                    "On demand — starts when an Android app opens"
                    if not lifecycle.keep_warm
                    else "Ready after sign-in when Android apps are installed"
                ),
            )
        )
        pause = Adw.ActionRow(
            title="Pause Android applications",
            subtitle="Stops Android apps and notifications until resumed.",
        )
        pause_button = Gtk.Button(label="Pause", valign=Gtk.Align.CENTER)
        pause_button.connect("clicked", lambda *_: self.run_action("pause"))
        pause.add_suffix(pause_button)
        runtime.add(pause)
        resume = Adw.ActionRow(
            title="Resume Android applications",
            subtitle="Warms the Android session without opening the full Android home screen.",
        )
        resume_button = Gtk.Button(label="Resume", valign=Gtk.Align.CENTER)
        resume_button.add_css_class("suggested-action")
        resume_button.connect("clicked", lambda *_: self.run_action("resume"))
        resume.add_suffix(resume_button)
        runtime.add(resume)
        restart = Adw.ActionRow(
            title="Restart Android runtime",
            subtitle="Repairs a stopped session without erasing applications or data.",
        )
        restart_button = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER)
        restart_button.connect("clicked", lambda *_: self.run_action("restart"))
        restart.add_suffix(restart_button)
        runtime.add(restart)

        self.applications = Adw.PreferencesGroup(
            title="Installed Android applications",
            description="Permissions remain separate for each application.",
        )
        page.add(self.applications)
        self.application_rows: list[Adw.ActionRow] = []

        privacy = Adw.PreferencesGroup(
            title="Privacy",
            description="Access remains controlled inside Android per application.",
        )
        page.add(privacy)
        for title, subtitle in (
            ("Files", "No broad access to your Luma home folder"),
            ("Contacts and calendars", "Not shared by default"),
            ("Camera, microphone, and location", "Requested per application"),
            ("Notifications", "Delivered while the Android session is ready"),
        ):
            privacy.add(Adw.ActionRow(title=title, subtitle=subtitle))

        about = Adw.PreferencesGroup(title="Technology and credit")
        page.add(about)
        about.add(
            Adw.ActionRow(
                title="Powered by Waydroid",
                subtitle="Android container engine by the Waydroid contributors · GPL-3.0",
            )
        )
        threading.Thread(target=self.refresh, daemon=True).start()

    def refresh(self) -> None:
        try:
            status = self.engine.status()
            state = str(status.get("session", status.get("container", "Stopped"))).capitalize()
            applications = self.engine.applications()
            if "fp6_kernel_admitted" in status:
                admitted = bool(status["fp6_kernel_admitted"])
                gate = str(status.get("fp6_gpu_gate", ""))
                if admitted:
                    safety = "Verified FP6 kernel · GPU watchdog active while Android runs"
                elif gate == "gpu-fault-latched":
                    safety = "Android stopped after a GPU fault · reboot required"
                else:
                    safety = "Android is blocked on this unverified FP6 kernel"
            else:
                safety = ""
        except LumaAndroidError as error:
            state = str(error)
            applications = []
            safety = ""
        GLib.idle_add(self.state.set_subtitle, state)
        GLib.idle_add(self.safety.set_visible, bool(safety))
        if safety:
            GLib.idle_add(self.safety.set_subtitle, safety)
        GLib.idle_add(self.show_applications, applications)

    def show_applications(self, applications: list) -> bool:
        for row in self.application_rows:
            self.applications.remove(row)
        self.application_rows.clear()
        if not applications:
            row = Adw.ActionRow(
                title="No Android applications installed",
                subtitle="Open an APK, APKS, XAPK, or APKM file, or drop it into Applications in Filer.",
            )
            self.applications.add(row)
            self.application_rows.append(row)
            return False
        for application in applications:
            row = Adw.ActionRow(title=application.name, subtitle=application.package)
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            actions.set_valign(Gtk.Align.CENTER)
            permissions = Gtk.Button(
                icon_name="preferences-system-privacy-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Permissions",
            )
            permissions.update_property(
                [Gtk.AccessibleProperty.LABEL], ["Permissions"]
            )
            permissions.connect(
                "clicked", lambda _button, package=application.package: self.run_permissions(package)
            )
            remove = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Remove application",
            )
            remove.add_css_class("destructive-action")
            remove.update_property(
                [Gtk.AccessibleProperty.LABEL], ["Remove application"]
            )
            remove.connect(
                "clicked",
                lambda _button, app=application: self.confirm_remove(app.name, app.package),
            )
            actions.append(permissions)
            actions.append(remove)
            row.add_suffix(actions)
            self.applications.add(row)
            self.application_rows.append(row)
        return False

    def confirm_remove(self, name: str, package: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"Remove {name}?",
            body=(
                "The Android application and its private Android data will be removed. "
                "Your Luma files are not affected."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: self.run_remove(package)
            if response == "remove"
            else None,
        )
        dialog.present()

    def run_remove(self, package: str) -> None:
        self.state.set_subtitle("Removing application…")

        def worker() -> None:
            try:
                self.engine.remove(package)
                self.refresh()
            except LumaAndroidError as error:
                GLib.idle_add(self.state.set_subtitle, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def run_action(self, action: str) -> None:
        def worker() -> None:
            try:
                if action == "pause":
                    self.engine.stop_session()
                elif action == "restart":
                    self.engine.restart_session()
                else:
                    self.engine.ensure_ready(detached=True)
                self.refresh()
            except LumaAndroidError as error:
                GLib.idle_add(self.state.set_subtitle, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def run_permissions(self, package: str) -> None:
        def worker() -> None:
            try:
                self.engine.open_permissions(package)
            except LumaAndroidError as error:
                GLib.idle_add(self.state.set_subtitle, str(error))

        threading.Thread(target=worker, daemon=True).start()


class SettingsApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.projectluma.AndroidSettings")

    def do_activate(self) -> None:
        window = self.props.active_window or SettingsWindow(self)
        window.present()


def main() -> int:
    return SettingsApplication().run([])


if __name__ == "__main__":
    raise SystemExit(main())
