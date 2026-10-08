# SPDX-License-Identifier: MPL-2.0
"""Open at Login: whether an app starts on its own when the person logs in.

There is one answer per app, read from where the session actually looks, so
anything that makes an app start by itself is shown and can be turned off:

- XDG autostart entries, in the person's folder and in $XDG_CONFIG_DIRS
  (`/etc/xdg/autostart`), the person's file of a name replacing the system's,
  read with the rules systemd-xdg-autostart-generator applies at login;
- the Background portal's autostart entries (`X-XDP-Autostart`), which this
  service runs as the app's background agent instead, so for those apps the
  login start *is* the agent and its allow decision is the switch;
- entries naming a Luma background agent (`X-Luma-Background-Agent`), which are
  the agent's own fallback and are controlled by "Run in the Background".

Turning an app off writes the standard override: `Hidden=true` in the person's
folder (for a system entry, a copy of it). Turning it on clears that, or, when
the app has no entry at all, writes one from the app's own desktop entry. Files
are replaced atomically; nothing outside the person's autostart folder is
written.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import desktop, ids
from .registry import AgentRecord, Layout

DESKTOP_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,199}$")
#: Launchers whose name says nothing about which app they start.
GENERIC_PROGRAMS = frozenset({
    "env", "sh", "bash", "dash", "zsh", "python", "python3", "perl", "flatpak", "gapplication",
    "gtk-launch", "dbus-send", "gdbus", "xdg-open", "sleep", "exec", "systemd-run", "snap",
})
OVERRIDE_KEY = "X-Luma-Login-Override"
APP_KEY = "X-Luma-App"


class LoginItemError(ValueError):
    pass


def is_desktop_id(value: object) -> bool:
    return isinstance(value, str) and value.isascii() and DESKTOP_ID.fullmatch(value) is not None \
        and not value.lower().endswith(".desktop")


@dataclass(slots=True)
class AutostartFile:
    stem: str
    path: Path
    user: bool
    values: dict[str, str]
    #: The system file this person's file overrides, if there is one.
    shadows: Path | None = None

    @property
    def argv(self) -> tuple[str, ...]:
        try:
            return desktop.parse_exec(self.values.get("Exec", ""))
        except ValueError:
            return ()


@dataclass(slots=True)
class AppEntry:
    desktop_id: str
    path: Path
    values: dict[str, str]

    @property
    def argv(self) -> tuple[str, ...]:
        try:
            return desktop.parse_exec(self.values.get("Exec", ""))
        except ValueError:
            return ()


def _program(argv: tuple[str, ...]) -> str:
    """The first argument that names a program rather than a launcher."""
    for argument in argv:
        if "=" in argument and not argument.startswith("/"):
            continue  # env assignments
        name = os.path.basename(argument)
        if name in GENERIC_PROGRAMS or argument.startswith("-"):
            continue
        return name
    return ""


def _flatpak_app(argv: tuple[str, ...]) -> str:
    if not argv or os.path.basename(argv[0]) != "flatpak" or "run" not in argv:
        return ""
    for argument in argv[argv.index("run") + 1:]:
        if not argument.startswith("-"):
            return argument
    return ""


def _desktops(environ) -> set[str]:
    value = environ.get("XDG_CURRENT_DESKTOP") or "GNOME"
    return {item for item in value.split(":") if item}


@dataclass
class LoginItems:
    layout: Layout
    agents: Callable[[], dict[str, AgentRecord]]
    allowed: Callable[[str], bool]
    environ: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    # -- Reading -------------------------------------------------------------

    def _folders(self) -> list[tuple[Path, bool]]:
        return [(self.layout.config_home / "autostart", True),
                *[(directory / "autostart", False) for directory in self.layout.config_dirs]]

    def files(self) -> dict[str, AutostartFile]:
        """The entry the session reads for each file name, as the generator orders them."""
        found: dict[str, AutostartFile] = {}
        for folder, user in self._folders():
            try:
                paths = sorted(folder.glob("*.desktop"))
            except OSError:
                continue
            for path in paths:
                stem = path.name[: -len(".desktop")]
                try:
                    values = desktop.read_group(path)
                except (OSError, UnicodeError, ValueError):
                    continue
                if stem in found:
                    if found[stem].user and not user and found[stem].shadows is None:
                        found[stem].shadows = path
                    continue
                found[stem] = AutostartFile(stem, path, user, values)
        return found

    def runs(self, entry: AutostartFile) -> bool:
        """Whether the session starts this entry at login."""
        values = entry.values
        if values.get("Type", "Application") != "Application":
            return False
        if values.get("Hidden", "").strip().lower() == "true":
            return False
        if values.get("X-GNOME-Autostart-enabled", "true").strip().lower() == "false":
            return False
        if values.get("X-systemd-skip", "").strip().lower() == "true":
            return False
        current = _desktops(self.environ)
        only = {item for item in values.get("OnlyShowIn", "").split(";") if item}
        if only and not only & current:
            return False
        never = {item for item in values.get("NotShowIn", "").split(";") if item}
        if never & current:
            return False
        try_exec = values.get("TryExec", "").strip()
        if try_exec and not self._executable(try_exec):
            return False
        return bool(values.get("Exec", "").strip())

    def _executable(self, name: str) -> bool:
        if name.startswith("/"):
            return os.access(name, os.X_OK)
        for directory in self.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(":"):
            if directory and os.access(os.path.join(directory, name), os.X_OK):
                return True
        return False

    def app_entry(self, desktop_id: str) -> AppEntry | None:
        if not is_desktop_id(desktop_id):
            return None
        for directory in (self.layout.data_home, *self.layout.data_dirs):
            path = directory / "applications" / f"{desktop_id}.desktop"
            if path.is_file():
                try:
                    values = desktop.read_group(path)
                except (OSError, UnicodeError, ValueError):
                    return None
                return AppEntry(desktop_id, path, values)
        return None

    def _program_owners(self) -> dict[str, set[str]]:
        """Which installed apps run each program, so a shared launcher never ties two apps together."""
        stamp = []
        directories = [directory / "applications" for directory in (self.layout.data_home, *self.layout.data_dirs)]
        for directory in directories:
            try:
                stamp.append((str(directory), directory.stat().st_mtime_ns))
            except OSError:
                stamp.append((str(directory), 0))
        cached = getattr(self, "_owners_cache", None)
        if cached and cached[0] == stamp:
            return cached[1]
        owners: dict[str, set[str]] = {}
        seen: set[str] = set()
        for directory in directories:
            try:
                paths = sorted(directory.glob("*.desktop"))
            except OSError:
                continue
            for path in paths:
                desktop_id = path.name[: -len(".desktop")]
                if desktop_id in seen:
                    continue
                seen.add(desktop_id)
                try:
                    values = desktop.read_group(path)
                    argv = desktop.parse_exec(values.get("Exec", ""))
                except (OSError, UnicodeError, ValueError):
                    continue
                program = _program(argv)
                if program:
                    owners.setdefault(program, set()).add(desktop_id)
        object.__setattr__(self, "_owners_cache", (stamp, owners))
        return owners

    def _matches(self, entry: AutostartFile, desktop_id: str, app: AppEntry | None) -> bool:
        values = entry.values
        if entry.stem == desktop_id:
            return True
        if desktop_id in {values.get("X-Flatpak", ""), values.get("X-XDP-Autostart", ""), values.get(APP_KEY, "")}:
            return True
        if app is None:
            return False
        flatpak = app.values.get("X-Flatpak", "") or _flatpak_app(app.argv)
        if flatpak:
            return _flatpak_app(entry.argv) == flatpak
        program = _program(app.argv)
        if not program or _program(entry.argv) != program or _flatpak_app(entry.argv):
            return False
        # Only a program no other installed app runs says which app an entry starts.
        return self._program_owners().get(program, {desktop_id}) <= {desktop_id}

    def _agent_for(self, desktop_id: str, entry: AutostartFile) -> AgentRecord | None:
        """The background agent that runs in this entry's place, if one does."""
        for record in self.agents().values():
            try:
                unit = ids.autostart_file_unit(entry.stem)
            except ids.InvalidIdentifier:
                return None
            if unit in record.autostart_units:
                return record
            if record.app_id == desktop_id and entry.values.get("X-XDP-Autostart", "") == desktop_id:
                return record
        return None

    def describe(self, desktop_id: str) -> dict:
        if not is_desktop_id(desktop_id):
            raise LoginItemError(f"not a desktop file name: {desktop_id!r}")
        app = self.app_entry(desktop_id)
        name = (app.values.get("Name", "") if app else "") or desktop_id
        sources: list[str] = []
        entries: list[str] = []
        enabled = False
        agent_record: AgentRecord | None = None
        present = False
        for entry in self.files().values():
            if not self._matches(entry, desktop_id, app):
                continue
            present = True
            record = self._agent_for(desktop_id, entry)
            if record is not None:
                # The agent runs instead; for a portal entry that is the login start.
                if entry.values.get("X-XDP-Autostart", "") == record.app_id:
                    agent_record = record
                continue
            entries.append(str(entry.path))
            if self.runs(entry):
                enabled = True
                sources.append("autostart-user" if entry.user else "autostart-system")
        if agent_record is not None and not entries:
            allowed = self.allowed(agent_record.app_id)
            return {
                "app-id": desktop_id, "name": name, "enabled": allowed, "sources": ["portal"] if allowed else [],
                "controlled-by": "background", "agent": agent_record.app_id, "entries": [],
                "available": True,
            }
        return {
            "app-id": desktop_id, "name": name, "enabled": enabled, "sources": sources,
            "controlled-by": "autostart", "agent": agent_record.app_id if agent_record else "",
            "entries": entries, "available": app is not None or present,
        }

    def list(self) -> list[dict]:
        ids_seen: set[str] = set()
        for entry in self.files().values():
            candidate = entry.values.get(APP_KEY) or entry.values.get("X-Flatpak") \
                or entry.values.get("X-XDP-Autostart") or entry.stem
            if is_desktop_id(candidate):
                ids_seen.add(candidate)
        items = [self.describe(item) for item in sorted(ids_seen)]
        return [item for item in items if item["enabled"]]

    # -- Writing -------------------------------------------------------------

    def set_enabled(self, desktop_id: str, enabled: bool, *,
                    set_agent_allowed: Callable[[str, bool], None]) -> dict:
        current = self.describe(desktop_id)
        if current["controlled-by"] == "background":
            set_agent_allowed(current["agent"], enabled)
            return self.describe(desktop_id)
        app = self.app_entry(desktop_id)
        matching = [entry for entry in self.files().values()
                    if self._matches(entry, desktop_id, app) and self._agent_for(desktop_id, entry) is None]
        user_folder = self.layout.config_home / "autostart"
        if enabled:
            if not matching:
                if app is None:
                    raise LoginItemError(f"{desktop_id} has no desktop entry to start")
                self._write(user_folder / f"{desktop_id}.desktop", self._entry_from_app(app))
            for entry in matching:
                if entry.user and entry.values.get(OVERRIDE_KEY, "").lower() == "true" and entry.shadows:
                    # Our override of a system entry: removing it restores the system's.
                    entry.path.unlink(missing_ok=True)
                    continue
                if not self.runs(entry):
                    lines = self._lines(entry.path)
                    lines = self._set(lines, {"Hidden": "false", "X-GNOME-Autostart-enabled": "true"})
                    self._write(user_folder / entry.path.name, lines)
        else:
            for entry in matching:
                if not self.runs(entry):
                    continue
                lines = self._lines(entry.path)
                updates = {"Hidden": "true"}
                if not entry.user:
                    updates[OVERRIDE_KEY] = "true"
                self._write(user_folder / entry.path.name, self._set(lines, updates))
        return self.describe(desktop_id)

    @staticmethod
    def _lines(path: Path) -> list[str]:
        return path.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def _set(lines: list[str], updates: dict[str, str]) -> list[str]:
        """Set keys in [Desktop Entry], keeping every other line as it was."""
        out: list[str] = []
        group = None
        pending = dict(updates)
        entry_seen = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if group == "Desktop Entry":
                    out.extend(f"{key}={value}" for key, value in pending.items())
                    pending = {}
                group = stripped[1:-1]
                entry_seen = entry_seen or group == "Desktop Entry"
                out.append(line)
                continue
            if group == "Desktop Entry" and "=" in line:
                key = line.split("=", 1)[0].strip()
                if key in updates:
                    if key in pending:
                        out.append(f"{key}={pending.pop(key)}")
                    continue
            out.append(line)
        if group == "Desktop Entry":
            out.extend(f"{key}={value}" for key, value in pending.items())
        elif not entry_seen:
            out = ["[Desktop Entry]", *[f"{key}={value}" for key, value in updates.items()], *out]
        return out

    @staticmethod
    def _entry_from_app(app: AppEntry) -> list[str]:
        values = app.values
        lines = ["[Desktop Entry]", "Type=Application"]
        for key in ("Name", "Icon", "Exec", "TryExec", "Path", "X-Flatpak", "X-SnapInstanceName"):
            if values.get(key):
                raw = values[key].replace("\\", "\\\\").replace("\n", "\\n")
                lines.append(f"{key}={raw}")
        lines += [f"{APP_KEY}={app.desktop_id}", "X-GNOME-Autostart-enabled=true"]
        return lines

    @staticmethod
    def _write(path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines) + "\n")
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
