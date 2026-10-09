# SPDX-License-Identifier: Apache-2.0
"""The six-hourly background app update, run inside Depot.

``luma-depot-app-updates.timer`` starts ``luma-depot --update-apps``. Like
first-boot provisioning, that makes this process Depot's single instance, so
updates go through the window's own update path and show in the Updates tab
if it is open. Updates that ask for more wait for review. Deferred updates and
completed background runs are announced once per build, with a direct Updates action.
Everything done or skipped on its own is written to the journal for Luma
Vitals (depot_errors.activity), and every update lands in "Recently updated".
"""

from __future__ import annotations

from gi.repository import Gio, GLib

from luma_installer import depot_app_history, depot_autoupdate, depot_counting, depot_errors


def pending_for(records) -> list:
    pending = []
    for record in records:
        if not record.managed or not record.has_update:
            continue
        name = record.app.name if record.app else record.app_id
        widens = any(change.change in ("added", "widened") and change.key != "files.portal"
                     for change in record.permission_changes)
        pending.append(depot_autoupdate.Pending(record.app_id, name, record.update_version, widens,
                                                getattr(record, "update_commit", ""), getattr(record, "commit", "")))
    return pending


def start_app_updates(application) -> "AppUpdateRun | None":
    if getattr(application, "app_updates", None) is not None and application.app_updates.active:
        return application.app_updates
    window = application.props.active_window
    if window is None:
        from .window import DepotWindow
        window = DepotWindow(application)
    run = AppUpdateRun(application, window)
    application.app_updates = run
    run.start()
    return run


