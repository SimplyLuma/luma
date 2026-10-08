from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RelayConfig:
    max_package_bytes: int = 8 * 1024 * 1024 * 1024
    network_default: bool = True
    notifications_default: bool = True


def load_config(path: Path = Path("/etc/luma/relay.conf")) -> RelayConfig:
    parser = configparser.ConfigParser()
    if path.is_file():
        parser.read(path)
    section = parser["relay"] if parser.has_section("relay") else {}
    return RelayConfig(
        max_package_bytes=int(section.get("max_package_bytes", 8 * 1024**3)),
        network_default=str(section.get("network_default", "true")).lower()
        in {"1", "true", "yes", "on"},
        notifications_default=str(section.get("notifications_default", "true")).lower()
        in {"1", "true", "yes", "on"},
    )


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))


def cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def relay_root() -> Path:
    root = data_home() / "luma-relay"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def windows_apps_root() -> Path:
    root = relay_root() / "windows" / "apps"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def shared_cache_root() -> Path:
    root = cache_home() / "luma-relay" / "components"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root
