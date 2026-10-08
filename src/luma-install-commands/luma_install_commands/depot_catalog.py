# SPDX-License-Identifier: MPL-2.0
"""Depot's application catalogue, read as a name-to-source hint only.

dnf and apt resolve against Fedora's own repositories first, exactly as
Fedora itself does. Only a name neither dnf5 nor the Debian name table can
place is looked up here, so a browser or other application Fedora does not
package (Vivaldi, say) routes to the same source Depot's app store would
offer -- usually Flathub, sometimes a vendor repository already enabled on
this computer -- instead of a plain "not found".

This module never authorizes an install by itself and does not verify the
catalogue's signature the way Depot's own app store does (see
``luma_installer.depot_catalog`` in luma-application-installer, the verified
reader): it only turns a name into a backend and a source id. The actual
trust boundary stays where it already is for every path here -- flatpak's
own signature check of the Flathub remote configured by this package
(``data/flathub.flatpakrepo``) for a Flatpak result, and dnf5's own
resolution against repositories already enabled and trusted on this
computer for an rpm result. A tampered or stale catalogue file can only ever
point at a name in one of those two places a person could have typed
themselves; it cannot make either backend install something unsigned.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path

#: Where Depot's own package (luma-application-installer) installs the
#: catalogue; read directly rather than duplicated. Schema 4 first (the
#: shape both current catalogues use), the older schema 3 file otherwise.
DATA_DIRECTORY = Path(os.environ.get("LUMA_DEPOT_DATA_DIRECTORY", "/usr/share/luma/installer"))
CANDIDATES = (DATA_DIRECTORY / "depot-catalog-4.json", DATA_DIRECTORY / "depot-catalog.json")

#: Backends this reader can point a package-manager front end at: a Flatpak
#: install (Flathub, verified by flatpak itself) or an rpm already reachable
#: through a repository dnf5 has enabled. 'deb', 'download' and other
#: backends have no address a shell command can install from; Depot's own
#: installer handles those.
AUTOMATABLE = frozenset({"flatpak", "rpm"})

#: Luma only ships x86_64 and aarch64; normalize the odd platforms that spell
#: the second one differently (macOS' own Python, used in development).
_ARCH = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())


@dataclass(frozen=True)
class Match:
    id: str
    name: str
    backend: str
    source_id: str
    repository: str
    branch: str = "stable"


def _load(paths=CANDIDATES) -> list[dict]:
    for path in paths:
        try:
            with open(path, "rb") as stream:
                content = stream.read(8 * 1024 * 1024 + 1)
        except OSError:
            continue
        if len(content) > 8 * 1024 * 1024:
            continue
        try:
            document = json.loads(content)
        except ValueError:
            continue
        applications = document.get("applications")
        if isinstance(applications, list):
            return applications
    return []


def _related(target: str, value: str) -> bool:
    """``target`` names the same application as ``value``, allowing the vendor
    suffixes real-world install commands add (``vivaldi-stable``, ``-bin``,
    ``-beta``) without matching an unrelated name that merely contains it."""
    if not value:
        return False
    return (target == value or target.startswith(value + "-") or target.startswith(value + "_")
            or value.startswith(target + "-") or value.startswith(target + "_"))


def find(name: str, *, paths=CANDIDATES) -> list[Match]:
    """Catalogue entries matching ``name``, best (exact) matches first."""
    target = name.strip().lower()
    if not target:
        return []
    exact: list[Match] = []
    partial: list[Match] = []
    for row in _load(paths):
        if not isinstance(row, dict) or row.get("backend") not in AUTOMATABLE:
            continue
        architectures = row.get("architectures")
        if isinstance(architectures, list) and architectures and _ARCH not in architectures:
            continue
        row_id, row_name = str(row.get("id", "")), str(row.get("name", ""))
        source_id = row.get("source_id")
        if not row_id or not row_name or not source_id:
            continue
        match = Match(row_id, row_name, row["backend"], str(source_id), str(row.get("repository", "")),
                     str(row.get("branch") or "stable"))
        row_id_l, row_name_l = row_id.lower(), row_name.lower()
        if target in (row_id_l, row_name_l):
            exact.append(match)
        elif _related(target, row_id_l) or _related(target, row_name_l):
            partial.append(match)
    return exact or partial
