# SPDX-License-Identifier: Apache-2.0
"""`luma lint` rules for the [background] table (ADR-033).

luma-background enforces the same rules on the installed declaration; both
are tested against tests/luma-background/corpus so the messages a developer
sees and the decision the system makes cannot disagree.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

CATEGORIES = ("communication", "calendar", "alarms", "mail", "sync", "widget-data", "other")
WAKE_EVENTS = ("login", "network", "resume", "schedule")
KNOWN_KEYS = {"agent", "exec", "category", "wake", "interval", "publishes"}
MAX_ARGUMENTS = 32
MAX_ARGUMENT_LENGTH = 256
_FORBIDDEN = set("%$`;|&<>\\\n\r\t\"'(){}*?[]~#!")
_BUS_NAME = re.compile(r"^[A-Za-z_-][A-Za-z0-9_-]*(?:\.[A-Za-z_-][A-Za-z0-9_-]*)+$")
_VALUE_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_LIVE_EXTENSION = re.compile(r"^live-extension:[A-Za-z0-9_.-]{1,128}$")
_INTERVAL = re.compile(r"^([1-9][0-9]{0,4})([mhd])$")
_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def interval_seconds(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError("background.interval must be a string such as '15m'")
    match = _INTERVAL.fullmatch(value.strip())
    if not match:
        raise ValueError("background.interval must be a number followed by m, h or d")
    seconds = int(match.group(1)) * _SECONDS[match.group(2)]
    if seconds < 300:
        raise ValueError("background.interval must be at least 5m")
    if seconds > 7 * 86400:
        raise ValueError("background.interval must be at most 7d")
    return seconds


def command_errors(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return ["background.exec is required"]
    if any(character in _FORBIDDEN for character in value):
        return ["background.exec may not contain quoting, shell syntax, specifiers or field codes"]
    if not value.isprintable():
        return ["background.exec may not contain control characters"]
    argv = value.split()
    errors = []
    if len(argv) > MAX_ARGUMENTS:
        errors.append(f"background.exec may have at most {MAX_ARGUMENTS} arguments")
    if any(len(argument) > MAX_ARGUMENT_LENGTH for argument in argv):
        errors.append(f"background.exec arguments may be at most {MAX_ARGUMENT_LENGTH} characters")
    if "=" in argv[0]:
        errors.append("background.exec may not begin with an environment assignment")
    if argv[0].startswith("-"):
        errors.append("background.exec must begin with a program")
    return errors


def background_errors(manifest: dict[str, Any]) -> list[str]:
    """Rules that need only the manifest itself."""

    background = manifest.get("background")
    if background is None:
        return []
    if not isinstance(background, dict):
        return ["background must be a table"]
    application = manifest.get("application")
    app_id = application.get("id", "") if isinstance(application, dict) else ""
    errors: list[str] = []
    for key in sorted(set(background) - KNOWN_KEYS):
        errors.append(f"background.{key} is not a known key")
    agent = background.get("agent")
    if not (isinstance(agent, str) and _BUS_NAME.fullmatch(agent) and len(agent) <= 255
            and isinstance(app_id, str) and app_id and agent.startswith(app_id + ".")
            and len(agent) > len(app_id) + 1):
        errors.append("background.agent must be a D-Bus name that starts with application.id and a dot")
    errors.extend(command_errors(background.get("exec")))
    if background.get("category") not in CATEGORIES:
        errors.append("background.category must be one of " + ", ".join(CATEGORIES))
    wake = background.get("wake")
    if not isinstance(wake, list) or not wake or not all(isinstance(item, str) for item in wake):
        errors.append("background.wake must be a non-empty array of strings")
        wake = []
    else:
        for item in wake:
            if item not in WAKE_EVENTS:
                errors.append(f"background.wake contains an unknown event: {item}")
        if len(wake) != len(set(wake)):
            errors.append("background.wake must not contain duplicates")
    if "interval" in background:
        try:
            interval_seconds(background["interval"])
        except ValueError as error:
            errors.append(str(error))
        if "schedule" not in wake:
            errors.append("background.interval requires 'schedule' in background.wake")
    publishes = background.get("publishes", [])
    if not isinstance(publishes, list) or not all(isinstance(item, str) for item in publishes):
        errors.append("background.publishes must be an array of strings")
    else:
        for item in publishes:
            if not (_VALUE_NAME.fullmatch(item) or _LIVE_EXTENSION.fullmatch(item)):
                errors.append(f"background.publishes contains an invalid name: {item}")
        if len(publishes) != len(set(publishes)):
            errors.append("background.publishes must not contain duplicates")
    return errors


def background_project_errors(manifest: dict[str, Any], root: Path,
                              desktop_exec: str | None, flatpak_command: str | None) -> list[str]:
    """Rules that compare the declaration with the rest of the project."""

    background = manifest.get("background")
    if not isinstance(background, dict) or not isinstance(background.get("exec"), str):
        return []
    errors: list[str] = []
    distribution = manifest.get("distribution", {})
    portals = distribution.get("portals", []) if isinstance(distribution, dict) else []
    if isinstance(portals, list) and "background" not in portals:
        errors.append("distribution.portals must include 'background' when [background] is declared")
    try:
        program = background["exec"].split()[0]
    except IndexError:
        return errors
    expected = flatpak_command
    if expected is None and desktop_exec:
        try:
            expected = shlex.split(desktop_exec)[0]
        except (ValueError, IndexError):
            expected = None
    if expected and program != expected:
        errors.append(
            f"background.exec must run the app's own command ({expected}); "
            "a Flatpak agent always runs inside the app's sandbox"
        )
    return errors
