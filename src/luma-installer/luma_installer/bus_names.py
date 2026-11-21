# SPDX-License-Identifier: Apache-2.0
"""The session-bus names an application may own, from what its package says.

An application's bus is filtered. A name it may not own fails its start in
ways that look like nothing happened: a toolkit's single-instance check asks
for the name, is refused, concludes another instance is running, hands it the
request and exits with status 0 (FeatherPad, started plainly).

Flatpak grants an application its ID and the names under it. A package has no
single ID, so the names come from what it ships:

* **declared**: the file names of its launchers (the desktop entry
  specification makes a launcher's file name its bus name), the `Name=` of
  every D-Bus service file it installs, and its AppStream IDs;
* **embedded**: names the launcher's own program contains that carry the
  package's name as one of their elements (`org.featherpad.FeatherPad` in
  `featherpad`). Many applications register a name no file declares.

Names the session's own services answer to are never granted, whatever a
package says.
"""
from __future__ import annotations

import configparser
import re
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)+")
RESERVED_PREFIXES = ("org.freedesktop.", "org.gtk.", "org.a11y.", "org.mpris.", "org.projectluma.",
                     "io.luma.", "org.gnome.Shell", "org.gnome.Mutter", "org.gnome.SessionManager",
                     "org.gnome.SettingsDaemon", "org.gnome.ScreenSaver", "org.gnome.keyring")
LIMIT = 16
# Words in a package name that are packaging, not the application's name.
_PACKAGING = {"launcher", "bin", "common", "desktop", "app", "stable", "beta", "client", "installer", "release", "esr",
              "latest", "linux", "amd64", "arm64", "x86", "x64", "appimage", "deb", "rpm", "portable"}
# What looks like a bus name inside a program and is a file, a host or a resource instead.
_NOT_NAMES = re.compile(r"\.(so|desktop|service|png|svg|xml|json|html?|js|css|qm|qml|txt|conf|ini|ui|pak|bin|dat|log|"
                        r"com|org|net|io|dev|app)(\.\d+)*$", re.IGNORECASE)

DESKTOP_FOLDERS = ("/usr/share/applications", "/usr/local/share/applications")
SERVICE_FOLDERS = ("/usr/share/dbus-1/services", "/usr/local/share/dbus-1/services")
METAINFO_FOLDERS = ("/usr/share/metainfo", "/usr/share/appdata")


def allowed(name: str) -> bool:
    return (len(name) <= 200 and bool(NAME.fullmatch(name))
            and not any(name == prefix.rstrip(".") or name.startswith(prefix) for prefix in RESERVED_PREFIXES))


def identity_words(*identities: str) -> set[str]:
    """What a bus name's element has to be for the name to count as this package's."""
    words: set[str] = set()
    for identity in identities:
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", identity.casefold()) if part]
        # A trailing hash or version is not a name.
        parts = [part for part in parts if not re.fullmatch(r"[0-9a-f]{8,}|\d+", part)]
        kept = [part for part in parts if part not in _PACKAGING] or parts
        words.update(part for part in kept if len(part) >= 4)
        if len(kept) > 1 and len("".join(kept)) >= 4:
            words.add("".join(kept))
    return words


def declared(files: Iterable[str], read: Callable[[str], str | None], prefix: str = "") -> list[str]:
    """Names the package's own files declare. `prefix` is where an unpacked folder sits (an AppImage's /opt/ID)."""
    names: list[str] = []
    for line in files:
        path = PurePosixPath(line.strip())
        folder, name = str(path.parent), path.name
        local = folder == prefix.rstrip("/") if prefix else False
        if name.endswith(".desktop") and (folder in DESKTOP_FOLDERS or local or folder == f"{prefix}/usr/share/applications"):
            names.append(name[:-len(".desktop")])
        elif name.endswith(".service") and (folder in SERVICE_FOLDERS or folder == f"{prefix}/usr/share/dbus-1/services"):
            text = read(str(path)) or ""
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read_string(text)
                names.append(parser["D-BUS Service"].get("Name", "").strip())
            except (configparser.Error, KeyError):
                continue
        elif (folder in METAINFO_FOLDERS or folder == f"{prefix}/usr/share/metainfo") and name.endswith((".metainfo.xml", ".appdata.xml")):
            names.append(re.sub(r"\.(metainfo|appdata)\.xml$", "", name))
            match = re.search(r"<id>\s*([^<\s]+)\s*</id>", read(str(path)) or "")
            if match:
                names.append(match.group(1).removesuffix(".desktop"))
    return names


def embedded(tokens: Iterable[str], words: set[str]) -> list[str]:
    """Of the name-like strings found in a program, the ones that carry the package's name."""
    names: list[str] = []
    for token in tokens:
        token = token.strip().strip(".-")
        if token.count(".") < 2 or not NAME.fullmatch(token) or _NOT_NAMES.search(token):
            continue
        elements = [element.casefold() for element in token.split(".")]
        # A reverse-DNS name starts with a short top-level word; a version string or a host name does not qualify.
        if not re.fullmatch(r"[a-z]{2,6}", elements[0]) or elements[0] == "www":
            continue
        if any(element in words for element in elements[1:]) and token not in names:
            names.append(token)
    return names


def scan_command(words: set[str], programs: list[str]) -> list[str] | None:
    """A grep that prints the name-like strings around the package's words in its programs."""
    usable = sorted(word for word in words if re.fullmatch(r"[a-z0-9]{4,40}", word))
    if not usable or not programs:
        return None
    pattern = "[A-Za-z0-9_.-]*(" + "|".join(usable) + ")[A-Za-z0-9_.-]*"
    return ["grep", "-a", "-o", "-h", "-i", "-E", "-m", "20000", pattern, *programs]


def select(*groups: Iterable[str]) -> list[str]:
    """The names to grant, in order, without the reserved and the malformed."""
    names: list[str] = []
    for group in groups:
        for name in group:
            if name and allowed(name) and name not in names:
                names.append(name)
    return names[:LIMIT]


def proxy_arguments(names: Iterable[str]) -> list[str]:
    return [argument for name in names for argument in (f"--own={name}", f"--own={name}.*")]
