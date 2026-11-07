# SPDX-License-Identifier: Apache-2.0
"""`luma lint` rules for the [tv] table: opting in to the TV home.

An application appears on the shell's TV home only when it declares
`[tv] supported = true`. The shell reads applications from the installed
desktop entries (the catalogue Home and the shelf use), so the declaration is
installed as X-Luma-TV keys; these rules keep the two in agreement.
See docs/developer-platform/tv-mode.md.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Any

KNOWN_KEYS = {"supported", "tile", "presentation"}
PRESENTATIONS = ("tv", "fullscreen")
_ICON_NAME = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


def tile_icon_name(app_id: str) -> str:
    """The installed icon name of an application's wide TV tile."""
    return f"{app_id}-tv"


def tv_errors(manifest: dict[str, Any]) -> list[str]:
    """Rules that need only the manifest itself."""
    tv = manifest.get("tv")
    if tv is None:
        return []
    if not isinstance(tv, dict):
        return ["tv must be a table"]
    errors = [f"tv.{key} is not a known key" for key in sorted(set(tv) - KNOWN_KEYS)]
    if type(tv.get("supported")) is not bool:
        errors.append("tv.supported must be true or false")
    if tv.get("supported") is True:
        tile = tv.get("tile")
        if not isinstance(tile, str) or not tile.endswith(".svg"):
            errors.append("tv.tile must name the 16:9 wide tile, an SVG in the project")
        if tv.get("presentation", "tv") not in PRESENTATIONS:
            errors.append("tv.presentation must be one of " + ", ".join(PRESENTATIONS))
    return errors


def tv_project_errors(manifest: dict[str, Any], root: Path, desktop_file: Path | None) -> list[str]:
    """Rules that compare the declaration with the project's files."""
    tv = manifest.get("tv")
    if not isinstance(tv, dict) or tv.get("supported") is not True or not isinstance(tv.get("tile"), str):
        return []
    application = manifest.get("application", {})
    app_id = str(application.get("id", "")) if isinstance(application, dict) else ""
    errors: list[str] = []
    tile = (root / tv["tile"]).resolve()
    if not tile.is_relative_to(root.resolve()):
        errors.append("tv.tile must stay inside the project")
    elif not tile.is_file():
        errors.append(f"tv.tile does not exist: {tv['tile']}")
    elif tile.stem != tile_icon_name(app_id):
        errors.append(f"tv.tile must be named {tile_icon_name(app_id)}.svg so it installs as that icon")
    if desktop_file is None or not desktop_file.is_file():
        return errors
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(desktop_file, encoding="utf-8")
        entry = parser["Desktop Entry"]
    except (OSError, UnicodeError, configparser.Error, KeyError):
        return errors
    expected = {"X-Luma-TV": "true", "X-Luma-TV-Tile": tile_icon_name(app_id),
                "X-Luma-TV-Presentation": tv.get("presentation", "tv")}
    for key, value in expected.items():
        if entry.get(key) != value:
            errors.append(f"desktop file {key} must be {value} to match [tv]")
    return errors