class AppUpdateRun:
    def __init__(self, application, window) -> None:
        self.application = application
        self.window = window
        self.active = False
        self.queue: list[str] = []
        self.current = ""
        self.pending = {}
        self.completed = []

    def start(self) -> None:
        self.active = True
        self.application.hold()
        installer = self.window.installer
        if hasattr(installer, "force_update_check"):
            installer.force_update_check = True
        installer.installed(self._checked)

    def _checked(self, result) -> None:
        if not result.ok:
            self._finish()
            return
        records = tuple(result.value)
        self.window.installed = {record.app_id: record for record in records}
        pending = pending_for(records)
        self.pending = {item.app_id: item for item in pending}
        if getattr(self.window, 'host_client', None) is not None:
            if not self.window._settings_ready:
                self._finish()
                return
            settings = self.window.settings
        else:
            settings = depot_counting.load_settings()
        monitor = Gio.NetworkMonitor.get_default()
        try:
            power_saver = Gio.PowerProfileMonitor.dup_default().get_power_saver_enabled()
        except (AttributeError, GLib.Error):
            power_saver = False
        from .system_updates import battery_low
        history = depot_app_history.load()
        for app_id in depot_app_history.lift_outdated_pauses(
                history, {item.app_id: (item.release, item.commit) for item in pending}):
            name = next((item.name for item in pending if item.app_id == app_id), app_id)
            depot_errors.activity("resumed", app_id=app_id, name=name, automatic=True,
                                  detail="a newer version than the one taken back is available")
        for item in pending:
            if depot_app_history.holds(history, item.app_id, item.release, item.commit):
                depot_errors.activity("held", app_id=item.app_id, name=item.name, automatic=True,
                                      detail="paused after going back to the previous version", version=item.release)
        state=depot_autoupdate.load()
        if getattr(self.window, 'host_client', None) is not None:
            state['approved']=list(self.window.installer.permission_state['approved'])
        plan = depot_autoupdate.plan(pending, state, enabled=settings.app_updates,
                                     metered=monitor.get_network_metered(), power_saver=power_saver,
                                     low_battery=battery_low(depot_autoupdate.LOW_BATTERY_PERCENT),
                                     history=history)
        if plan.skipped_reason and pending:
            for item in pending:
                depot_errors.activity("skipped", app_id=item.app_id, name=item.name, automatic=True,
                                      detail=plan.skipped_reason, version=item.release)
        if plan.notify:
            if self._notify_held(plan.notify):
                depot_autoupdate.mark_notified(plan.notify)
        if plan.available:
            if self._notify_apps(plan.available, completed=False):
                depot_autoupdate.mark_announced(plan.available, "available")
        self.queue = [app_id for app_id in plan.install if app_id not in self.window.jobs]
        if not self.queue:
            self._finish()
            return
        self.window.install_listeners.append(self)
        self._next()

    def _next(self) -> bool:
        if not self.queue:
            self._finish()
            return GLib.SOURCE_REMOVE
        self.current = self.queue.pop(0)
        job = self.window.jobs.get(self.current)
        if job is not None and not job.failed:
            self.current = ""
            GLib.idle_add(self._next)
            return GLib.SOURCE_REMOVE
        started = self.window._update(self.current, approve=False, automatic=True,
                            expected_commit=self.pending[self.current].commit,
                            expected_installed_commit=self.pending[self.current].installed_commit)
        if started is False:
            self.current = ""
            GLib.idle_add(self._next)
        return GLib.SOURCE_REMOVE

    def install_finished(self, app_id, result, cancelled) -> None:
        """Install results are not ours; updates arrive in update_finished."""

    def update_finished(self, app_id, result, cancelled) -> None:
        if app_id != self.current:
            return
        if result.ok and not cancelled and app_id in self.pending:
            self.completed.append(self.pending[app_id])
        self.current = ""
        GLib.idle_add(self._next)

    def _notify_held(self, held) -> bool:
        if len(held) == 1:
            title = f"The update to {held[0].name} asks for more"
        else:
            title = f"{len(held)} app updates ask for more"
        notification = Gio.Notification.new(title)
        notification.set_body("Look at what changes in Depot before they update. Nothing changes until you do.")
        notification.set_default_action_and_target("app.show-view", GLib.Variant.new_string("updates"))
        try:
            self.application.send_notification("held-app-updates", notification)
        except GLib.Error:
            return False
        return True

    def _notify_apps(self, items, *, completed) -> bool:
        if len(items) == 1:
            title = f"{items[0].name} updated" if completed else f"An update to {items[0].name} is available"
        else:
            title = f"{len(items)} apps updated" if completed else f"{len(items)} app updates are available"
        notification = Gio.Notification.new(title)
        notification.set_body("Running apps keep their current version until you open them again."
                              if completed else "Open Updates in Depot to review and install them.")
        notification.set_default_action_and_target("app.show-view", GLib.Variant.new_string("updates"))
        try:
            self.application.send_notification("completed-app-updates" if completed else "available-app-updates",
                                               notification)
        except GLib.Error:
            return False
        return True

    def _remind_security_firmware(self) -> None:
        """One gentle notification per security firmware release; others wait silently."""
        firmware = getattr(self.window, "firmware", None)
        if firmware is None or not firmware.updates:
            return
        state = depot_autoupdate.load()
        fresh = [update for update in firmware.updates if update.security
                 and f"firmware:{update.device_id}@{update.version}" not in state["notified"]]
        if not fresh:
            return
        title = fresh[0].description.title if len(fresh) == 1 else f"{len(fresh)} security updates for your computer"
        notification = Gio.Notification.new(title)
        notification.set_body("Install it in Depot when this computer is connected to power. "
                              "Nothing is installed without you.")
        notification.set_default_action_and_target("app.show-view", GLib.Variant.new_string("updates"))
        try:
            self.application.send_notification("security-firmware", notification)
        except GLib.Error:
            return
        for update in fresh:
            state["notified"].append(f"firmware:{update.device_id}@{update.version}")
        depot_autoupdate.save(state)

    def _refresh_firmware_blocklist(self) -> None:
        if getattr(self.window, 'host_client', None) is not None:
            self.window.host_client.call('FirmwareBlocklistRefresh', {}, lambda _result: None)
            return
        import threading

        def fetch():
            import urllib.request
            from luma_installer import depot_firmware
            from luma_installer.depot_catalog import public_key
            try:
                with urllib.request.urlopen(depot_firmware.URL, timeout=10) as stream:
                    content = stream.read(depot_firmware.MAX_BYTES + 1)
                with urllib.request.urlopen(depot_firmware.URL + ".minisig", timeout=10) as stream:
                    signature = stream.read(4096)
                depot_firmware.remember(content, signature, public_key())
            except Exception:  # not published yet, offline, or not verified: keep what is known
                return
        threading.Thread(target=fetch, daemon=True).start()

    def _finish(self) -> None:
        completed = depot_autoupdate.unannounced(self.completed, "completed")
        if completed and self._notify_apps(completed, completed=True):
            depot_autoupdate.mark_announced(completed, "completed")
        self._refresh_firmware_blocklist()
        self._remind_security_firmware()
        self.active = False
        if self in self.window.install_listeners:
            self.window.install_listeners.remove(self)
        provisioning = self.window.provisioning
        if not self.window.get_visible() and not (provisioning is not None and provisioning.active):
            self.window.destroy()
        self.application.release()
