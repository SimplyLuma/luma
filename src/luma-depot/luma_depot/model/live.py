# SPDX-License-Identifier: Apache-2.0
"""The person's real Luma as a `World`: the catalogue, what is installed here, waiting updates and Luma's
own update from the update agent (org.projectluma.Update1). Installs, removals and updates go through the
installation provider Depot always used (host client in the Flatpak, native otherwise).

Until Luma Connect hands Depot the person's other devices, the world holds one device: this one, named
as the system names itself. Looks and Tweaks come from the catalogue once it publishes them; until then
those pages say "Nothing here yet." rather than showing invented items.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from ..providers import App, Catalogue, InstalledApp, Result
from .store import Done, Store
from .world import FORMS, CategoryInfo, Device, Done as DoneRow, Item, PendingUpdate, SystemUpdate, World

__all__ = ["LiveStore", "device_name", "world_from"]

#: The five categories of the redesign, with their lines, glyphs, hues and photos (5.1).
CATEGORIES = (
    CategoryInfo("create", "Create", "Make pictures, films and music", "palette", 25, "luma-creatives-painter"),
    CategoryInfo("work", "Work", "Write, plan, build and talk", "briefcase", 250, "life-sync-terrace"),
    CategoryInfo("media", "Media", "Watch, listen and look", "clapperboard", 330, "luma-hero-move"),
    CategoryInfo("play", "Play", "Games and play", "gamepad-2", 140, "luma-gamers-evening"),
    CategoryInfo("tools", "Tools", "Keep your devices running well", "wrench", 200, "life-footer"),
)
_KEYS = {c.key for c in CATEGORIES}


def device_name() -> str:
    """The name this computer goes by (its pretty hostname), as Depot's device rows say it."""
    try:
        for line in Path("/etc/machine-info").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_HOSTNAME="):
                name = line.split("=", 1)[1].strip().strip('"')
                if name:
                    return name
    except OSError:
        pass
    host = GLib.get_host_name() or ""
    return host.split(".")[0] if host and host != "localhost" else "This computer"


def _form() -> str:
    try:
        from luma_appkit import lumaui
    except ImportError:  # a package check without the kit
        return "desktop"
    return "phone" if lumaui.mobile_form_factor() else "desktop"


def _size(n: int) -> str:
    if n <= 0:
        return ""
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def _package_kind(app: App) -> str:
    if app.system_state:
        return "system"
    if app.tier == "luma":
        return "luma"
    kind = (app.channel_kind or app.sandbox or "").lower()
    for known in ("deb", "rpm", "snap", "layered"):
        if known in kind:
            return known
    return "flatpak" if "flatpak" in kind or app.sandbox == "flatpak" or not kind else kind


def _category(app: App) -> str:
    for name in app.categories:
        if name.casefold() in _KEYS:
            return name.casefold()
    return "tools"


def _when(timestamp: int) -> str:
    if not timestamp:
        return ""
    seconds = max(0, int(datetime.now().timestamp()) - timestamp)
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def _system(state) -> SystemUpdate | None:
    """Luma's own update from the agent's state (luma_installer.depot_system_update.SystemUpdate)."""
    if state is None or not getattr(state, "managed", True):
        return None
    waiting = bool(state.available_version or state.staged_version)
    name = (state.staged_name if state.staged_version else state.available_name) if waiting else state.booted_name
    return SystemUpdate(name=name or state.booted_version, size=_size(state.download_bytes),
                        channel=(state.channel or "stable").capitalize(), notes=(),
                        running=state.booted_name or state.booted_version, checked=_when(state.last_check),
                        up_to_date=not waiting)


def world_from(catalogue: Catalogue | None, installed: dict[str, InstalledApp], system_state=None) -> World:
    here = Device("here", device_name(), _form())
    apps = []
    for app in (catalogue.apps if catalogue else ()):
        if app.unlisted:
            continue
        apps.append(replace(app, category_key=_category(app), package_kind=_package_kind(app),
                            size_label=app.size_label or _size(app.download_bytes),
                            runs=app.runs or ("desktop", "tablet")))
    pending = tuple(PendingUpdate(app_id, ("here",), record.version, record.update_version,
                                  _size(record.update_bytes),
                                  (record.update_summary,) if record.update_summary else ())
                    for app_id, record in installed.items() if record.has_update)
    popular = tuple(app.app_id for app in sorted(apps, key=lambda a: (a.installs_total, a.rating_count), reverse=True))
    rollback = []
    if system_state is not None and getattr(system_state, "rollback_available", False):
        rollback.append(DoneRow("luma", system_state.booted_name or "Luma", "", device_name()))
    return World(forms=FORMS, devices=(here,), here="here", categories=CATEGORIES, apps=tuple(apps),
                 popular=popular, installed={"here": tuple(installed)}, pending=pending, done=tuple(rollback),
                 auto={"apps": True, "luma": bool(getattr(system_state, "automatic_download", True)), "matched": False},
                 system=_system(system_state), kept=tuple(a.app_id for a in apps if a.app_id in installed))


