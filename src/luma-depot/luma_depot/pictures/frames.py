# SPDX-License-Identifier: Apache-2.0
"""How an app's screenshots are framed (handoff 9.1, 9.2), as recipes the gallery, thumbnails and the
lightbox all draw from, so the same picture appears at every size."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from luma_appkit import FramedPicture

from ..assets import data_file, wallpaper
from ..model.world import Item, World, _hash
from ..parts.face import icon_path

__all__ = ["Picture", "app_pictures", "shot_file"]

#: Real places a phone is held in (our own photography), one per app by its id's hash.
ROOMS = ("life-ari-street", "life-sync-terrace", "life-ledge-blog-1")
#: The dock in a desktop frame: the app itself joins after the first four.
DOCK = ("filer", "viola", "tide", "photos", "messages", "calendar", "notes")


@dataclass(frozen=True)
class Picture:
    """One picture of an item: `make(radius)` draws it; `phone` says it is a phone picture."""

    key: str
    label: str
    make: Callable[[float], Gtk.Widget]
    phone: bool = False


def shot_file(world: World, key: str) -> str | None:
    """A screenshot by key: Depot's own (Luma's apps), then the world's (a catalogue's cache, the fixture)."""
    found = data_file("screenshots", f"{key}.webp")
    if found is None and world.assets is not None and (world.assets / "shots" / f"{key}.webp").is_file():
        found = str(world.assets / "shots" / f"{key}.webp")
    return found


def room_for(item_id: str) -> str | None:
    return data_file("rooms", f"{ROOMS[_hash(item_id) % len(ROOMS)]}.webp")


def dock_for(world: World, item: Item) -> tuple[list[str], int | None]:
    ids = list(dict.fromkeys([*DOCK[:4], item.id, *DOCK[4:]]))
    icons, running = [], None
    for app_id in ids:
        other = world.item("app", app_id)
        path = icon_path(world, other) if other is not None else None
        if path is None:
            continue
        if app_id == item.id:
            running = len(icons)
        icons.append(path)
        if len(icons) == 8:
            break
    return icons, running


def app_pictures(world: World, item: Item) -> list[Picture]:
    pictures = []
    for key in item.app.shot_keys if item.app is not None else ():
        shot = shot_file(world, key)
        if shot is None:
            continue  # missing stays missing: no stand-in screenshot
        if "-phone-" in key:
            room = room_for(item.id)
            pictures.append(Picture(key, f"{item.name} on a phone",
                                    lambda r, s=shot, b=room: FramedPicture.phone(s, backdrop=b, radius=r,
                                                                                 label=f"{item.name} on a phone"),
                                    phone=True))
        else:
            wall = wallpaper("meadow" if key.endswith("-light") else "prism", world.assets / "art" if world.assets else None)
            icons, running = dock_for(world, item)
            pictures.append(Picture(key, f"{item.name} on the desktop",
                                    lambda r, s=shot, b=wall, d=icons, n=running: FramedPicture.desktop(
                                        s, backdrop=b, dock=d, running=n, radius=r,
                                        label=f"{item.name} on the desktop")))
    return pictures
