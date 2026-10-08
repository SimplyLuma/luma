# SPDX-License-Identifier: Apache-2.0
"""SearchProvider2 discovery and adaptation without a duplicate content index."""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable


GROUP = "Shell Search Provider"


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    desktop_id: str
    bus_name: str
    object_path: str
    kind: str
    name: str
    description: str
    autostart: bool
    default_disabled: bool


_KIND_MARKERS = (
    ("setting", ("controlcenter", "settings")),
    ("file", ("nautilus", "files", "tracker")),
    ("mail", ("geary", "evolution", "mail")),
    ("contact", ("contact",)),
    ("calendar", ("calendar",)),
    ("message", ("message", "chat")),
    ("photo", ("photo", "shotwell")),
    ("music", ("music", "rhythmbox")),
    ("place", ("maps", "place")),
)


def infer_kind(desktop_id: str, declared: str = "") -> str:
    declared = declared.strip().lower()
    if declared in {item[0] for item in _KIND_MARKERS} | {"app"}:
        return declared
    folded = desktop_id.casefold()
    for kind, markers in _KIND_MARKERS:
        if any(marker in folded for marker in markers):
            return kind
    return "app"


_CATEGORY_KINDS = (
    ("mail", ("Email",)),
    ("contact", ("ContactManagement",)),
    ("calendar", ("Calendar",)),
    ("message", ("InstantMessaging", "Chat")),
    ("photo", ("Photography",)),
    ("music", ("Music",)),
    ("place", ("Maps",)),
    ("file", ("FileManager",)),
)


def kind_from_categories(categories: str | None, fallback: str = "app") -> str:
    """A mail app's results are mail, whatever the app is called."""
    names = set((categories or "").split(";"))
    for kind, markers in _CATEGORY_KINDS:
        if names.intersection(markers):
            return kind
    return fallback


def provider_directories(environment: dict[str, str] | None = None) -> tuple[Path, ...]:
    env = os.environ if environment is None else environment
    data_home = Path(env.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    data_dirs = env.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    return tuple(
        root / "gnome-shell/search-providers"
        for root in (data_home, *(Path(item) for item in data_dirs if item))
    )


def discover_providers(
    directories: Iterable[Path] | None = None,
    *,
    excluded_bus_names: frozenset[str] = frozenset({"org.projectluma.Search"}),
) -> tuple[Provider, ...]:
    """Read standard provider descriptors once at activation time."""
    found: dict[tuple[str, str], Provider] = {}
    for directory in directories or provider_directories():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.search-provider.ini")):
            parser = ConfigParser(interpolation=None)
            try:
                parser.read(path, encoding="utf-8")
                section = parser[GROUP]
                if section.getint("Version", fallback=0) < 2:
                    continue
                desktop_id = section["DesktopId"].strip()
                bus_name = section["BusName"].strip()
                object_path = section["ObjectPath"].strip()
            except (OSError, KeyError, ValueError):
                continue
            if bus_name in excluded_bus_names:
                continue
            identifier = path.name.removesuffix(".search-provider.ini")
            provider = Provider(
                id=identifier,
                desktop_id=desktop_id,
                bus_name=bus_name,
                object_path=object_path,
                kind=infer_kind(desktop_id, section.get("X-Luma-Kind", "")),
                name=section.get("X-Luma-Name", desktop_id.removesuffix(".desktop")),
                description=section.get(
                    "X-Luma-Description",
                    f"Search results provided by {desktop_id.removesuffix('.desktop')}",
                ),
                autostart=section.getboolean("AutoStart", fallback=True),
                default_disabled=section.getboolean("DefaultDisabled", fallback=False),
            )
            found.setdefault((bus_name, object_path), provider)
    return tuple(sorted(found.values(), key=lambda provider: provider.id))
