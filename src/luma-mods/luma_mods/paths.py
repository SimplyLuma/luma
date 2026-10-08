"""Fixed per-user locations for the Luma Mod lifecycle."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import StateError


def _xdg(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name)
    path = Path(raw) if raw else fallback
    if not path.is_absolute():
        raise StateError(f"{name} must be an absolute path")
    return path


@dataclass(frozen=True)
class UserPaths:
    state_root: Path
    preference_file: Path

    @classmethod
    def current(cls) -> "UserPaths":
        home = Path.home()
        state_home = _xdg("XDG_STATE_HOME", home / ".local" / "state")
        config_home = _xdg("XDG_CONFIG_HOME", home / ".config")
        return cls(
            state_root=state_home / "luma" / "mods",
            preference_file=config_home / "luma" / "preferences.json",
        )
