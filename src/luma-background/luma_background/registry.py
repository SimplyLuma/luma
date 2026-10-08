# SPDX-License-Identifier: MPL-2.0
"""Which apps have background agents, found only where the system can vouch.

Three sources, each tied to an installed desktop entry:

- declarations: `<app-id>.toml` files carrying the app's [background] table,
  in the system data directories, the person's data directory, or inside an
  installed Flatpak;
- autostart entries the Background portal wrote for a sandboxed app;
- sandboxed apps the portal has recorded a background decision for.

Anything that fails a rule is left out and reported, never half-registered.

An app whose agent this service runs must not also be started by the session's
own autostart, or it runs twice (once outside every limit and switch). Every
autostart entry that stands in for a registered agent is recorded on its
record so the manager can mask the unit systemd-xdg-autostart-generator made
from it: the portal's entry for the app (`X-XDP-Autostart`, whoever wrote it)
and any entry, in the person's or the system's autostart folders, that names
the app's agent with `X-Luma-Background-Agent` -- the fallback an app ships for
sessions without this service.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable

from . import declaration as declarations
from . import desktop, ids, units
from .categories import CATEGORIES, Category

LOGIN_WAKE = ("login",)
NATIVE_SERVICE_APPS = frozenset({'org.projectluma.Messages', 'org.projectluma.Phone',
                                'org.projectluma.Calendar', 'org.projectluma.Clock'})


def native_service_role(app):
    """Only the signed OS contract can separate these UIs from native agents."""
    if app not in NATIVE_SERVICE_APPS:
        return False
    try:
        from luma_installer.native_app_roles import required
    except ImportError:
        return False  # older/non-Luma host, no native service delegation
    return required(app)


@dataclass(frozen=True, slots=True)
class AgentRecord:
    app_id: str
    name: str
    icon: str
    category: Category
    agent: str
    command: units.AgentCommand | None
    wake: tuple[str, ...]
    interval_seconds: int
    publishes: tuple[str, ...]
    origin: str                 # native | flatpak
    trusted_install: bool
    declared: bool
    portal_autostart: bool
    declaration_path: str = ""
    desktop_path: str = ""
    #: Generated autostart units that would start this app a second time.
    autostart_units: tuple[str, ...] = ()

    @property
    def unit(self) -> str:
        return ids.agent_unit(self.app_id) if self.command else ""

    @property
    def has_unit(self) -> bool:
        return self.command is not None


@dataclass(slots=True)
class ScanResult:
    agents: dict[str, AgentRecord] = field(default_factory=dict)
    problems: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Layout:
    """Where to look. Tests point this at a temporary tree."""

    data_dirs: tuple[Path, ...]
    data_home: Path
    config_home: Path
    home: Path
    flatpak_system: Path = Path("/var/lib/flatpak")
    #: $XDG_CONFIG_DIRS, whose autostart folders the session reads after the person's.
    config_dirs: tuple[Path, ...] = (Path("/etc/xdg"),)

    @classmethod
    def from_environment(cls, environ=None) -> "Layout":
        environ = os.environ if environ is None else environ
        home = Path(environ.get("HOME") or Path.home())
        config_dirs = tuple(Path(item) for item in (environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
                            if item.startswith("/"))
        return cls(
            data_dirs=tuple(desktop.data_dirs(environ)),
            data_home=desktop.data_home(environ),
            config_home=desktop.config_home(environ),
            home=home,
            config_dirs=config_dirs or (Path("/etc/xdg"),),
        )

    @property
    def flatpak_user(self) -> Path:
        return self.data_home / "flatpak"

    def environ(self) -> dict[str, str]:
        return {
            "XDG_DATA_DIRS": ":".join(str(item) for item in self.data_dirs),
            "XDG_DATA_HOME": str(self.data_home),
            "XDG_CONFIG_HOME": str(self.config_home),
            "XDG_CONFIG_DIRS": ":".join(str(item) for item in self.config_dirs),
            "HOME": str(self.home),
        }

    def watch_directories(self) -> list[Path]:
        paths = [directory / "luma/background" for directory in self.data_dirs]
        paths += [directory / "applications" for directory in self.data_dirs]
        paths += [
            self.data_home / "luma/background",
            self.data_home / "applications",
            self.config_home / "autostart",
            self.flatpak_system / "app",
            self.flatpak_user / "app",
        ]
        paths += [directory / "autostart" for directory in self.config_dirs]
        return paths


class Registry:
    def __init__(self, layout: Layout, *,
                 is_system_path: Callable[[Path], bool] = desktop.is_system_path) -> None:
        self.layout = layout
        self._is_system_path = is_system_path

    # -- Sources -----------------------------------------------------------

    def _system_declarations(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        # XDG order: the first directory that has a file wins.
        for directory in self.layout.data_dirs:
            for path in sorted((directory / "luma/background").glob("*.toml")):
                found.setdefault(path.stem, path)
        return found

    def _user_declarations(self) -> dict[str, Path]:
        return {path.stem: path for path in sorted((self.layout.data_home / "luma/background").glob("*.toml"))}

    def _flatpak_candidates(self) -> set[str]:
        found: set[str] = set()
        for root in (self.layout.flatpak_system, self.layout.flatpak_user):
            for path in (root / "app").glob("*/current/active/files/share/luma/background/*.toml"):
                app = path.stem
                if path.parents[6].name == app:
                    found.add(app)
        return found

    def _flatpak_declaration(self, entry: desktop.DesktopEntry) -> Path | None:
        roots = [self.layout.flatpak_user, self.layout.flatpak_system]
        if entry.system:
            roots.reverse()
        for root in roots:
            candidate = root / "app" / entry.app_id / "current/active/files/share/luma/background" / f"{entry.app_id}.toml"
            if candidate.is_file():
                return candidate
        return None

    def _autostart_files(self) -> dict[str, tuple[Path, dict[str, str]]]:
        """The autostart entries the session would run, by file name.

        As systemd-xdg-autostart-generator reads them: the person's folder
        first, then $XDG_CONFIG_DIRS in order, the first file of a name
        replacing the rest; a hidden entry starts nothing.
        """
        found: dict[str, tuple[Path, dict[str, str]]] = {}
        seen: set[str] = set()
        for directory in (self.layout.config_home, *self.layout.config_dirs):
            for path in sorted((directory / "autostart").glob("*.desktop")):
                stem = path.name[: -len(".desktop")]
                if stem in seen:
                    continue
                seen.add(stem)
                try:
                    values = desktop.read_group(path)
                except (OSError, UnicodeError, ValueError):
                    continue
                if values.get("Hidden", "").lower() == "true":
                    continue
                found[stem] = (path, values)
        return found

    def _autostart_entries(self, files: dict[str, tuple[Path, dict[str, str]]],
                           problems: list[tuple[str, str]]) -> dict[str, tuple[str, ...]]:
        """Portal autostart entries, which the portal writes to the person's folder only."""
        found: dict[str, tuple[str, ...]] = {}
        user_folder = self.layout.config_home / "autostart"
        for stem, (path, values) in files.items():
            app = values.get("X-XDP-Autostart", "")
            if not app or path.parent != user_folder:
                continue
            if stem != app or not ids.is_app_id(app) or values.get("X-Flatpak", app) != app:
                problems.append((str(path), "portal autostart entry does not match its app"))
                continue
            try:
                found[app] = desktop.parse_exec(values.get("Exec", ""))
            except ValueError:
                problems.append((app, "portal autostart entry has an unreadable Exec"))
        return found

    @staticmethod
    def _replaced_autostart(record: "AgentRecord", files: dict[str, tuple[Path, dict[str, str]]],
                            problems: list[tuple[str, str]]) -> tuple[str, ...]:
        names: set[str] = set()
        for stem, (path, values) in files.items():
            portal_entry = stem == record.app_id and values.get("X-XDP-Autostart", "") == record.app_id
            names_agent = bool(record.agent) and values.get("X-Luma-Background-Agent", "") == record.agent
            if not (portal_entry or names_agent):
                continue
            try:
                names.add(ids.autostart_file_unit(stem))
            except ids.InvalidIdentifier:
                problems.append((str(path), "autostart entry for a background agent has a name that cannot be masked"))
        return tuple(sorted(names))

    def _entry(self, app: str) -> desktop.DesktopEntry | None:
        entry = None
        for directory in [self.layout.data_home, *self.layout.data_dirs]:
            candidate = directory / "applications" / f"{app}.desktop"
            if candidate.is_file():
                try:
                    loaded = desktop.load(candidate)
                except (OSError, UnicodeError, ValueError):
                    return None
                entry = loaded if not loaded.hidden else None
                break
        if entry is None:
            return None
        system = self._is_system_path(Path(entry.path))
        if system != entry.system:
            entry = desktop.DesktopEntry(entry.app_id, entry.path, entry.name, entry.icon,
                                         entry.exec_argv, entry.flatpak, system, entry.hidden)
        return entry

    def _native_service_entry(self, app: str) -> desktop.DesktopEntry | None:
        # A same-ID exported Flatpak entry owns the UI, never the host's modem,
        # mailbox keys or alarm scheduler. Resolve only trusted host entries.
        for directory in self.layout.data_dirs:
            candidate = directory / 'applications' / f'{app}.desktop'
            if not candidate.is_file() or not self._is_system_path(candidate):
                continue
            entry = desktop.load(candidate)
            if entry.hidden or entry.is_flatpak:
                continue
            return replace(entry, system=True)
        return None

    # -- Scan --------------------------------------------------------------

    def scan(self, portal_apps: Iterable[str] = ()) -> ScanResult:
        result = ScanResult()
        system = self._system_declarations()
        user = self._user_declarations()
        autostart_files = self._autostart_files()
        autostart = self._autostart_entries(autostart_files, result.problems)
        candidates = sorted(set(system) | set(user) | set(autostart) | set(portal_apps)
                            | self._flatpak_candidates())
        for app in candidates:
            if not ids.is_app_id(app):
                result.problems.append((app, "not a valid application ID"))
                continue
            try:
                host_service = native_service_role(app)
                entry = self._native_service_entry(app) if host_service else self._entry(app)
                if host_service and (system.get(app) is None or not self._is_system_path(system[app])):
                    raise ValueError('native service needs its trusted host declaration')
            except (OSError, UnicodeError, ValueError) as error:
                result.problems.append((app, str(error)))
                continue
            if entry is None:
                result.problems.append((app, "no installed desktop entry"))
                continue
            try:
                record = self._record(app, entry, system.get(app), user.get(app),
                                      autostart.get(app), app in set(portal_apps))
            except (declarations.DeclarationError, units.CommandRefused, ValueError) as error:
                result.problems.append((app, str(error)))
                continue
            if record is not None:
                if record.has_unit:
                    record = replace(record, autostart_units=self._replaced_autostart(
                        record, autostart_files, result.problems))
                result.agents[app] = record
        return result

    def _record(self, app: str, entry: desktop.DesktopEntry, system_path: Path | None,
                user_path: Path | None, autostart_argv: tuple[str, ...] | None,
                portal_known: bool) -> AgentRecord | None:
        declared: declarations.Declaration | None = None
        declaration_path: Path | None = None
        if entry.is_flatpak:
            declaration_path = self._flatpak_declaration(entry)
        elif system_path is not None:
            declaration_path = system_path
        elif user_path is not None:
            declaration_path = user_path
        if declaration_path is not None:
            declared = declarations.load(declaration_path)

        if declared is None and entry.is_flatpak is False:
            # Native apps have no portal; without a declaration they have no agent.
            if autostart_argv is not None or portal_known:
                raise ValueError("only sandboxed apps can use the Background portal")
            raise ValueError("declaration could not be read")

        trusted = entry.system and (
            declaration_path is None or self._is_system_path(declaration_path)
        )
        if entry.is_flatpak:
            origin = "flatpak"
            if declared is not None:
                command = units.flatpak_command(app, declared.argv, entry)
            elif autostart_argv is not None:
                command = units.portal_autostart_command(app, autostart_argv)
            else:
                command = None
        else:
            origin = "native"
            command = units.native_command(
                declared.argv, entry,  # type: ignore[union-attr]
                declaration_is_system=declaration_path is not None and self._is_system_path(declaration_path),
                home=self.layout.home,
            )

        wake = tuple(declared.wake) if declared else ()
        if autostart_argv is not None and "login" not in wake:
            wake = wake + LOGIN_WAKE
        return AgentRecord(
            app_id=app,
            name=entry.name,
            icon=entry.icon,
            category=CATEGORIES[declared.category if declared else "other"],
            agent=declared.agent if declared else "",
            command=command,
            wake=wake,
            interval_seconds=declared.interval_seconds if declared else 0,
            publishes=declared.publishes if declared else (),
            origin=origin,
            trusted_install=trusted,
            declared=declared is not None,
            portal_autostart=autostart_argv is not None,
            declaration_path=str(declaration_path or ""),
            desktop_path=entry.path,
        )
