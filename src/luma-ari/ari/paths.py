# SPDX-License-Identifier: Apache-2.0
"""Where Ari keeps what she keeps. All of it is the person's, under their home."""
from __future__ import annotations

import os
from pathlib import Path


def _base(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable) or Path.home() / fallback)


def data_dir() -> Path:
    path = _base("XDG_DATA_HOME", ".local/share") / "luma" / "ari"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    path = _base("XDG_CONFIG_HOME", ".config") / "luma" / "ari"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path / "config.json"


def database_path() -> Path:
    return data_dir() / "ari.db"


def audit_path() -> Path:
    return data_dir() / "activity.jsonl"


def share_dir() -> Path:
    installed = Path("/usr/share/luma-ari")
    return installed if installed.exists() else Path(__file__).resolve().parents[1] / "data"


def runtime_binary() -> Path | None:
    for candidate in (os.environ.get("ARI_LLAMA_SERVER"), "/usr/libexec/luma-ari/llama-server"):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None
