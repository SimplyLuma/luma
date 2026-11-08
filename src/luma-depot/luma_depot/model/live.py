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
from .world import FORMS, CategoryInfo, Device, Done as DoneRow, Item, PendingUpdate, Story, SystemUpdate, World

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
    """The listing's own category, or "": an app nobody filed is in no category shelf (never "Tools")."""
    for name in (app.category_key, *app.categories):
        if name and name.casefold() in _KEYS:
            return name.casefold()
    return ""


#: How many apps need real install counts before a list may say "Popular" and number them (Today shows six).
MEASURED = 6


def _popular(store: list[App], curated: tuple[str, ...], catalogue: Catalogue | None) -> tuple[tuple[str, ...], bool]:
    """Store apps by real installs when enough have them; else the Hub's curated order, unranked (the old
    window's `home_selection.popular_apps`, kept: measured first, then featured and collections, then the
    Explore picks, then the other publishers' apps by name)."""
    from ..home_selection import EXPLORE_SLUGS, catalogue_slug
    measured = sorted((a for a in store if a.installs_total > 0),
                      key=lambda a: (-a.installs_total, a.name.casefold(), a.app_id))
    if len(measured) >= MEASURED:
        return tuple(a.app_id for a in measured), True
    ids = {a.app_id for a in store}
    by_slug = {catalogue_slug(a): a.app_id for a in store}
    order = [a.app_id for a in measured] + [i for i in curated if i in ids]
    if catalogue is not None:
        order += [a.app_id for a in (catalogue.featured,) if a is not None and a.app_id in ids]
        order += [i for c in catalogue.collections for i in c.app_ids if i in ids]
    order += [by_slug[s] for s in EXPLORE_SLUGS if s in by_slug]
    order += [a.app_id for a in sorted(store, key=lambda a: (a.name.casefold(), a.app_id)) if a.tier != "luma"]
    return tuple(dict.fromkeys(order)), False


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


def _merge(provider: App, listing: App) -> App:
    """The v1 listing's words and pictures over the provider's entry, whose install identity stays."""
    keep = {"app_id": provider.app_id, "flatpak_id": provider.flatpak_id or listing.flatpak_id,
            "system_state": provider.system_state, "channel_kind": provider.channel_kind, "sandbox": provider.sandbox,
            "installable": provider.installable, "removable": provider.removable, "permissions": provider.permissions,
            "icon_name": provider.icon_name}
    return replace(listing, **keep)


