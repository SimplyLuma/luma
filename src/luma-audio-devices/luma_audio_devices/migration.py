# SPDX-License-Identifier: MPL-2.0
"""Retire per-account stopgaps that luma-audio-policy now covers system-wide.

Before luma-audio-policy existed, some accounts received
~/.config/pipewire/pipewire.conf.d/10-luma-airplay-opt-in.conf, which sets
module.raop = false for that account only. The package's
/usr/share/pipewire/pipewire.conf.d/40-luma-network-audio-opt-in.conf sets
the same property for everyone, so the per-account copy is removed, but only
when it is byte-for-byte the file Luma wrote and the system fragment is
really installed. An edited copy is the person's own configuration and stays.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

__all__ = ("remove_temporary_airplay_override", "TEMPORARY_OVERRIDE_SHA256")

log = logging.getLogger("luma-audio-devices")

TEMPORARY_OVERRIDE_NAME = "10-luma-airplay-opt-in.conf"
TEMPORARY_OVERRIDE_SHA256 = "87f36a7ac31242f3af6ea251f1ba0671f79b70303d3fb76952dd1a238da41cb0"
SYSTEM_FRAGMENT = Path("/usr/share/pipewire/pipewire.conf.d/40-luma-network-audio-opt-in.conf")


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def remove_temporary_airplay_override(config_home: Path | None = None,
                                      system_fragment: Path = SYSTEM_FRAGMENT) -> str:
    """Returns "removed", "kept-modified", "kept-no-system-fragment" or "absent"."""
    path = (config_home or _config_home()) / "pipewire" / "pipewire.conf.d" / TEMPORARY_OVERRIDE_NAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return "absent"
    except OSError as error:
        log.info("cannot read %s: %s", path, error)
        return "absent"
    if hashlib.sha256(data).hexdigest() != TEMPORARY_OVERRIDE_SHA256:
        log.info("%s differs from the file Luma installed; leaving it alone", path)
        return "kept-modified"
    if not system_fragment.is_file():
        return "kept-no-system-fragment"
    try:
        path.unlink()
    except OSError as error:
        log.warning("cannot remove %s: %s", path, error)
        return "kept-modified"
    log.info("removed %s; %s now covers every account", path, system_fragment)
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return "removed"
