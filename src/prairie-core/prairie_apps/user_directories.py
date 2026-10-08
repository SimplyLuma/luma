# SPDX-License-Identifier: Apache-2.0
"""Shared XDG media-directory lookup; never execute user configuration."""
from __future__ import annotations
import os
from pathlib import Path
import re
from collections.abc import Mapping


def photos_directory(environment: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    home = Path(env.get("HOME", str(Path.home())))
    value = env.get("XDG_PICTURES_DIR", "").strip()
    if not value:
        variable = "HOST_XDG_CONFIG_HOME" if env.get("FLATPAK_ID") in {
            "org.projectluma.Photos", "org.projectluma.Camera"} else "XDG_CONFIG_HOME"
        config = Path(env.get(variable) or str(home / ".config")) / "user-dirs.dirs"
        try:
            for line in config.read_text().splitlines():
                match = re.fullmatch(r'\s*XDG_PICTURES_DIR="((?:[^"\\]|\\.)*)"\s*', line)
                if match:
                    value = re.sub(r'\\([\\"`$])', r'\1', match[1])
                    break
        except FileNotFoundError:
            pass
    if value:
        if value == "$HOME" or value.startswith("$HOME/"):
            value = str(home) + value[5:]
        if Path(value).is_absolute():
            return Path(value)
    # Existing unconfigured libraries stay readable until explicitly migrated.
    if not (home / "Photos").exists() and (home / "Pictures").is_dir():
        return home / "Pictures"
    return home / "Photos"
