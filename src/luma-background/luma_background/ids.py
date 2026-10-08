# SPDX-License-Identifier: MPL-2.0
"""Identifiers, and the unit names derived from them.

Every name that reaches systemd, D-Bus or the file system is built here from
an identifier that has already been validated here. Nothing else composes a
unit name, so nothing else can smuggle a path, a specifier or a second unit
into one.
"""

from __future__ import annotations

import re

MAX_ID_LENGTH = 255

# Flatpak's application ID grammar, which is also a valid D-Bus well-known
# name: at least three dot-separated elements of [A-Za-z0-9_], none starting
# with a digit, and '-' allowed only in the last element.
_APP_ID = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
    r"\.[A-Za-z_][A-Za-z0-9_-]*$"
)
# D-Bus well-known name grammar, for the agent's bus name.
_BUS_NAME = re.compile(r"^[A-Za-z_-][A-Za-z0-9_-]*(?:\.[A-Za-z_-][A-Za-z0-9_-]*)+$")
SCHEDULE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
VALUE_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
LIVE_EXTENSION_PUBLICATION = re.compile(r"^live-extension:([A-Za-z0-9_.-]{1,128})$")

UNIT_PREFIX = "app-"
AGENT_SUFFIX = "-agent"
# The names an autostart file may have for its generated unit to be masked:
# letters, digits, '_', '.' and '-', which is every name Luma and the portal write.
_AUTOSTART_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}$")


class InvalidIdentifier(ValueError):
    pass


def is_app_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_ID_LENGTH
        and value.isascii()
        and _APP_ID.fullmatch(value) is not None
        and not value.lower().endswith(".desktop")
    )


def app_id(value: object) -> str:
    if not is_app_id(value):
        raise InvalidIdentifier(f"not a valid application ID: {value!r}")
    return value  # type: ignore[return-value]


def is_agent_name(value: object, owner: str) -> bool:
    """An agent's bus name must live under its application's ID.

    That is what lets a Flatpak app own it at all, and it stops one app from
    declaring an agent that answers to another app's name.
    """
    return (
        isinstance(value, str)
        and value.isascii()
        and len(value) <= MAX_ID_LENGTH
        and _BUS_NAME.fullmatch(value) is not None
        and value.startswith(owner + ".")
        and len(value) > len(owner) + 1
    )


def schedule_name(value: object) -> str:
    if not isinstance(value, str) or SCHEDULE_NAME.fullmatch(value) is None:
        raise InvalidIdentifier(f"not a valid schedule name: {value!r}")
    return value


def systemd_escape(value: str) -> str:
    """systemd-escape(1) for the characters an application ID may contain.

    Only '-' needs it: it is the one character in the ID grammar that systemd
    reads as structure. Anything else would already have failed validation,
    and is refused here rather than escaped so a mistake cannot pass silently.
    """
    out = []
    for character in value:
        if character.isalnum() or character in "_.":
            out.append(character)
        elif character == "-":
            out.append("\\x2d")
        else:
            raise InvalidIdentifier(f"unexpected character in identifier: {character!r}")
    return "".join(out)


def systemd_unescape(value: str) -> str:
    return re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), value)


def agent_unit(app: str) -> str:
    return f"{UNIT_PREFIX}{systemd_escape(app_id(app))}{AGENT_SUFFIX}.service"


def schedule_timer(app: str, name: str) -> str:
    return f"{UNIT_PREFIX}{systemd_escape(app_id(app))}{AGENT_SUFFIX}-{schedule_name(name)}.timer"


def schedule_service(app: str, name: str) -> str:
    return f"{UNIT_PREFIX}{systemd_escape(app_id(app))}{AGENT_SUFFIX}-{schedule_name(name)}.service"


def autostart_unit(app: str) -> str:
    """The unit systemd-xdg-autostart-generator makes from an autostart file."""
    return f"{UNIT_PREFIX}{systemd_escape(app_id(app))}@autostart.service"


def autostart_file_unit(stem: str) -> str:
    """The generator's unit for `<stem>.desktop`, whatever app it belongs to.

    systemd-xdg-autostart-generator names it app-<unit_name_escape(stem)>@autostart.service;
    for the characters allowed here that escaping only rewrites '-'.
    """
    if not isinstance(stem, str) or _AUTOSTART_FILE.fullmatch(stem) is None:
        raise InvalidIdentifier(f"not an autostart file name that can be masked: {stem!r}")
    return f"{UNIT_PREFIX}{systemd_escape(stem)}@autostart.service"


_AGENT_UNIT = re.compile(r"^app-(?P<app>[A-Za-z0-9_.\\]+?)-agent\.service$")
_SCHEDULE_UNIT = re.compile(
    r"^app-(?P<app>[A-Za-z0-9_.\\]+?)-agent-(?P<name>[a-z0-9][a-z0-9-]{0,39})\.service$"
)


def parse_agent_unit(unit: str) -> str | None:
    match = _AGENT_UNIT.fullmatch(unit)
    if not match:
        return None
    candidate = systemd_unescape(match.group("app"))
    return candidate if is_app_id(candidate) else None


def parse_schedule_unit(unit: str) -> tuple[str, str] | None:
    match = _SCHEDULE_UNIT.fullmatch(unit)
    if not match:
        return None
    candidate = systemd_unescape(match.group("app"))
    if not is_app_id(candidate):
        return None
    return candidate, match.group("name")
