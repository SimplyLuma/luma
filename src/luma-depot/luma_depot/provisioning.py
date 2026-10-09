# SPDX-License-Identifier: Apache-2.0
"""Installing the apps chosen during installation, inside Depot.

``luma-depot-provision.service`` runs ``luma-depot --provision`` once the
graphical session has started. That makes this process Depot's single
instance (or forwards to the one already open), so every install goes through
the window's own install path: the same source checks, the same progress, and
the same jobs a person sees if they open Depot while it is working. The window
is not shown unless they do. A notification says when it is finished.

Nothing here blocks the session. Offline, it waits and tries again with a
growing delay; logged out halfway, it resumes next time from its state file.
"""

from __future__ import annotations

from gi.repository import Gio, GLib

from luma_installer import depot_provision
from luma_installer.depot_catalog import CatalogError, local_catalog


def start_provisioning(application) -> "ProvisionRun | None":
    if application.provisioning is not None and application.provisioning.active:
        return application.provisioning
    if depot_provision.already_done():
        return None
    try:
        plan = depot_provision.read_plan()
    except depot_provision.PlanError:
        return None
    if not plan.collections and not plan.applications:
        depot_provision.done_marker().parent.mkdir(parents=True, exist_ok=True)
        depot_provision.done_marker().write_text(plan.digest + "\n", encoding="utf-8")
        return None
    window = application.props.active_window
    if window is None:
        from .window import DepotWindow
        window = DepotWindow(application)
    run = ProvisionRun(application, window, plan)
    application.provisioning = run
    window.provisioning = run
    run.start()
    return run


class ProvisionRun:
    def __init__(self, application, window, plan) -> None:
        self.application = application
        self.window = window
        self.plan = plan
        self.provisioner = None
        self.active = False
        self.visible = False
        self.current = ""
        self.timer = 0
        self.summary = {}
        self.monitor = Gio.NetworkMonitor.get_default()
        self.monitor_handler = 0
        self.waiting_for_catalogue = False

    # ── Life cycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        self.active = True
        self.visible = True
        self.application.hold()
        self.window.install_listeners.append(self)
        self.monitor_handler = self.monitor.connect("network-changed", self._network_changed)
        if self.window.catalogue is None:
            self.waiting_for_catalogue = True
        else:
            self._begin()

    def catalogue_loaded(self, result) -> None:
        if self.waiting_for_catalogue and self.active:
            self.waiting_for_catalogue = False
            self._begin()

    def _begin(self) -> None:
        try:
            catalog = local_catalog()
        except CatalogError:
            self._finish()
            return
        installed = self.window.installed
        self.provisioner = depot_provision.Provisioner(
            self.plan, catalog,
            is_installed=lambda app_id: ("catalog:" + app_id) in installed)
        self.provisioner.save()
        self.window.render()
        self._step()

    def _network_changed(self, _monitor, available) -> None:
        if available and self.timer and self.active:
            GLib.source_remove(self.timer)
            self.timer = 0
            # A network that just arrived is worth one early attempt; the
            # backoff still governs whatever fails after it.
            for app_id in self.provisioner.state["order"]:
                record = self.provisioner.record(app_id)
                if record["state"] == depot_provision.PENDING:
                    record["next_attempt"] = 0
            self._step()

    def _step(self) -> bool:
        self.timer = 0
        if not self.active or self.provisioner is None:
            return GLib.SOURCE_REMOVE
        action, value = self.provisioner.next_action()
        if action == "install":
            catalogue = self.window.catalogue
            app = catalogue.find("catalog:" + value) if catalogue else None
            if app is None or not app.installable:
                reason = (app.availability if app is not None else "") or "Not available on this computer."
                self.provisioner.failed(value, reason, permanent=True)
                return self._step()
            if app.app_id in self.window.installed:
                self.provisioner.succeeded(value)
                return self._step()
            self.current = value
            self.provisioner.started(value)
            self.window._install(app)
            self.window.render()
        elif action == "wait":
            self.current = ""
            self.timer = GLib.timeout_add_seconds(value, self._step)
            self.window.render()
        else:
            self._finish()
        return GLib.SOURCE_REMOVE

    def install_finished(self, app_id: str, result, cancelled: bool) -> None:
        if not self.active or self.provisioner is None or not app_id.startswith("catalog:"):
            return
        identifier = app_id.split(":", 1)[1]
        if identifier not in self.provisioner.state["apps"] or identifier != self.current:
            return
        self.current = ""
        if result.ok:
            self.provisioner.succeeded(identifier)
        elif cancelled:
            # The person stopped this one; that is an answer, not a failure to retry.
            self.provisioner.failed(identifier, "Cancelled", permanent=True)
        else:
            self.provisioner.failed(identifier, result.error.hint or str(result.error))
        GLib.idle_add(self._step)

    def _finish(self) -> None:
        if self.provisioner is not None:
            self.provisioner.finish()
            self.summary = self.provisioner.summary()
        self.active = False
        if self.monitor_handler:
            self.monitor.disconnect(self.monitor_handler)
            self.monitor_handler = 0
        if self in self.window.install_listeners:
            self.window.install_listeners.remove(self)
        self._notify()
        self.window.render()
        updates = getattr(self.application, "app_updates", None)
        if not self.window.get_visible() and not (updates is not None and updates.active):
            self.window.destroy()
        self.application.release()

    def dismiss(self) -> None:
        self.visible = False

    # ── Words ────────────────────────────────────────────────────────────

    def _names(self, identifiers) -> list[str]:
        catalogue = self.window.catalogue
        names = []
        for identifier in identifiers:
            app = catalogue.find("catalog:" + identifier) if catalogue else None
            if app is not None:
                names.append(app.name)
        return names

    def _counts(self):
        if self.provisioner is None:
            return 0, 0
        order = self.provisioner.state.get("order", [])
        done = sum(1 for app_id in order
                   if self.provisioner.record(app_id)["state"] == depot_provision.INSTALLED)
        return done, len(order)

    def headline(self) -> str:
        done, total = self._counts()
        if self.active:
            if not total:
                return "Getting the apps you chose ready"
            return f"Installing the apps you chose · {min(done + 1, total)} of {total}"
        failed = self.summary.get(depot_provision.FAILED, []) + [
            item for item in self.summary.get(depot_provision.UNAVAILABLE, [])
            if not item.startswith("collection:")]
        if failed:
            return f"{done} of {total} apps you chose are installed"
        return "The apps you chose are installed"

    def detail(self) -> str:
        if self.active:
            if self.current:
                names = self._names([self.current])
                return f"Now installing {names[0]}. You can keep using this computer." if names else \
                    "You can keep using this computer."
            if self.timer:
                return "Waiting for a connection to try again. Nothing needs doing."
            return "You can keep using this computer."
        failed = self._names(self.summary.get(depot_provision.FAILED, []))
        if failed:
            return "Depot will try " + ", ".join(failed) + " again next time you sign in."
        return "Find them in your apps."

    def _notify(self) -> None:
        notification = Gio.Notification.new(self.headline())
        notification.set_body(self.detail())
        notification.set_default_action_and_target("app.show-view", GLib.Variant.new_string("mine"))
        try:
            self.application.send_notification("first-boot-apps", notification)
        except GLib.Error:
            pass
