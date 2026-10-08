# SPDX-License-Identifier: Apache-2.0
"""In-memory providers for LUMA_DEPOT_FIXTURE; no machine state is opened."""

from __future__ import annotations

from .os_updates import OsState
from .providers import (App, Catalogue, Category, CatalogProvider, Collection,
                        InstallationProvider, InstalledApp, Progress, Result, run_async)
from .v70_data import Listing, load_fixture


class V70Catalogue(CatalogProvider):
    def __init__(self, path: str) -> None:
        self.listings = load_fixture(path)
        self._apps = {listing.id: App(
            app_id=listing.id, name=listing.name, summary=listing.tagline,
            description=listing.tagline, categories=(listing.category.title(),),
            icon_name=listing.icon or "application-x-executable-symbolic",
            tone=listing.category, rating=listing.rating, rating_count=listing.reviews,
            featured=listing.id == "tide", tier="luma" if listing.luma else "listed",
            slug=listing.id, removable=listing.id not in {"filer", "viola", "terminal"},
            system_state="installed" if listing.id in {"filer", "viola", "terminal"} else "",
        ) for listing in self.listings}
        categories = (
            Category("Create", "Create", "lumaui-palette-symbolic"),
            Category("Work", "Work", "lumaui-briefcase-symbolic"),
            Category("Media", "Media", "lumaui-clapperboard-symbolic"),
            Category("Play", "Play", "lumaui-gamepad-2-symbolic"),
            Category("Tools", "Tools", "lumaui-wrench-symbolic"),
        )
        self.catalogue = Catalogue(
            tuple(self._apps.values()), categories, self._apps["tide"],
            (Collection("luma", "Made for Luma", "Free, private, and they work together",
                        tuple(listing.id for listing in self.listings if listing.luma)),
             Collection("popular", "Popular on Luma", "What people install first",
                        ("blender", "obsidian", "spotify", "signal", "vlc", "steam", "gimp", "discord", "libre"))),
        )

    def load_catalogue(self, callback, cancellable=None) -> None:
        run_async(lambda: self.catalogue, callback, cancellable)

    def search(self, query, callback, cancellable=None) -> None:
        from .v70_data import search
        run_async(lambda: tuple(self._apps[item.id] for item in search(self.listings, query)),
                  callback, cancellable)

    def app(self, app_id, callback, cancellable=None) -> None:
        run_async(lambda: self._apps[app_id], callback, cancellable)


class V70Installation(InstallationProvider):
    """The Studio's installed set. Actions alter memory only, never a file."""

    def __init__(self, listings: tuple[Listing, ...], apps: dict[str, App]) -> None:
        self.records = {item.id: InstalledApp(
            item.id, "1.0.159" if item.update else "1.0", 0,
            update_version=item.update, app=apps[item.id], managed=item.kind != "system",
        ) for item in listings if item.installed}

    def installed(self, callback, cancellable=None) -> None:
        run_async(lambda: tuple(self.records.values()), callback, cancellable)

    def free_bytes(self) -> int:
        return 10**12

    def install(self, app, on_progress, callback, cancellable=None) -> None:
        self.records[app.app_id] = InstalledApp(app.app_id, "1.0", 0, app=app, managed=True)
        run_async(lambda: self.records[app.app_id], callback, cancellable)

    def remove(self, app_id, *, keep_data, callback, cancellable=None) -> None:
        self.records.pop(app_id, None)
        run_async(lambda: {"app_id": app_id, "kept_data": keep_data}, callback, cancellable)

    def update(self, app_id, on_progress, callback, cancellable=None, *, expected_commit="", expected_installed_commit="") -> None:
        from dataclasses import replace
        record = self.records[app_id]
        self.records[app_id] = replace(record, version=record.update_version or record.version,
                                        update_version="")
        run_async(lambda: self.records[app_id], callback, cancellable)

    def launch(self, app_id, context=None) -> bool:
        # The fixture is only a design state; it never starts an installed app.
        return False


class V70OsReader:
    def state(self, callback, cancellable=None) -> None:
        run_async(lambda: OsState(
            enrolled=True, channel="Nightly", remote="dl.simplyluma.com",
            booted_version="Luma 1.0 · nightly September 16",
            staged_version="Luma 1.0 · nightly September 22", staged_bytes=312_000_000,
            notes=("Adjustable scroll speed for mice and touchpads, plus 14 more changes.",)),
            callback, cancellable)
