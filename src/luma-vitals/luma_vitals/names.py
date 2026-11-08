# SPDX-License-Identifier: Apache-2.0
"""Name a unit the way the person knows it: by its app's name, never its id.

systemd names an app's unit after its desktop id (``app-gnome-<id>-<n>.scope``,
``app-flatpak-<id>-<n>.scope``, ``app-<id>@<x>.service``,
``dbus-:1.2-<id>@0.service``), with ``-`` in the id escaped as ``\\x2d``. A
universal package runs in a container named ``luma-run-<application id>``,
whose launcher is ``org.projectluma.Installed.<application id>.desktop``.
"""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path

INSTALLED_PREFIX = "org.projectluma.Installed."
CONTAINER_PREFIX = "luma-run-"
FLATPAK_EXPORTS = ("/var/lib/flatpak/exports/share",)

_SCOPE = re.compile(r"^app-(?:gnome-|flatpak-)?(?P<id>.+?)-(?P<instance>[0-9]+|[0-9a-f]{8,})\.(?:scope|service)$")
_TEMPLATED = re.compile(r"^app-(?:gnome-|flatpak-)?(?P<id>.+?)@[^@]*\.service$")
_DBUS = re.compile(r"^dbus-:[0-9.]+-(?P<id>.+?)@[0-9]+\.service$")


@dataclass(frozen=True)
class AppName:
    name: str
    icon: str = ""
    desktop_id: str = ""
    #: The app's id when the unit is an app's, so its units can be grouped.
    app_id: str = ""


def unescape(name: str) -> str:
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1), 16)), name)


def app_id_of(unit: str) -> str:
    """The desktop id (without .desktop) an app's unit was started for, or ''."""
    if unit.endswith(".container") and unit.startswith(CONTAINER_PREFIX):
        return INSTALLED_PREFIX + unit[len(CONTAINER_PREFIX):-len(".container")]
    for pattern in (_DBUS, _TEMPLATED, _SCOPE):
        match = pattern.match(unit)
        if match:
            return unescape(match.group("id"))
    return ""


def _application_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots = [home, home / "flatpak/exports/share", *(Path(item) for item in system.split(":") if item),
             *(Path(item) for item in FLATPAK_EXPORTS)]
    seen, result = set(), []
    for root in roots:
        if root not in seen:
            seen.add(root)
            result.append(root / "applications")
    return result


def desktop_entry(desktop_id: str) -> dict[str, str] | None:
    if not desktop_id or "/" in desktop_id:
        return None
    for directory in _application_dirs():
        path = directory / f"{desktop_id}.desktop"
        if not path.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            return None
        return dict(parser["Desktop Entry"]) if parser.has_section("Desktop Entry") else None
    return None


def _from_id(app_id: str) -> str:
    """com.rhyme.viola -> Viola; deb-some-app-0123456789ab -> Some App."""
    if app_id.startswith(INSTALLED_PREFIX):
        rest = app_id[len(INSTALLED_PREFIX):]
        _kind, _, rest = rest.partition("-")
        head, _, tail = rest.rpartition("-")
        if head and re.fullmatch(r"[0-9a-f]{12}", tail):
            rest = head
        words = rest.replace("_", "-").split("-")
    else:
        words = [app_id.rsplit(".", 1)[-1]]
    words = [word for word in words if word]
    return " ".join(word[:1].upper() + word[1:] for word in words) or app_id


def describe(unit: str) -> AppName:
    if unit == "waydroid.lxc":
        return AppName("Android apps")
    app_id = app_id_of(unit)
    if not app_id:
        if unit.startswith("libpod-") or unit.endswith(".container"):
            return AppName("An app")
        return AppName("A background task")
    entry = desktop_entry(app_id) or {}
    return AppName(entry.get("Name") or _from_id(app_id), entry.get("Icon", ""),
                   f"{app_id}.desktop" if entry else "", app_id)


def display_name(unit: str) -> str:
    return describe(unit).name
