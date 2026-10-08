# SPDX-License-Identifier: Apache-2.0
"""Depot's Updates page, laid out like a calm software-update screen.

Top to bottom:

1. **Needs your attention** -- everything waiting on the person, whatever kind:
   a system update to download or restart for, hardware (firmware) updates,
   app updates that failed, ask for more or wait because automatic updates
   are off, apps paused after going back, and errors. Each item carries its
   own action. When nothing waits: "Everything is up to date", when it was
   last checked, and Check Now.
2. **Recently updated** -- 90 days of app updates, automatic or not, with
   what's new and, within 30 days, Go Back.
3. **This computer** -- version, channel, where updates come from, signature.
4. **Automatic updates** -- apps and system downloads; the long explanations
   sit behind disclosures.

The sidebar badge counts exactly the attention items.
"""

from __future__ import annotations

from datetime import datetime
import time

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from luma_installer import depot_app_history, depot_counting, depot_errors

from .providers import Progress, Result

APP_UPDATE_SOURCES_TEXT = ("Apps come from the sources Depot shows on each app's page: Luma's own app "
                           "repository and Flathub. Depot checks every download's signature and keeps apps "
                           "running while they update; an app uses its new version the next time it opens.")


def _when(stamp: int, now: float | None = None) -> str:
    moment = datetime.fromtimestamp(stamp)
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    clock = moment.strftime("%H:%M")
    if moment.date() == today:
        return f"Today, {clock}"
    if (today - moment.date()).days == 1:
        return f"Yesterday, {clock}"
    return moment.strftime("%-d %b %Y, ") + clock


def _label(text: str, style: str, *, wrap: bool = True, selectable: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap, selectable=selectable)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.add_css_class(style)
    return widget


def _button(text: str, callback, *, primary: bool = False, quiet: bool = False, sensitive: bool = True) -> Gtk.Button:
    widget = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
    widget.add_css_class("luma-button")
    widget.add_css_class("small")
    if primary:
        widget.add_css_class("primary")
    if quiet:
        widget.add_css_class("quiet")
    widget.set_sensitive(sensitive)
    widget.connect("clicked", lambda *_: callback())
    return widget


def _card(title: str, summary: str = "") -> tuple[Gtk.Box, Gtk.Box]:
    block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    block.add_css_class("dp-apps")
    head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    head.add_css_class("dp-appshead")
    head.append(_label(title, "dp-apps-title", wrap=False))
    if summary:
        head.append(_label(summary, "dp-apps-summary"))
    block.append(head)
    listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    listing.add_css_class("dp-uplist")
    return block, listing


def _append_rows(listing: Gtk.Box, rows) -> None:
    for index, row in enumerate(rows):
        if index:
            rule = Gtk.Box()
            rule.add_css_class("dp-acc-rule")
            listing.append(rule)
        listing.append(row)


def _disclosure(title: str, text: str) -> Gtk.Expander:
    expander = Gtk.Expander(label=title)
    expander.add_css_class("dp-notes")
    expander.set_child(_label(text, "dp-up-detail", selectable=True))
    return expander


