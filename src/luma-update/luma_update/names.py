# SPDX-License-Identifier: Apache-2.0
"""What a Luma release is called, for people.

Machine versions (``1.0.0-nightly.20260917.5``, ``1.0.0-beta.1.1``, ``1.0.0``)
are the only thing ever compared or acted on; nothing here changes them. A
display name ("Luma (Prairie, Beta 0, Nightly 20260916)") is presentation only.

The booted system's name is its own ``/usr/lib/os-release`` PRETTY_NAME. A
version that is not booted is named, in order, by:

1. ``display_name`` of the signed graph release with that version;
2. that deployment's own os-release PRETTY_NAME, when it is on disk;
3. a name derived from the version with the booted system's codename and stage;
4. "Luma <version>".

An os-release that names no build (``ID=luma`` without VERSION_CODENAME or
IMAGE_VERSION: an image from before release names, or luma-release's stage-only
file) is not used as a name: the graph, else step 3, names it instead.
Pure functions, no GLib, so the rules are unit-tested without a system.
"""

from __future__ import annotations

from pathlib import Path
import re

__all__ = ("MAX_NAME", "OS_RELEASE", "clean", "parse_os_release", "read_os_release",
           "deployment_os_release", "pretty_name", "derive", "display_name", "booted_name")

MAX_NAME = 200
OS_RELEASE = "/usr/lib/os-release"
DEFAULT_CODENAME = "prairie"
DEFAULT_STAGE = "beta"
DEFAULT_STAGE_NUMBER = "0"

_NUMBER = r"(0|[1-9]\d{0,8})"
_NIGHTLY = re.compile(_NUMBER + r"\." + _NUMBER + r"\." + _NUMBER + r"-nightly\.(\d{8})(?:\.\d{1,9})?\Z")
_BETA = re.compile(_NUMBER + r"\." + _NUMBER + r"\." + _NUMBER + r"-beta\." + _NUMBER + r"(?:\." + _NUMBER + r")?\Z")
_RELEASE = re.compile(_NUMBER + r"\." + _NUMBER + r"\." + _NUMBER + r"\Z")
_CODENAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}\Z")
_STAGE_NUMBER = re.compile(r"(0|[1-9]\d{0,8})(\.(0|[1-9]\d{0,8}))?\Z")
_OSNAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")
_CHECKSUM = re.compile(r"[0-9a-f]{64}\Z")
_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


def clean(value) -> str:
    """A name fit to show: one line, at most 200 characters, else ''."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in text):
        return ""
    return text


def parse_os_release(text: str) -> dict[str, str]:
    """os-release(5) assignments. Quotes and backslash escapes are undone; nothing is evaluated."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _KEY.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = re.sub(r'\\([\\"$`])', r"\1", value)
        values[key] = value
    return values


def read_os_release(path) -> dict[str, str]:
    try:
        with open(path, "rb") as stream:
            data = stream.read(65536)
    except OSError:
        return {}
    return parse_os_release(data.decode("utf-8", errors="replace"))


def deployment_os_release(root, deployment) -> Path | None:
    """The os-release inside a deployment on disk, or None when it cannot be named safely."""
    osname = str(getattr(deployment, "osname", "") or "")
    checksum = str(getattr(deployment, "checksum", "") or "")
    serial = getattr(deployment, "serial", 0)
    if not _OSNAME.match(osname) or not _CHECKSUM.match(checksum) or isinstance(serial, bool) \
            or not isinstance(serial, int) or serial < 0:
        return None
    return Path(root) / "ostree" / "deploy" / osname / "deploy" / f"{checksum}.{serial}" / "usr/lib/os-release"


def pretty_name(info: dict | None) -> str:
    """PRETTY_NAME, unless it is missing or from an image older than release names."""
    if not info:
        return ""
    name = clean(info.get("PRETTY_NAME"))
    if not name:
        return ""
    # An os-release that names no build: from an image older than release
    # names ("Luma 1.0"), or luma-release's stage-only file ("Luma (Prairie,
    # Beta 0)") layered onto such an image. The graph names the build instead.
    if info.get("ID") == "luma" and (not info.get("VERSION_CODENAME") or not info.get("IMAGE_VERSION")):
        return ""
    return name


def _codename(info: dict) -> str:
    codename = str(info.get("VERSION_CODENAME") or "")
    if not _CODENAME.match(codename):
        codename = DEFAULT_CODENAME
    return " ".join(part.capitalize() for part in re.split(r"[._-]+", codename) if part)


def derive(version: str, booted: dict | None = None) -> str:
    """A name from the version alone, with the booted system's codename and stage."""
    text = version.strip() if isinstance(version, str) else ""
    if not text:
        return ""
    if text.lower().startswith("luma"):
        return clean(text)
    info = booted or {}
    codename = _codename(info)
    match = _NIGHTLY.match(text)
    if match:
        stage = str(info.get("LUMA_RELEASE_STAGE") or DEFAULT_STAGE)
        if stage == "final":
            return f"Luma (Version {match.group(1)}, {codename}, Nightly {match.group(4)})"
        number = str(info.get("LUMA_RELEASE_STAGE_NUMBER") or DEFAULT_STAGE_NUMBER)
        if not _STAGE_NUMBER.match(number):
            number = DEFAULT_STAGE_NUMBER
        return f"Luma ({codename}, Beta {number}, Nightly {match.group(4)})"
    match = _BETA.match(text)
    if match:
        number = match.group(4) + (f".{match.group(5)}" if match.group(5) is not None else "")
        return f"Luma ({codename}, Beta {number})"
    match = _RELEASE.match(text)
    if match:
        if match.group(2) == "0" and match.group(3) == "0":
            return f"Luma (Version {match.group(1)}, {codename})"
        return f"Luma (Version {text}, {codename})"
    return clean(f"Luma {text}")


def display_name(version: str, *, graph_name=None, os_release: dict | None = None,
                 booted: dict | None = None) -> str:
    """The name of a release that is not booted, by the fallback order above."""
    if not version:
        return ""
    return clean(graph_name) or pretty_name(os_release) or derive(version, booted)


def booted_name(booted: dict | None, version: str, *, graph_name=None) -> str:
    """The booted system's name: its own PRETTY_NAME first."""
    return pretty_name(booted) or clean(graph_name) or derive(version, booted)
