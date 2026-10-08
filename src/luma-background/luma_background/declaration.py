# SPDX-License-Identifier: MPL-2.0
"""The [background] table of an installed luma-app.toml, validated.

These are the same rules `luma lint` applies (tests/luma-background shares a
fixture corpus with the SDK so the two cannot drift), but this copy is the one
that decides what runs, so it is strict, self-contained, and never guesses.
"""

from __future__ import annotations

import re
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ids
from .categories import CATEGORIES, WAKE_EVENTS

MAX_DECLARATION_BYTES = 256 * 1024
MAX_ARGUMENTS = 32
MAX_ARGUMENT_LENGTH = 256
MIN_INTERVAL_SECONDS = 5 * 60
MAX_INTERVAL_SECONDS = 7 * 24 * 3600
_INTERVAL = re.compile(r"^([1-9][0-9]{0,4})([mhd])$")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}

#: Characters that mean something to a shell, to systemd's ExecStart parser,
#: or to a desktop entry. The command is never given to a shell, but refusing
#: them keeps a declaration unambiguous wherever it is read.
_FORBIDDEN = set("%$`;|&<>\\\n\r\t\"'(){}*?[]~#!")


class DeclarationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True, slots=True)
class Declaration:
    app_id: str
    agent: str
    argv: tuple[str, ...]
    category: str
    wake: tuple[str, ...]
    interval_seconds: int = 0
    publishes: tuple[str, ...] = ()
    name: str = ""
    source: str = ""


def parse_interval(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError("background.interval must be a string such as '15m'")
    match = _INTERVAL.fullmatch(value.strip())
    if not match:
        raise ValueError("background.interval must be a number followed by m, h or d")
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if seconds < MIN_INTERVAL_SECONDS:
        raise ValueError("background.interval must be at least 5m")
    if seconds > MAX_INTERVAL_SECONDS:
        raise ValueError("background.interval must be at most 7d")
    return seconds


def parse_command(value: Any, field_name: str = "background.exec") -> tuple[str, ...]:
    errors = command_errors(value, field_name)
    if errors:
        raise DeclarationError(errors)
    return tuple(shlex.split(value))


def command_errors(value: Any, field_name: str = "background.exec") -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{field_name} is required"]
    if any(character in _FORBIDDEN for character in value):
        return [f"{field_name} may not contain quoting, shell syntax, specifiers or field codes"]
    if not value.isprintable():
        return [f"{field_name} may not contain control characters"]
    argv = value.split()
    errors = []
    if len(argv) > MAX_ARGUMENTS:
        errors.append(f"{field_name} may have at most {MAX_ARGUMENTS} arguments")
    if any(len(argument) > MAX_ARGUMENT_LENGTH for argument in argv):
        errors.append(f"{field_name} arguments may be at most {MAX_ARGUMENT_LENGTH} characters")
    program = argv[0]
    if "=" in program:
        errors.append(f"{field_name} may not begin with an environment assignment")
    if program.startswith("-"):
        errors.append(f"{field_name} must begin with a program")
    return errors


def validate(data: dict[str, Any], *, expected_app_id: str | None = None) -> list[str]:
    """Every problem with a parsed luma-app.toml's [background] table."""

    errors: list[str] = []
    application = data.get("application")
    if not isinstance(application, dict):
        return ["application table is required"]
    app_id = application.get("id")
    if not ids.is_app_id(app_id):
        return ["application.id must be a valid application ID"]
    if expected_app_id is not None and app_id != expected_app_id:
        errors.append("application.id must match the declaration's file name")
    background = data.get("background")
    if not isinstance(background, dict):
        return errors + ["background table is required"]

    known = {"agent", "exec", "category", "wake", "interval", "publishes"}
    for key in sorted(set(background) - known):
        errors.append(f"background.{key} is not a known key")

    if not ids.is_agent_name(background.get("agent"), app_id):
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
            parse_interval(background["interval"])
        except ValueError as error:
            errors.append(str(error))
        if "schedule" not in wake:
            errors.append("background.interval requires 'schedule' in background.wake")

    publishes = background.get("publishes", [])
    if not isinstance(publishes, list) or not all(isinstance(item, str) for item in publishes):
        errors.append("background.publishes must be an array of strings")
    else:
        for item in publishes:
            if not (ids.VALUE_NAME.fullmatch(item) or ids.LIVE_EXTENSION_PUBLICATION.fullmatch(item)):
                errors.append(f"background.publishes contains an invalid name: {item}")
        if len(publishes) != len(set(publishes)):
            errors.append("background.publishes must not contain duplicates")
    return errors


def from_mapping(data: dict[str, Any], *, source: str = "",
                 expected_app_id: str | None = None) -> Declaration:
    errors = validate(data, expected_app_id=expected_app_id)
    if errors:
        raise DeclarationError(errors)
    application = data["application"]
    background = data["background"]
    return Declaration(
        app_id=application["id"],
        agent=background["agent"],
        argv=parse_command(background["exec"]),
        category=background["category"],
        wake=tuple(background["wake"]),
        interval_seconds=parse_interval(background["interval"]) if "interval" in background else 0,
        publishes=tuple(background.get("publishes", ())),
        name=str(application.get("name", "") or "")[:80],
        source=source,
    )


def load(path: Path) -> Declaration:
    """Read one installed declaration. The file name is the application ID."""

    expected = path.name[: -len(".toml")] if path.name.endswith(".toml") else ""
    if not ids.is_app_id(expected):
        raise DeclarationError([f"declaration file name is not an application ID: {path.name}"])
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_DECLARATION_BYTES + 1)
    except OSError as error:
        raise DeclarationError([f"cannot read declaration: {error.strerror}"]) from error
    if len(raw) > MAX_DECLARATION_BYTES:
        raise DeclarationError(["declaration is too large"])
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise DeclarationError([f"declaration is not valid TOML: {error}"]) from error
    return from_mapping(data, source=str(path), expected_app_id=expected)
