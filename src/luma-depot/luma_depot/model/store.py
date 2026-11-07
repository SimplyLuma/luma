# SPDX-License-Identifier: Apache-2.0
"""Where Depot's actions go: the person's real Luma, or the approved sample.

Pages ask the store to install, uninstall and update, and to drive Luma's own
update; when it is done the store hands the shell a new `World` and a few
words for the toast. `SampleStore` changes the sample world in memory (the
design fixture, previews, screenshots); `LiveStore` asks the catalogue, the
installation provider and the update agent, and rebuilds the world from what
they report.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .world import Item, World

__all__ = ["SampleStore", "Store"]

Done = Callable[[World, str], None]


class Store:
    """The actions every page uses. `done(world, toast)` runs when one finishes."""

    def __init__(self, world: World) -> None:
        self.world = world

    def install(self, item: Item, devices: list[str], done: Done, *, keep_matched: bool | None = None) -> None:
        raise NotImplementedError

    def uninstall(self, item: Item, device: str, done: Done) -> None:
        raise NotImplementedError

    def update(self, item: Item, devices: tuple[str, ...], done: Done, *, approve: bool = False) -> None:
        raise NotImplementedError

    #: Items with an install, removal or update running (the key reads Installing…).
    busy: set = set()

    def held(self, app_id: str) -> bool:
        """Whether an update waits for the person to approve what it asks for more."""
        return False

    def asks_for_more(self, app_id: str) -> bool:
        return bool([c for c in self.permission_changes(app_id) if c.change in ("added", "widened")])

    def permission_changes(self, app_id: str) -> tuple:
        """What a waiting update adds, widens, narrows or removes (Permission rows with `change`)."""
        return ()

    def update_all(self, done: Done) -> None:
        raise NotImplementedError

    def check(self, done: Done) -> None:
        raise NotImplementedError

    # Luma itself (11.2, 11.3, 11.7)
    def download_system(self, done: Done) -> None:
        raise NotImplementedError

    def restart(self, done: Done) -> None:
        raise NotImplementedError

    def set_channel(self, channel: str, done: Done) -> None:
        raise NotImplementedError

    def go_back(self, done: Done) -> None:
        raise NotImplementedError

    def set_automatic(self, key: str, on: bool) -> None:
        self.world.auto[key] = on

    @property
    def channel(self) -> str:
        return (self.world.system.channel if self.world.system else "").lower() or "stable"

    @property
    def system_phase(self) -> str:
        """ready · downloading · downloaded · current"""
        return "current" if self.world.system is None or self.world.system.up_to_date else "ready"


def _names(world: World, devices) -> str:
    names = [world.device(d).name for d in devices]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class SampleStore(Store):
    """The approved mockup's world, changed in memory with the mockup's words."""

    def __init__(self, world: World) -> None:
        super().__init__(world)
        self._phase = "ready"
        self._channel = "nightly"

    def _set(self, world: World, done: Done, words: str) -> None:
        self.world = world
        done(world, words)

    def install(self, item: Item, devices: list[str], done: Done, *, keep_matched: bool | None = None) -> None:
        world = self.world
        for device in devices:
            world = world.with_on(item, device, True)
        if keep_matched is not None:
            world = world.with_matched(item.id, keep_matched)
        if devices == [self.world.here]:
            words = f"{item.name} was installed on {self.world.this_device.name}"
        else:
            words = f"{item.name}: sent to {_names(self.world, devices)}"
        self._set(world, done, words)

    def uninstall(self, item: Item, device: str, done: Done) -> None:
        self._set(self.world.with_on(item, device, False), done,
                  f"{item.name} was uninstalled from {self.world.device(device).name}")

    def update(self, item: Item, devices: tuple[str, ...], done: Done, *, approve: bool = False) -> None:
        if devices == (self.world.here,):
            words = f"{item.name} was updated on {self.world.this_device.name}"
        else:
            words = f"{item.name}: updating on {_names(self.world, devices)}"
        self._set(self.world.with_updated(item.id, devices), done, words)

    def update_all(self, done: Done) -> None:
        world, count = self.world, len(self.world.pending)
        for pending in self.world.pending:
            world = world.with_updated(pending.app, pending.devices)
        self._set(world, done, f"Updating {count} app{'s' if count != 1 else ''} on your devices")

    def check(self, done: Done) -> None:
        system = self.world.system
        world = replace(self.world, system=replace(system, checked="just now")) if system else self.world
        self._set(world, done, "Checked just now")

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def system_phase(self) -> str:
        if self.world.system is None or self.world.system.up_to_date:
            return "current"
        return self._phase

    def download_system(self, done: Done) -> None:
        from gi.repository import GLib
        self._phase = "downloading"
        done(self.world, "")

        def finished() -> bool:
            self._phase = "downloaded"
            done(self.world, "")
            return False

        GLib.timeout_add(2600, finished)

    def restart(self, done: Done) -> None:
        done(self.world, "Restarting into Luma…")

    def set_channel(self, channel: str, done: Done) -> None:
        self._channel = channel
        done(self.world, "")

    def go_back(self, done: Done) -> None:
        running = self.world.system.running if self.world.system else "Luma"
        done(self.world, f"Restarting into {running}…")
