# SPDX-License-Identifier: Apache-2.0
"""Paths, tunables and the anonymous-statistics switch.

Package defaults live in ``/usr/lib/luma/update.conf``; an administrator may
override any key in ``/etc/luma/update.conf``. Every path can be redirected
with ``LUMA_UPDATE_ROOT`` so the test suite and the VM rig never touch a real
system's files.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import os
from pathlib import Path

__all__ = ("Paths", "Settings", "load_settings", "statistics_enabled", "CHANNELS", "PREVIEW_CHANNELS")

CHANNELS = ("stable", "beta", "nightly")
PREVIEW_CHANNELS = ("beta", "nightly")


@dataclass(frozen=True)
class Paths:
    root: Path = Path("/")

    @classmethod
    def from_environment(cls) -> "Paths":
        return cls(Path(os.environ.get("LUMA_UPDATE_ROOT", "/")))

    def _p(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    @property
    def state_dir(self) -> Path:
        return self._p("/var/lib/luma-update")

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def wariness_file(self) -> Path:
        return self.state_dir / "wariness"

    @property
    def runtime_dir(self) -> Path:
        return self._p("/run/luma-update")

    @property
    def status_file(self) -> Path:
        return self.runtime_dir / "status.json"

    @property
    def vendor_config(self) -> Path:
        return self._p("/usr/lib/luma/update.conf")

    @property
    def admin_config(self) -> Path:
        return self._p("/etc/luma/update.conf")

    @property
    def statistics_config(self) -> Path:
        return self._p("/etc/luma/statistics.conf")

    @property
    def preview_credential(self) -> Path:
        return self._p("/etc/luma/update-preview-credential")

    @property
    def preview_mirrorlist(self) -> Path:
        # The first images' layout: the remote's url is switched to this file while enrolled.
        return self._p("/etc/luma/update-preview-mirrorlist")

    @property
    def update_mirrorlist(self) -> Path:
        # The image's layout from 2026-09: the remote's url always names this root-only
        # mirror list, and enrollment changes only the list.
        return self._p("/etc/luma/update-mirrorlist")

    @property
    def os_release(self) -> Path:
        """The booted system's os-release: its PRETTY_NAME is what it is called."""
        return self._p("/usr/lib/os-release")

    @property
    def ostree_remotes_dir(self) -> Path:
        return self._p("/etc/ostree/remotes.d")

    @property
    def graph_key_dirs(self) -> tuple[Path, ...]:
        # /usr/share/luma/update is where the OS image installs the release key.
        return (self._p("/usr/share/luma/update"), self._p("/usr/lib/luma-update/graph-keys.d"),
                self._p("/etc/luma/update-graph-keys.d"))


@dataclass(frozen=True)
class Settings:
    graph_url: str = "https://dl.simplyluma.com/os/graph/{channel}.json"
    stable_repo_url: str = "https://dl.simplyluma.com/os/repo"
    preview_repo_url: str = "https://dl.simplyluma.com/os/preview/{credential}/repo"
    stable_remote: str = "luma"
    ref_template: str = "luma/1/{arch}/{channel}"
    events_url: str = "https://hub.simplyluma.com/api/updates/events"
    preview_credentials_url: str = "https://hub.simplyluma.com/api/updates/preview-credentials"
    automatic_download: bool = True
    minimum_battery_percent: float = 30.0
    check_interval_seconds: int = 6 * 3600
    minimum_check_spacing_seconds: int = 3600
    idle_exit_seconds: int = 300
    allow_insecure_urls: bool = False  # only for the isolated VM rig; never in an image
    extra: dict = field(default_factory=dict)

    def ref(self, channel: str, arch: str) -> str:
        return self.ref_template.format(channel=channel, arch=arch)

    def remote_for(self, channel: str) -> str:
        # One remote for every channel; preview enrollment changes where it points.
        return self.stable_remote


_BOOL = {"true": True, "yes": True, "1": True, "on": True,
         "false": False, "no": False, "0": False, "off": False}


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(path.read_text(encoding="utf-8"), source=str(path))
    except (OSError, UnicodeDecodeError, configparser.Error):
        pass
    return parser


def load_settings(paths: Paths) -> Settings:
    values: dict[str, str] = {}
    for path in (paths.vendor_config, paths.admin_config):
        parser = _read_ini(path)
        if parser.has_section("update"):
            values.update({key: value.strip() for key, value in parser.items("update")})
    defaults = Settings()
    kwargs = {}
    for name, default in defaults.__dict__.items():
        if name == "extra" or name not in values:
            continue
        raw = values[name]
        try:
            if isinstance(default, bool):
                kwargs[name] = _BOOL[raw.lower()]
            elif isinstance(default, int):
                kwargs[name] = int(raw)
            elif isinstance(default, float):
                kwargs[name] = float(raw)
            else:
                kwargs[name] = raw
        except (KeyError, ValueError):
            continue
    settings = Settings(**kwargs)
    if not settings.allow_insecure_urls:
        for name in ("graph_url", "stable_repo_url", "preview_repo_url", "events_url", "preview_credentials_url"):
            if not getattr(settings, name).startswith("https://"):
                settings = Settings(**{**settings.__dict__, name: getattr(defaults, name)})
    return settings


def statistics_enabled(paths: Paths) -> bool:
    """Anonymous statistics are on unless ``/etc/luma/statistics.conf`` says off.

    The file is shared by Luma's anonymous statistics; Settings owns the
    switch. A missing file means the default (on). A file that exists but
    cannot be read or understood means off: a damaged setting must never turn
    reporting back on for someone who switched it off.
    """
    path = paths.statistics_config
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    except (OSError, UnicodeDecodeError):
        return False
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text, source=str(path))
    except configparser.Error:
        return False
    if not parser.has_section("statistics"):
        return not text.strip()
    raw = parser.get("statistics", "enabled", fallback="true").strip().lower()
    return _BOOL.get(raw, False)