def world_from(catalogue: Catalogue | None, installed: dict[str, InstalledApp], system_state=None,
               listings=None, media=None, curated: tuple[str, ...] = ()) -> World:
    """The world from the installer's catalogue (its listings and every app installed here, Android and
    other local packages included) and the signed v1 listings. Store lists read only Depot listings
    (`World.store`): the v1 listings once they are here (the Hub is their source), until then the
    installer's own signed catalogue. Local apps stay in `apps` for the Library and their pages."""
    here = Device("here", device_name(), _form())
    apps = []
    v1_apps, v1_looks, v1_tweaks, _story = listings or ([], [], [], {})
    by_id = {a.app_id: a for a in v1_apps}
    seen = {}  # v1 listing id -> the world's id for it
    provided = list(catalogue.apps if catalogue else ())
    merged = []
    for app in provided:
        listing = by_id.get(app.app_id) or by_id.get(app.flatpak_id) or by_id.get(app.slug)
        merged.append(_merge(app, listing) if listing is not None else app)
        if listing is not None:
            seen[listing.app_id] = app.app_id
    # listed in v1 but unknown to the installer's catalogue: shown, never installed from a ref
    merged += [replace(a, installable=False) for a in v1_apps if a.app_id not in seen]
    if v1_apps:
        store_ids = set(seen.values()) | {a.app_id for a in v1_apps if a.app_id not in seen}
    else:
        store_ids = {a.app_id for a in provided if a.app_id.startswith("catalog:")}
    for app in merged:
        if app.unlisted:
            continue
        apps.append(replace(app, category_key=_category(app), package_kind=_package_kind(app),
                            size_label=app.size_label or _size(app.download_bytes),
                            runs=app.runs or ("desktop", "tablet")))
    pending = tuple(PendingUpdate(app_id, ("here",), record.version, record.update_version,
                                  _size(record.update_bytes),
                                  (record.update_summary,) if record.update_summary else ())
                    for app_id, record in installed.items() if record.has_update)
    store = tuple(a.app_id for a in apps if a.app_id in store_ids)
    popular, measured = _popular([a for a in apps if a.app_id in store_ids],
                                 tuple(seen.get(i, i) for i in curated), catalogue)
    rollback = []
    if system_state is not None and getattr(system_state, "rollback_available", False):
        rollback.append(DoneRow("luma", system_state.booted_name or "Luma", "", device_name()))
    story = None
    if _story and _story.get("app") and any(a.app_id == _story["app"] for a in apps):
        story = Story(_story.get("kick", ""), _story.get("title", ""), _story.get("blurb", ""), _story["app"],
                      _story.get("art", ""), int(_story.get("hue", 0) or 0))
    return World(forms=FORMS, devices=(here,), here="here", categories=CATEGORIES, apps=tuple(apps),
                 looks=tuple(v1_looks), tweaks=tuple(v1_tweaks), media=media, story=story,
                 popular=popular, popular_measured=measured, store=store, installed={"here": tuple(installed)}, pending=pending, done=tuple(rollback),
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
        self.busy: set[str] = set()
        self.listings = None
        self.curated: tuple[str, ...] = ()
        from .catalogue_v1 import MediaCache
        self.media = MediaCache(on_ready=self._rebuild)
        self._load_listings()
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

    def _load_listings(self) -> None:
        """The signed v1 catalogue in the background; without it the AppStream data stands alone."""
        import threading
        from . import catalogue_v1

        def work() -> None:
            document = catalogue_v1.load()
            if document is None:
                return
            listings, curated = catalogue_v1.parse(document), catalogue_v1.curated(document)
            GLib.idle_add(lambda: (self._listed(listings, curated), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def _listed(self, listings, curated: tuple[str, ...] = ()) -> None:
        self.listings, self.curated = listings, curated
        apps, looks, tweaks, _story = listings
        urls = [a.icon_url for a in apps] + [s for a in apps for s in a.shot_keys] + [a.mark for a in apps]
        urls += [x.art for x in looks] + [listings[3].get("art", "")]
        self.media.prefetch(urls)
        self._rebuild()

    def _rebuild(self) -> None:
        self.world = world_from(self.catalogue, self.installed, self.system.state if self.system else None,
                                listings=self.listings, media=self.media.path, curated=self.curated)
        self.on_world(self.world)

    # ── apps ──────────────────────────────────────────────────────────────

    def permission_changes(self, app_id: str) -> tuple:
        record = self.installed.get(app_id)
        return tuple(record.permission_changes) if record is not None else ()

    def _finished(self, done: Done, words: str, item: Item | None = None, failed: str = "", event: str = ""):
        def finished(result: Result) -> None:
            if item is not None:
                self.busy.discard(item.id)
            if result.ok:
                self.installer.installed(lambda r: (self._installed(r), done(self.world, words)))
                from .events import send
                if item is not None and event:
                    send(item, event)
            else:
                done(self.world, failed or "That didn't work. Try again")
        return finished

    def install(self, item: Item, devices: list[str], done: Done, *, keep_matched: bool | None = None) -> None:
        if item.app is None:
            return
        self.busy.add(item.id)
        done(self.world, "")  # the key reads Installing… while it runs (17.2)
        self.installer.install(item.app, lambda _p: None,
                               self._finished(done, f"{item.name} was installed on {self.world.this_device.name}", item,
                                              f"Couldn't install {item.name}. Try again", "install"))

    def uninstall(self, item: Item, device: str, done: Done) -> None:
        self.busy.add(item.id)
        self.installer.remove(item.id, keep_data=True, callback=self._finished(
            done, f"{item.name} was uninstalled from {self.world.this_device.name}", item,
            f"Couldn't uninstall {item.name}. Try again", "uninstall"))

    # An update that asks for more (a permission added or widened) is held until the person approves it
    # on the app's page, under the changes it lists. Bulk, retry and background updates never approve
    # (the old window's rule, kept exactly).
    @staticmethod
    def asks_for_more(record: InstalledApp | None) -> bool:
        return record is not None and any(change.change in ("added", "widened") and change.key != "files.portal"
                                          for change in record.permission_changes)

    def held(self, app_id: str) -> bool:
        from luma_installer import depot_autoupdate
        record = self.installed.get(app_id)
        if not self.asks_for_more(record):
            return False
        pending = depot_autoupdate.Pending(app_id, record.app.name if record.app else app_id, record.update_version,
                                           True, record.update_commit, record.commit)
        state = getattr(self.installer, "permission_state", None) or depot_autoupdate.load()
        return depot_autoupdate.is_held(pending, state)

    def update(self, item: Item, devices: tuple[str, ...], done: Done, *, approve: bool = False) -> None:
        from luma_installer import depot_autoupdate
        record = self.installed.get(item.id)
        if record is None or not record.has_update or not record.update_commit or not record.commit:
            done(self.world, f"{item.name} changed. Check again before updating")
            return
        extra = {}
        if self.asks_for_more(record):
            pending = depot_autoupdate.Pending(item.id, item.name, record.update_version, True,
                                               record.update_commit, record.commit)
            if approve and getattr(self.installer, "requires_permission_approval", False):
                extra = {"permission_approval": True}
            elif approve:
                try:
                    depot_autoupdate.approve(pending)
                except (OSError, ValueError):
                    done(self.world, f"Couldn't update {item.name}. Check again and review it")
                    return
            elif self.held(item.id):
                done(self.world, f"{item.name} asks for more. Review it on its page")
                return
        self.busy.add(item.id)
        self.installer.update(item.id, lambda _p: None,
                              self._finished(done, f"{item.name} was updated on {self.world.this_device.name}", item,
                                             f"Couldn't update {item.name}. Try again", "update"),
                              None, expected_commit=record.update_commit, expected_installed_commit=record.commit,
                              **extra)

    def update_all(self, done: Done) -> None:
        ready = [entry for entry in self.world.pending if not self.held(entry.app)]
        for entry in ready:
            item = self.world.item("app", entry.app)
            if item is not None:
                self.update(item, entry.devices, lambda world, _w: self.on_world(world))
        held = len(self.world.pending) - len(ready)
        words = f"Updating {len(ready)} app{'s' if len(ready) != 1 else ''}"
        if held:
            words += f". {held} ask{'s' if held == 1 else ''} for more; review on {'its' if held == 1 else 'their'} page"
        done(self.world, words)

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
