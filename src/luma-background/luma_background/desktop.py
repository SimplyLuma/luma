# SPDX-License-Identifier: MPL-2.0
"""Desktop entries: the root of an app's identity on the session.

Only an app with an installed desktop entry can have an agent. The entry
gives the name and icon people see, and its Exec line is the one command the
service trusts to start the app's own code.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from . import ids

MAX_DESKTOP_BYTES = 128 * 1024
FLATPAK_BINARY = "/usr/bin/flatpak"


@dataclass(frozen=True, slots=True)
class DesktopEntry:
    app_id: str
    path: str
    name: str
    icon: str
    exec_argv: tuple[str, ...]
    flatpak: str = ""
    system: bool = False
    hidden: bool = False

    @property
    def is_flatpak(self) -> bool:
        return bool(self.flatpak)

    @property
    def program(self) -> str:
        """The program the entry runs: for a Flatpak, the command in its sandbox."""
        if self.is_flatpak:
            for argument in self.exec_argv:
                if argument.startswith("--command="):
                    return argument.split("=", 1)[1]
            return ""
        return self.exec_argv[0] if self.exec_argv else ""


def _unescape_value(value: str) -> str:
    out = []
    iterator = iter(value)
    for character in iterator:
        if character != "\\":
            out.append(character)
            continue
        following = next(iterator, "")
        out.append({"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(following, following))
    return "".join(out)


def parse_exec(value: str) -> tuple[str, ...]:
    """Split an Exec value as the Desktop Entry Specification quotes it."""

    argv: list[str] = []
    current: list[str] = []
    in_token = False
    quoted = False
    index = 0
    while index < len(value):
        character = value[index]
        if quoted:
            if character == "\\" and index + 1 < len(value) and value[index + 1] in '"`$\\':
                current.append(value[index + 1])
                index += 2
                continue
            if character == '"':
                quoted = False
            else:
                current.append(character)
        elif character == '"':
            quoted = True
            in_token = True
        elif character == " ":
            if in_token:
                argv.append("".join(current))
                current = []
                in_token = False
        else:
            current.append(character)
            in_token = True
        index += 1
    if quoted:
        raise ValueError("unterminated quote in Exec")
    if in_token:
        argv.append("".join(current))
    # Field codes are placeholders for files the launcher passes; an agent is
    # never given files, so they are dropped rather than passed through.
    return tuple(
        argument for argument in argv
        if not (len(argument) == 2 and argument[0] == "%")
        and argument not in {"@@", "@@u", "@@f"}
    )


def read_group(path: Path, group: str = "Desktop Entry") -> dict[str, str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_DESKTOP_BYTES + 1)
    if len(raw) > MAX_DESKTOP_BYTES:
        raise ValueError("desktop entry is too large")
    values: dict[str, str] = {}
    current = None
    for line in raw.decode("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            continue
        if current != group or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), _unescape_value(value.strip()))
    return values


def _locale_names() -> list[str]:
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable, "")
        if value and value not in {"C", "POSIX"}:
            base = value.split(".", 1)[0].split("@", 1)[0]
            names = [base]
            if "_" in base:
                names.append(base.split("_", 1)[0])
            return names
    return []


def is_system_path(path: Path) -> bool:
    """True when neither the file nor any directory above it is the user's."""

    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    if not str(resolved).startswith(("/usr/", "/var/lib/flatpak/", "/etc/")):
        return False
    probe = resolved
    while True:
        try:
            info = probe.lstat()
        except OSError:
            return False
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
        if probe.parent == probe:
            return True
        probe = probe.parent


def load(path: Path) -> DesktopEntry:
    expected = path.name[: -len(".desktop")]
    if not ids.is_app_id(expected):
        raise ValueError(f"desktop file name is not an application ID: {path.name}")
    values = read_group(path)
    if values.get("Type") != "Application":
        raise ValueError("desktop entry is not an application")
    name = values.get("Name", "")
    for locale in _locale_names():
        if f"Name[{locale}]" in values:
            name = values[f"Name[{locale}]"]
            break
    exec_value = values.get("Exec", "")
    argv = parse_exec(exec_value) if exec_value else ()
    flatpak = values.get("X-Flatpak", "")
    if flatpak and flatpak != expected:
        raise ValueError("X-Flatpak does not match the desktop file name")
    return DesktopEntry(
        app_id=expected,
        path=str(path),
        name=name.strip()[:80] or expected,
        icon=values.get("Icon", "").strip(),
        exec_argv=argv,
        flatpak=flatpak,
        system=is_system_path(path),
        hidden=values.get("Hidden", "").lower() == "true",
    )


def data_dirs(environ: dict[str, str] | None = None) -> list[Path]:
    environ = os.environ if environ is None else environ
    value = environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(item) for item in value.split(":") if item.startswith("/")]


def data_home(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    value = environ.get("XDG_DATA_HOME", "")
    if value.startswith("/"):
        return Path(value)
    return Path(environ.get("HOME", str(Path.home()))) / ".local/share"


def config_home(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    value = environ.get("XDG_CONFIG_HOME", "")
    if value.startswith("/"):
        return Path(value)
    return Path(environ.get("HOME", str(Path.home()))) / ".config"


def find(app_id: str, environ: dict[str, str] | None = None) -> DesktopEntry | None:
    """The desktop entry the session would use for this ID, if any."""

    ids.app_id(app_id)
    for directory in [data_home(environ), *data_dirs(environ)]:
        candidate = directory / "applications" / f"{app_id}.desktop"
        if candidate.is_file():
            try:
                entry = load(candidate)
            except (OSError, UnicodeError, ValueError):
                return None
            return None if entry.hidden else entry
    return None