class UpdatesPage:
    """Mixed into DepotWindow; everything the Updates page draws outside preview mode."""

    # ── What needs the person ────────────────────────────────────────────

    def _history(self):
        cached = getattr(self, "_history_cache", None)
        if cached is None or time.monotonic() - cached[0] > 5:
            try:
                history = depot_app_history.load()
            except OSError:
                history = depot_app_history.History()
            self._history_cache = (time.monotonic(), history)
        return self._history_cache[1]

    def _forget_history_cache(self) -> None:
        self._history_cache = None

    def _os_needs_attention(self) -> bool:
        system = self.system
        state = system.state if system is not None else None
        if state is None or not state.service:
            return False
        if state.update_ready or state.downloading or state.barrier_blocked or state.rolled_back_version:
            return True
        if system.error:
            return True
        return state.managed and (state.state == "error" or bool(state.last_error and not state.booted_version))

    def _app_attention(self):
        """(failed, held, manual, running, paused) app ids."""
        history = self._history()
        failed, held, manual, running = [], [], [], []
        for app_id, job in self.jobs.items():
            if job.kind not in ("update", "revert"):
                continue
            (failed if job.failed else running).append(app_id)
        for record in self.installed.values():
            if not record.has_update or record.app_id in self.jobs:
                continue
            if depot_app_history.holds(history, record.app_id, record.update_version,
                                       getattr(record, "update_commit", "")):
                continue
            if self._asks_for_more(record):
                held.append(record.app_id)
            elif not self.settings.app_updates:
                manual.append(record.app_id)
        paused = [app_id for app_id in history.paused if app_id in self.installed]
        return failed, held, manual, running, paused

    def _attention_count(self) -> int:
        failed, held, manual, _running, paused = self._app_attention()
        count = len(failed) + len(held) + len(manual) + len(paused)
        if self._os_needs_attention():
            count += 1
        if self.firmware is not None:
            count += len(self.firmware.attention()) + len(self._orphan_firmware_failures())
        return count

    def _orphan_firmware_failures(self):
        """Failures whose device is no longer offered an update (it may be
        waiting in its update mode): shown on their own, never dropped."""
        firmware = self.firmware
        offered = {update.device_id for update in firmware.updates}
        return [(device, failure) for device, failure in firmware.failures.items() if device not in offered]

    def _render_updates_page(self) -> None:
        failed, held, manual, running, paused = self._app_attention()
        firmware = self.firmware
        attention = []
        if self._os_needs_attention():
            attention.append(self._system_block())
        if firmware is not None and (firmware.updates or firmware.failures):
            attention.append(self._hardware_block())
        if failed or held or manual or running or paused:
            attention.append(self._apps_attention_block(failed, held, manual, running, paused))

        if attention:
            heading = _label("Needs your attention", "dp-attention-title", wrap=False)
            self.content.append(heading)
            for widget in attention:
                self.content.append(widget)
        else:
            self.content.append(self._calm_block())
        if firmware is not None and firmware.problem and not firmware.updates:
            self.content.append(self._hardware_problem_block())

        self.content.append(self._history_block())

        state = self.system.state if self.system is not None else None
        if not self._os_needs_attention() and state is not None and (not state.service or not state.managed):
            # Why this computer follows no channel, and what would change that, sits
            # with the channel choice rather than among things that need a decision.
            self.content.append(self._system_block())
        facts = self._system_facts_block()
        if facts is not None:
            self.content.append(facts)
        channels = self._channel_block()
        if channels is not None:
            self.content.append(channels)
        sources = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=12)
        from luma_installer.depot_firmware import SOURCE_TEXT
        sources.append(_disclosure("Where app updates come from", APP_UPDATE_SOURCES_TEXT))
        sources.append(_disclosure("Where hardware updates come from", SOURCE_TEXT))
        self.content.append(sources)

        self.content.append(self._automatic_updates_block())

    # ── Everything is up to date ─────────────────────────────────────────

    def _calm_block(self) -> Gtk.Widget:
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        block.add_css_class("dp-os")
        block.add_css_class("dp-calm")
        from luma_installer.depot_system_update import channel_name
        state = self.system.state if self.system is not None else None
        block.append(_label("Everything is up to date", "dp-os-name"))
        checking = self.system is not None and (self.system.busy == "Check" or
                                                (state is not None and state.state == "checking"))
        parts = []
        if state is not None and state.service and state.booted_version:
            parts.append(state.booted_name +
                         (f" · {channel_name(state.channel)}" if state.channel else ""))
        checked = state.checked_ago() if state is not None else ""
        parts.append("Checking now…" if checking else f"Last checked {checked}" if checked else "")
        meta = " · ".join(part for part in parts if part)
        if meta:
            block.append(_label(meta, "dp-os-meta"))
        waiting = [record for record in self.installed.values() if record.has_update and record.app_id not in self.jobs]
        if waiting and self.settings.app_updates:
            count = len(waiting)
            block.append(_label(f"{count} app update{'s' if count != 1 else ''} will install on "
                                f"{'its' if count == 1 else 'their'} own.", "dp-get-note"))
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=4)
        actions.append(_button("Check Now", self._check_everything, sensitive=not checking))
        if waiting and self.settings.app_updates:
            actions.append(_button("Update Apps Now", lambda: [self._update(r.app_id, approve=False, expected_commit=r.update_commit, expected_installed_commit=r.commit) for r in waiting
                                                              if not self._asks_for_more(r)], quiet=True))
        if state is not None and state.service and state.managed and state.rollback_available:
            actions.append(_button("Go Back to the Previous Luma Version\u2026", self._confirm_rollback, quiet=True))
        block.append(actions)
        return block

    def _check_everything(self) -> None:
        if self.system is not None:
            self.system.call("Check")
        if self.firmware is not None:
            self.firmware.load()
        installer = self.installer
        if hasattr(installer, "force_update_check"):
            installer.force_update_check = True
        installer.installed(self._installed_loaded)
        self.render()

    # ── Hardware ─────────────────────────────────────────────────────────

    def _hardware_block(self) -> Gtk.Widget:
        firmware = self.firmware
        waiting = firmware.attention()
        count = len(waiting)
        paused = len(firmware.updates) - count
        parts = []
        if count:
            parts.append(f"{count} update{'s' if count != 1 else ''} · installed only when you choose")
        if paused:
            parts.append(f"{paused} paused")
        block, listing = _card("Hardware updates", " · ".join(parts))
        shared = self._firmware_shared_requirements()
        if shared:
            needs = self._firmware_requirements(shared)
            needs.add_css_class("dp-fw-card-needs")
            block.append(needs)
        rows = [self._hardware_row(update) for update in firmware.updates]
        rows += [self._orphan_failure_row(device, failure)
                 for device, failure in self._orphan_firmware_failures()]
        _append_rows(listing, rows)
        if rows:
            block.append(listing)
        if firmware.refresh_error:
            block.append(_label(firmware.refresh_error, "dp-failed"))
        return block

    def _firmware_shared_requirements(self):
        """What the computer or fwupd needs before any update (fresh update
        information, a working firmware service): said once, above the rows."""
        from luma_installer.depot_firmware_safety import GLOBAL
        firmware = self.firmware
        seen = {}
        for update in firmware.updates:
            for requirement in firmware.offer(update).requirements:
                if requirement.key in GLOBAL:
                    seen.setdefault(requirement.key, requirement)
        return list(seen.values())

    def _hardware_problem_block(self) -> Gtk.Widget:
        """A firmware check that did not finish is the hardware card's own, quiet state."""
        firmware = self.firmware
        block, _listing = _card("Hardware updates", firmware.problem)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=8)
        actions.append(_button("Checking\u2026" if firmware.checking else "Try Again", firmware.load,
                               quiet=True, sensitive=not firmware.checking))
        block.append(actions)
        return block

    #: What an install is doing, as the person should hear it.
    _FIRMWARE_PHASES = {
        "prepare": "Preparing the update\u2026",
        "detach": "Switching the device to its update mode\u2026",
        "write": "Writing the update\u2026",
        "attach": "Restarting the device\u2026",
    }

    def _hardware_row(self, update) -> Gtk.Widget:
        from luma_installer.depot_firmware import needs_text
        firmware = self.firmware
        description = update.description
        offer = firmware.offer(update)
        failure = firmware.failures.get(update.device_id)
        installing = firmware.installing == update.device_id
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row.add_css_class("dp-uprow")
        if offer.state in ("paused", "held") and not installing:
            row.add_css_class("dp-fw-paused")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        mark = Gtk.Box(valign=Gtk.Align.START)
        mark.add_css_class("dp-accmark")
        mark.append(Gtk.Image(icon_name="security-high-symbolic" if description.security
                              else "application-x-firmware-symbolic", pixel_size=13))
        line.append(mark)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        name_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_line.append(_label(description.title, "dp-up-name"))
        if description.security:
            chip = Gtk.Label(label="Security", valign=Gtk.Align.CENTER)
            chip.add_css_class("dp-chip")
            chip.add_css_class("notable")
            name_line.append(chip)
        copy.append(name_line)
        copy.append(_label(description.summary, "dp-up-detail"))
        from luma_installer.depot_firmware_safety import GLOBAL
        own = [r for r in offer.requirements if r.key not in GLOBAL]
        charger_asked = any(r.key == "ac" for r in own)
        needs = needs_text(update.requires_ac and not charger_asked, update.needs_reboot)
        if needs and offer.state not in ("paused", "held"):
            copy.append(_label(needs, "dp-get-note"))
        if offer.state in ("paused", "held") and not installing and failure is None:
            copy.append(self._firmware_note(offer.reason, "paused"))
        elif own and not installing and failure is None:
            copy.append(self._firmware_requirements(own))
        if failure is None:
            copy.append(_disclosure("Details", self._firmware_details(update, offer, failure)))
        line.append(copy)
        end = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, valign=Gtk.Align.CENTER)
        if installing:
            end.append(_label("Installing", "dp-up-state", wrap=False))
        elif offer.state in ("paused", "held"):
            chip = Gtk.Label(label="Paused" if offer.state == "paused" else "Waiting", valign=Gtk.Align.CENTER)
            chip.add_css_class("dp-chip")
            end.append(chip)
        elif failure is not None:
            state = _label("Didn\u2019t install" if failure.stage == "before" else "Needs attention",
                           "dp-up-state", wrap=False)
            state.add_css_class("failed")
            end.append(state)
        else:
            ready = offer.state == "ready"
            install = _button("Install\u2026", lambda u=update: self._confirm_firmware(u),
                              primary=description.security and ready,
                              sensitive=not firmware.installing and ready)
            if not ready:
                install.set_tooltip_text(next((r.text for r in offer.requirements if r.blocking), ""))
            end.append(install)
        line.append(end)
        row.append(line)
        if installing:
            track = Gtk.ProgressBar(fraction=firmware.progress)
            track.add_css_class("dp-track")
            row.append(track)
            stage = self._FIRMWARE_PHASES.get(firmware.phase, "")
            row.append(_label((stage + " " if stage else "") +
                              "Keep this computer on power and the device connected until it finishes.",
                              "dp-progress-counts"))
        elif failure is not None:
            row.append(self._firmware_failure(failure, update,
                                              about=self._firmware_details(update, offer, failure)))
        return row

    def _firmware_note(self, text: str, style: str) -> Gtk.Widget:
        note = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=2)
        note.add_css_class("dp-fw-note")
        note.add_css_class(style)
        note.append(Gtk.Image(icon_name="media-playback-pause-symbolic", pixel_size=12, valign=Gtk.Align.START))
        note.append(_label(text, "dp-fw-note-text"))
        return note

    def _firmware_requirements(self, requirements) -> Gtk.Widget:
        """Every unmet requirement, as a line the person can act on, before Install."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        box.add_css_class("dp-fw-needs")
        for requirement in requirements:
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            item.add_css_class("dp-fw-need")
            item.add_css_class("blocking" if requirement.blocking else "info")
            item.append(Gtk.Image(icon_name="dialog-warning-symbolic" if requirement.blocking
                                  else "dialog-information-symbolic", pixel_size=12, valign=Gtk.Align.START))
            text = _label(requirement.text, "dp-fw-need-text")
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            item.append(text)
            if requirement.action == "refresh":
                firmware = self.firmware
                refresh = _button("Refreshing\u2026" if firmware.refreshing else requirement.action_label,
                                  firmware.refresh, quiet=True, sensitive=not firmware.refreshing)
                item.append(refresh)
            box.append(item)
        return box

    def _firmware_failure(self, failure, update=None, about: str = "") -> Gtk.Widget:
        """What went wrong, whether anything changed, and what to do: Try Again,
        Restart when that is the way on, and Details with fwupd's own words."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("dp-fw-failure")
        box.add_css_class("urgent" if failure.urgent else failure.stage)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        head.append(Gtk.Image(icon_name="dialog-error-symbolic" if failure.urgent else "dialog-warning-symbolic",
                              pixel_size=13, valign=Gtk.Align.START))
        head.append(_label(failure.title, "dp-fw-failure-title"))
        box.append(head)
        box.append(_label(failure.body, "dp-fw-failure-body"))
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=6)
        firmware = self.firmware
        offer = firmware.offer(update) if update is not None else None
        if update is not None and failure.retry and offer is not None and offer.state == "ready":
            actions.append(_button("Try Again", lambda u=update: firmware.install(u), primary=failure.urgent,
                                   sensitive=not firmware.installing))
        elif update is None:
            actions.append(_button("Check Again", firmware.load, primary=failure.urgent,
                                   sensitive=not firmware.checking))
        if failure.action == "restart":
            actions.append(_button("Restart\u2026", self._confirm_system_restart, quiet=not failure.urgent))
        box.append(actions)
        if offer is not None and offer.state in ("held", "paused"):
            box.append(_label(offer.reason, "dp-fw-failure-body"))
        detail = "\n\n".join(part for part in (
            f"What fwupd reported:\n{failure.detail}" if failure.detail else "", about) if part)
        if detail:
            box.append(_disclosure("Details", detail))
        return box

    def _orphan_failure_row(self, device_id: str, failure) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row.add_css_class("dp-uprow")
        row.append(self._firmware_failure(failure))
        return row

    def _firmware_details(self, update, offer, failure) -> str:
        details = [f"Device: {update.device}"]
        if update.vendor:
            details.append(f"Made by: {update.vendor}")
        if update.current_version or update.version:
            details.append(f"Version: {update.current_version or 'unknown'} → {update.version or 'newer'}")
        if update.plugin:
            details.append(f"Updated by: fwupd {self.firmware.fwupd_build or ''} ({update.plugin})".replace("  ", " "))
        if offer.requirements:
            details.append("")
            details.extend(f"{'Needed' if r.blocking else 'Note'}: {r.text}" for r in offer.requirements)
        if update.notes:
            details.append("")
            details.append(update.notes)
        if update.details_url:
            details.append("")
            details.append(update.details_url)
        return "\n".join(details)

    # ── Apps that need the person ────────────────────────────────────────

    def _apps_attention_block(self, failed, held, manual, running, paused) -> Gtk.Widget:
        rows = []
        parts = []
        if running:
            parts.append(f"{len(running)} updating")
        if failed:
            parts.append(f"{len(failed)} did not finish")
        if held:
            parts.append(f"{len(held)} ask{'s' if len(held) == 1 else ''} for more")
        if manual:
            parts.append(f"{len(manual)} waiting for you")
        if paused:
            parts.append(f"{len(paused)} paused")
        block, listing = _card("Apps", " · ".join(parts))
        for app_id in running + failed:
            record = self.installed.get(app_id)
            job = self.jobs.get(app_id)
            if record is not None and job is not None and job.kind == "update":
                rows.append(self._update_row(record))
            elif job is not None:
                rows.append(self._revert_job_row(app_id, job))
        for app_id in held + manual:
            rows.append(self._update_row(self.installed[app_id]))
        history = self._history()
        for app_id in paused:
            rows.append(self._paused_row(history.paused[app_id]))
        _append_rows(listing, rows)
        if rows:
            block.append(listing)
        ready = [self.installed[a] for a in manual]
        if len(ready) > 1:
            block.append(_button("Update All", lambda: [self._update(r.app_id, approve=False, expected_commit=r.update_commit, expected_installed_commit=r.commit) for r in ready], quiet=True))
        return block

    def _paused_row(self, pause) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("dp-uprow")
        record = self.installed.get(pause.app_id)
        app = record.app if record is not None else None
        if app is not None:
            row.append(self._icon_tile(app, 40))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        copy.append(_label(f"{pause.name}: automatic updates paused", "dp-up-name"))
        version = f" {pause.reverted_from_version}" if pause.reverted_from_version else ""
        copy.append(_label(f"You went back to the previous version. {pause.name} updates again on its own "
                           f"when a version newer than{version or ' the one you left'} is available.",
                           "dp-up-detail"))
        row.append(copy)
        row.append(_button("Resume", lambda: self._resume_updates(pause.app_id, pause.name)))
        return row

    def _revert_job_row(self, app_id: str, job) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row.add_css_class("dp-uprow")
        record = self.installed.get(app_id)
        name = record.app.name if record is not None and record.app else app_id
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        copy.append(_label(name, "dp-up-name"))
        copy.append(_label("Going back to the previous version" if not job.failed else job.failed,
                           "dp-up-detail" if not job.failed else "dp-failed"))
        line.append(copy)
        if job.failed:
            line.append(_button("OK", lambda: (self.jobs.pop(app_id, None), self.render())))
        row.append(line)
        if job.progress is not None and not job.failed:
            track = Gtk.ProgressBar(fraction=job.progress.fraction)
            track.add_css_class("dp-track")
            row.append(track)
        if job.failed and job.detail:
            row.append(self._failure_details(job.detail))
        return row

    def _resume_updates(self, app_id: str, name: str) -> None:
        try:
            depot_app_history.resume(app_id)
        except OSError:
            self._report("Could not resume updates", "Depot could not write its update history.")
            return
        depot_errors.activity("resumed", app_id=app_id, name=name, automatic=False)
        self._forget_history_cache()
        self.render()

    # ── Recently updated ─────────────────────────────────────────────────

    def _history_block(self) -> Gtk.Widget:
        history = self._history()
        entries = depot_app_history.recent(history)
        block, listing = _card("Recently updated",
                               "" if entries else "No app updates in the last 90 days.")
        if not entries:
            return block
        rows = [self._history_row(entry, history) for entry in entries[:8]]
        _append_rows(listing, rows)
        block.append(listing)
        if len(entries) > 8:
            more = Gtk.Expander(label=f"Show {len(entries) - 8} more")
            more.add_css_class("dp-notes")
            rest = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            rest.add_css_class("dp-uplist")
            _append_rows(rest, [self._history_row(entry, history) for entry in entries[8:]])
            more.set_child(rest)
            block.append(more)
        return block

    def _history_row(self, entry, history) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.add_css_class("dp-uprow")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        record = self.installed.get(entry.app_id)
        app = record.app if record is not None else (self.catalogue.find(entry.app_id) if self.catalogue else None)
        if app is not None:
            line.append(self._icon_tile(app, 32))
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        copy.append(_label(entry.name, "dp-up-name"))
        change = " → ".join(part for part in (entry.from_version, entry.to_version) if part)
        verb = "Went back" if entry.kind == "revert" else ("Updated automatically" if entry.automatic else "Updated")
        copy.append(_label(" · ".join(part for part in (verb, change, _when(entry.at)) if part), "dp-up-detail"))
        if entry.notes:
            copy.append(_disclosure("What’s new", entry.notes))
        line.append(copy)
        target = None
        if record is not None and record.managed and entry.kind == "update" and hasattr(self.installer, "revert"):
            target = depot_app_history.revertable(history, entry.app_id, record.commit)
        if target is entry and entry.app_id not in self.jobs:
            line.append(_button("Go Back…", lambda: self._confirm_revert(entry), quiet=True))
        row.append(line)
        return row

    def _confirm_revert(self, entry) -> None:
        version = f" {entry.from_version}" if entry.from_version else ""
        dialog = Adw.AlertDialog(
            heading=f"Go back to {entry.name}{version}?",
            body=(f"Depot installs the version you had before {_when(entry.at).lower()}. Automatic updates "
                  f"for {entry.name} pause until a version newer than {entry.to_version or 'this one'} is "
                  f"available, and you can resume them any time. If {entry.name} is open, it keeps "
                  f"running until you close it."))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("revert", "Go Back")
        dialog.set_response_appearance("revert", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, name: self._revert(entry) if name == "revert" else None)
        dialog.present(self)

    def _revert(self, entry) -> None:
        from .window import Job
        if entry.app_id in self.jobs and not self.jobs[entry.app_id].failed: return
        cancellable = Gio.Cancellable()
        owned = self.jobs[entry.app_id] = Job(app_id=entry.app_id, kind="revert", cancellable=cancellable,
                                      progress=Progress(entry.app_id, 0.0, 0, 0, "Going back"))
        self.render()

        def done(result: Result) -> None:
            if not self._current_transaction(owned,result): return
            job = self.jobs.get(entry.app_id)
            if result.ok:
                self.jobs.pop(entry.app_id, None)
                reverted = depot_app_history.Entry(
                    app_id=entry.app_id, name=entry.name, from_version=entry.to_version,
                    to_version=entry.from_version, at=int(time.time()), automatic=False,
                    from_commit=entry.to_commit, to_commit=entry.from_commit, kind="revert")
                try:
                    history = depot_app_history.record(reverted)
                    depot_app_history.pause(history, entry)
                except OSError:
                    self._report("Went back, but could not pause updates",
                                 "Depot could not write its update history, so it may update the app again.")
                depot_errors.activity("reverted", app_id=entry.app_id, name=entry.name, automatic=False,
                                      detail=f"{entry.to_version} to {entry.from_version}")
                depot_errors.activity("paused", app_id=entry.app_id, name=entry.name, automatic=False,
                                      version=entry.to_version)
                self._forget_history_cache()
                self.installer.installed(self._installed_loaded)
            elif job is not None:
                job.failed = result.error.hint or str(result.error)
                job.detail = getattr(result.error, "detail", "")
                job.progress = None
            self.render()
        self.installer.revert(entry.app_id, entry.from_commit, lambda p: self._progress_for_job(owned,p), done, cancellable)

    # ── History bookkeeping for every update ─────────────────────────────

    def _remember_before_update(self, app_id: str, automatic: bool) -> None:
        record = self.installed.get(app_id)
        if record is None:
            return
        pending = getattr(self, "_history_pending", None)
        if pending is None:
            pending = self._history_pending = {}
        pending[app_id] = (record, automatic)

    def _record_finished_updates(self) -> None:
        pending = getattr(self, "_history_pending", None) or {}
        for app_id in [a for a in pending if a not in self.jobs]:
            before, automatic = pending.pop(app_id)
            after = self.installed.get(app_id)
            if after is None:
                continue
            to_commit = getattr(after, "commit", "") or before.update_commit
            to_version = after.version or before.update_version
            if to_commit == before.commit and to_version == before.version:
                continue  # nothing changed (already current)
            app = after.app or before.app
            notes = ""
            if app is not None:
                release = next((r for r in getattr(app, "releases", ()) if r.version == to_version), None)
                notes = (release.description or "") if release is not None else ""
            name = app.name if app is not None else app_id
            try:
                depot_app_history.record(depot_app_history.Entry(
                    app_id=app_id, name=name, from_version=before.version, to_version=to_version,
                    at=int(time.time()), automatic=automatic, from_commit=before.commit,
                    to_commit=to_commit, notes=notes))
            except OSError:
                pass
            depot_errors.activity("updated", app_id=app_id, name=name, automatic=automatic,
                                  detail=f"{before.version or 'previous'} to {to_version or 'newest'}")
            self._forget_history_cache()

    # ── Automatic updates ────────────────────────────────────────────────

    def _automatic_updates_block(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        group = Adw.PreferencesGroup(title="Automatic updates", margin_top=24)
        apps = Adw.SwitchRow(title="Update apps automatically",
                             subtitle="In the background, when it will not cost you data or battery.",
                             active=self.settings.app_updates)

        def changed(row, _pspec):
            self.settings = depot_counting.Settings(self.settings.install_events, self.settings.countme,
                                                    row.get_active())
            try:
                self._save_settings()
            except OSError:
                self._report("Settings were not saved", "Depot could not write its settings file.")
            self.render()
        apps.connect("notify::active", changed)
        group.add(apps)
        box.append(group)
        box.append(_disclosure(
            "How automatic app updates work",
            "Depot checks every six hours and updates the apps it installed, but not on a metered "
            "connection, with power saving on, or on a low battery. Open apps keep running; they use "
            "the new version the next time they open. An update that asks for more waits for you. "
            "Every update is listed under Recently updated, and for 30 days you can go back to the "
            "version before it. Hardware updates are never installed automatically."))
        system = self._download_preference_block()
        if system is not None:
            box.append(system)
        return box
