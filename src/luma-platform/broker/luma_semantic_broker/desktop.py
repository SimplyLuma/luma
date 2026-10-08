# SPDX-License-Identifier: Apache-2.0
"""Bounded lookup of root-owned desktop metadata without a hot-path GIO scan."""

from __future__ import annotations

import configparser
from pathlib import Path
import stat

from .validation import application_id


SYSTEM_APPLICATION_ROOTS = (
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
)
MAX_DESKTOP_BYTES = 256 * 1024


def _visible_owner_uid(
    owner_uid: int,
    *,
    uid_map_path: Path = Path("/proc/self/uid_map"),
    overflow_uid_path: Path = Path("/proc/sys/kernel/overflowuid"),
) -> int:
    """Translate a host UID into the UID visible in this user namespace.

    Hardened user services run in a one-UID user namespace. Root-owned files
    below the immutable system application roots therefore appear to be owned
    by the kernel overflow UID, not UID 0. Mapping the expected owner preserves
    the root-ownership check without weakening the service sandbox.
    """

    try:
        mappings = []
        for line in uid_map_path.read_text(encoding="utf-8").splitlines():
            inside, outside, length = (int(value) for value in line.split())
            if length <= 0:
                raise ValueError("invalid UID mapping length")
            mappings.append((inside, outside, length))
        for inside, outside, length in mappings:
            if outside <= owner_uid < outside + length:
                return inside + owner_uid - outside
        return int(overflow_uid_path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError, ValueError):
        # A malformed or unreadable namespace description must fail closed.
        return owner_uid


def desktop_application(
    app_id: str,
    *,
    roots: tuple[Path, ...] = SYSTEM_APPLICATION_ROOTS,
    owner_uid: int = 0,
) -> tuple[bool, str]:
    """Return a trusted display name for an exact, root-owned desktop ID."""

    identifier = application_id(app_id)
    visible_owner_uid = _visible_owner_uid(owner_uid)
    for root in roots:
        candidate = root / f"{identifier}.desktop"
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != visible_owner_uid
                or info.st_size > MAX_DESKTOP_BYTES
            ):
                continue
            text = resolved.read_text(encoding="utf-8")
            if "\x00" in text:
                continue
            parser = configparser.ConfigParser(
                interpolation=None,
                strict=False,
            )
            parser.read_string(text)
            if parser.get("Desktop Entry", "Type", fallback="") != "Application":
                continue
            name = parser.get("Desktop Entry", "Name", fallback="").strip()
            if name:
                return True, name
        except (OSError, UnicodeError, configparser.Error, ValueError):
            continue
    return False, identifier
