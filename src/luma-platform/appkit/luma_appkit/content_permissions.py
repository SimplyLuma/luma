# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: permissions, one vocabulary everywhere.

Depot, the installer and every other place that says what an application can
reach use these eleven words, always in this order, each with one Lucide glyph
(Depot 8.11). The heading is "Permissions", never "Can reach".

    grants = from_reach([("mic", "Microphone and camera", "Only during a call you start or answer"),
                         ("music-2", "Your Music folder", "Reads it to build your library")])
    PermissionList(grants)          # Camera, Microphone, Files ("Music folder: reads it to build your library")

    from_keys(["files.home", "network", "devices.camera"])   # a package's technical grants, same words

What publishers send is free text with a glyph; `from_reach` maps it the way
the approved mockup does. "No account", "Nothing else" and "Sound" are not
permissions and are never listed. A grant an update adds, widens, narrows or
removes carries `change`, which the list shows as a small tag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, Sequence

__all__ = ["Grant", "PERMISSIONS", "PermissionList", "from_keys", "from_reach", "permission_word"]

#: (key, word, Lucide glyph), in the one fixed order.
PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("camera", "Camera", "camera"),
    ("microphone", "Microphone", "mic"),
    ("location", "Location", "map-pin"),
    ("files", "Files", "folder"),
    ("internet", "Internet", "globe"),
    ("notifications", "Notifications", "bell"),
    ("screen", "Screen", "monitor"),
    ("apps", "Other apps", "layers"),
    ("passwords", "Passwords", "key-round"),
    ("devices", "Devices", "usb"),
    ("system", "System", "terminal"),
)
_ORDER = {key: index for index, (key, _w, _i) in enumerate(PERMISSIONS)}
_WORD = {key: word for key, word, _i in PERMISSIONS}
_GLYPH = {key: glyph for key, _w, glyph in PERMISSIONS}

#: What an update did to a grant, as the tag the list shows.
CHANGES = {"added": "New", "widened": "Wider", "narrowed": "Narrower", "removed": "Removed"}

#: A package's technical grants (ADR-028 section 7 keys, Flatpak-derived) in the vocabulary.
#: Keys with no word here ("audio", "background") are not permissions a person decides on.
_KEYS = {
    "files": "files", "files.portal": "files", "files.home": "files", "files.host": "files",
    "network": "internet",
    "devices.camera": "camera", "devices.microphone": "microphone",
    "devices.all": "devices", "graphics": "devices", "printing": "devices",
    "location": "location",
    "notifications": "notifications",
    "screen": "screen", "display.x11": "screen",
    "session.bus": "apps",
    "secrets": "passwords",
    "system": "system", "system.bus": "system", "sandbox.escape": "system",
}


@dataclass
class Grant:
    """One permission: its key (one of the eleven), the app's reasons, and what an update did to it."""

    key: str
    reasons: list[str] = field(default_factory=list)
    change: str = ""

    def __post_init__(self) -> None:
        if self.key not in _ORDER:
            raise ValueError(f"{self.key!r} is not one of the eleven permissions")
        if self.change and self.change not in CHANGES:
            raise ValueError(f"a grant's change is one of {', '.join(CHANGES)}")

    @property
    def word(self) -> str:
        return _WORD[self.key]

    @property
    def glyph(self) -> str:
        return _GLYPH[self.key]

    @property
    def reason(self) -> str:
        """The reasons as one line: each sentence capitalised, repeats dropped, joined with ". "."""
        seen: list[str] = []
        for reason in self.reasons:
            text = reason.strip()
            if text and text not in seen:
                seen.append(text)
        return ". ".join(text[:1].upper() + text[1:] for text in seen)


def permission_word(key: str) -> str:
    return _WORD[key]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def from_reach(reach: Iterable[Sequence[str]]) -> list[Grant]:
    """Publishers' (glyph, what, why) lines in the vocabulary, in its order (the mockup's `perms()`)."""
    got: dict[str, list[str]] = {}

    def add(key: str, why: str) -> None:
        got.setdefault(key, []).append(why)

    for icon, what, why in reach:
        w = what.lower()
        if "camera" in w:
            add("camera", why)
        if "micro" in w:
            add("microphone", why)
        if icon == "map-pin" and "location" in w:
            add("location", why)
        if icon in ("folder-open", "folder", "music-2", "image", "download", "hard-drive"):
            folder = (re.sub(r"^Your ", "", what) + ": ") if re.search(r"folder|downloads", w) and "your files" not in w else ""
            add("files", folder + _lower_first(why))
        if icon in ("globe", "headphones"):
            add("internet", why)
        if icon == "bell":
            add("notifications", why)
        if icon == "monitor" and "screen" in w:
            add("screen", why)
        if icon == "layers":
            add("apps", why)
        if icon == "key-round":
            add("passwords", why)
        if icon == "cpu" or (icon == "monitor" and "tablet" in w):
            add("devices", f"{re.sub(r'^Your ', '', what)}: {_lower_first(why)}")
        if icon == "terminal" or "into the system" in w:
            add("system", why)
    return [Grant(key, reasons) for key, reasons in sorted(got.items(), key=lambda item: _ORDER[item[0]])]


def from_keys(keys: Iterable[object]) -> list[Grant]:
    """Technical grants in the vocabulary. Each item is a key string or an object with `key`,
    and optionally `detail` (the reason) and `change`. Grants that share a word merge."""
    got: dict[str, Grant] = {}
    for item in keys:
        key = item if isinstance(item, str) else getattr(item, "key", "")
        word = _KEYS.get(key) or _KEYS.get(key.split(".", 1)[0])
        if word is None:
            continue
        grant = got.setdefault(word, Grant(word))
        detail = "" if isinstance(item, str) else getattr(item, "detail", "")
        if detail:
            grant.reasons.append(detail)
        change = "" if isinstance(item, str) else getattr(item, "change", "")
        if change and (not grant.change or grant.change == "removed"):
            grant.change = change
    return sorted(got.values(), key=lambda grant: _ORDER[grant.key])


def PermissionList(grants: Sequence[Grant]):  # noqa: N802 - a widget constructor by name
    """The list: a glyph in a small well, the word and the reason, hairlines between rows."""
    from ._permission_list import PermissionListWidget
    return PermissionListWidget(grants)