class LiveStore(Store):
    """Loads the real world and acts on it; `on_world(world)` runs whenever it changes."""

    def __init__(self, on_world: Callable[[World], None]) -> None:
        from ..native import providers
        self.on_world = on_world
        self.catalogue_provider, self.installer, self.host_client = providers()
        self.catalogue: Catalogue | None = None
        self.installed: dict[str, InstalledApp] = {}
        try:
            from ..system_updates import SystemUpdates
            self.system = SystemUpdates(self._rebuild)
        except Exception:  # noqa: BLE001 - a missing agent leaves Luma's card out, nothing more
            self.system = None
        super().__init__(world_from(None, {}))
        self.reload()

    # ── reading ───────────────────────────────────────────────────────────

    def reload(self) -> None:
        self.catalogue_provider.load_catalogue(self._catalogue)
        self.installer.installed(self._installed)

    def _catalogue(self, result: Result) -> None:
        if result.ok:
            self.catalogue = result.value
            self._rebuild()

    def _installed(self, result: Result) -> None:
        if result.ok:
            self.installed = {record.app_id: record for record in result.value}
            self._rebuild()

    def _rebuild(self) -> None:
        self.world = world_from(self.catalogue, self.installed, self.system.state if self.system else None)
        self.on_world(self.world)

    # ── apps ──────────────────────────────────────────────────────────────

    def _finished(self, done: Done, words: str):
        def finished(result: Result) -> None:
            if result.ok:
                self.installer.installed(lambda r: (self._installed(r), done(self.world, words)))
            else:
                done(self.world, result.error.message if result.error else "That didn't work. Try again")
        return finished

    def install(self, item: Item, devices: list[str], done: Done, *, keep_matched: bool | None = None) -> None:
        if item.app is None:
            return
        self.installer.install(item.app, lambda _p: None,
                               self._finished(done, f"{item.name} was installed on {self.world.this_device.name}"))

    def uninstall(self, item: Item, device: str, done: Done) -> None:
        self.installer.remove(item.id, keep_data=True, callback=self._finished(
            done, f"{item.name} was uninstalled from {self.world.this_device.name}"))

    def update(self, item: Item, devices: tuple[str, ...], done: Done) -> None:
        record = self.installed.get(item.id)
        self.installer.update(item.id, lambda _p: None,
                              self._finished(done, f"{item.name} was updated on {self.world.this_device.name}"),
                              expected_commit=getattr(record, "update_commit", "") if record else "",
                              expected_installed_commit=getattr(record, "commit", "") if record else "")

    def update_all(self, done: Done) -> None:
        pending = list(self.world.pending)
        for entry in pending:
            item = self.world.item("app", entry.app)
            if item is not None:
                self.update(item, entry.devices, lambda world, _w: None)
        done(self.world, f"Updating {len(pending)} app{'s' if len(pending) != 1 else ''}")

    def check(self, done: Done) -> None:
        if self.system is not None:
            self.system.call("Check", then=lambda *_a: done(self.world, "Checked just now"))
        self.reload()

    # ── Luma itself ───────────────────────────────────────────────────────

    @property
    def system_phase(self) -> str:
        state = self.system.state if self.system else None
        if state is None or self.world.system is None or self.world.system.up_to_date:
            return "current"
        if state.staged_version:
            return "downloaded"
        if state.state in ("downloading", "staging") or (self.system and self.system.busy == "Download"):
            return "downloading"
        return "ready"

    @property
    def channel(self) -> str:
        state = self.system.state if self.system else None
        return (state.channel if state and state.channel else "stable").lower()

    def download_system(self, done: Done) -> None:
        if self.system is not None:
            self.system.call("Download", then=lambda *_a: done(self.world, ""))

    def restart(self, done: Done) -> None:
        if self.system is not None:
            self.system.restart()
        done(self.world, "")

    def set_channel(self, channel: str, done: Done) -> None:
        if self.system is not None:
            self.system.set_channel(channel)
        done(self.world, "")

    def go_back(self, done: Done) -> None:
        if self.system is not None:
            self.system.call("Rollback", then=lambda *_a: done(self.world, ""))

    def set_automatic(self, key: str, on: bool) -> None:
        super().set_automatic(key, on)
        if key == "luma" and self.system is not None:
            self.system.call("SetAutomaticDownload", on)
