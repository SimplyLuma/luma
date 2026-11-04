# SPDX-License-Identifier: Apache-2.0
"""What first-boot setup knows and decides, without any GTK.

The pages, in order: Hello (language and keyboard), Get online (only without
a cable, and only with Wi-Fi), About you (the account) and Name this Luma
(device name and time zone). A page the installer already answered is left
out (see handover.installer_answers). Finish creates the account and applies
everything through one root helper (apply.py), then signs the person in.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

__all__ = ("Setup", "PAGES", "first_name", "slug", "host_name", "strength", "mismatch",
           "username_problem", "RESERVED_USERNAMES")

#: Every first-boot page, in order.
PAGES = ("hello", "wifi", "you", "device")

#: Names an account may never take, beyond those already on the system.
RESERVED_USERNAMES = frozenset({
    "root", "bin", "daemon", "adm", "lp", "sync", "shutdown", "halt", "mail", "operator", "games", "ftp",
    "nobody", "admin", "administrator", "guest", "user", "users", "wheel", "sudo", "system", "luma",
    "gdm", "gnome-initial-setup", "polkitd", "dbus", "systemd-network", "systemd-resolve", "sshd",
})

_MARKS = re.compile(r"[̀-ͯ]")


def _plain(text: str) -> str:
    """Accents stripped, lower case."""
    return _MARKS.sub("", unicodedata.normalize("NFD", text)).lower()


def first_name(name: str) -> str:
    parts = name.strip().split()
    return parts[0] if parts else ""


def slug(text: str) -> str:
    """A username from a name: accents stripped, lower case, a-z and 0-9 only."""
    return re.sub(r"[^a-z0-9]+", "", _plain(text))[:32]


def host_name(text: str) -> str:
    """The static host name from the device name: "Nick's Luma" is nicks-luma."""
    plain = re.sub(r"[’']", "", _plain(text))
    name = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:63].strip("-")
    return name or "luma"


def strength(password: str) -> tuple[int, str]:
    """Four bars and one sentence. A hint, never a gate."""
    if not password:
        return 0, ""
    score = sum((len(password) >= 8, len(password) >= 12, bool(re.search(r"\d", password)),
                 bool(re.search(r"[^A-Za-z0-9]", password)),
                 bool(re.search(r"[a-z]", password) and re.search(r"[A-Z]", password))))
    if score <= 1:
        return 1, "Weak. Try 8 or more characters."
    if score == 2:
        return 2, "Fair. Add a number or a symbol."
    if score == 3:
        return 3, "Good."
    return 4, "Strong."


def mismatch(password: str, again: str) -> bool:
    """The second password has gone where the first does not."""
    return bool(again) and not password.startswith(again)


def username_problem(username: str, taken=None) -> str:
    """Why a username cannot be used, or "" when it can."""
    if not username:
        return "Pick a username."
    if not re.fullmatch(r"[a-z][a-z0-9]{0,31}", username):
        return "A username starts with a letter."
    if username in RESERVED_USERNAMES:
        return "That username belongs to the system. Try another."
    if taken is not None and taken(username):
        return "That username is taken. Try another."
    return ""


@dataclass
class Setup:
    """The answers so far."""

    locale: str = "en_US.UTF-8"
    layout: str = "us"                 # an XKB layout, "us" or "us+dvorak"
    network: str = ""                  # the joined network's name; "wired" with a cable
    name: str = ""
    username: str = ""
    username_edited: bool = False
    password: str = ""
    again: str = ""
    face: str = ""                     # a picture's path; "" for their initial
    device: str = ""
    device_edited: bool = False
    timezone: str = "UTC"
    timezone_automatic: bool = False   # found from where they are, once online
    answered: frozenset = field(default_factory=frozenset)  # pages the installer answered
    accessibility: dict = field(default_factory=dict)       # screen-reader, large-text, zoom

    # -- what follows from what ---------------------------------------------
    @property
    def first(self) -> str:
        return first_name(self.name)

    @property
    def device_name(self) -> str:
        if self.device_edited:
            return self.device
        return f"{self.first}’s Luma" if self.first else "Luma"

    @property
    def online(self) -> bool:
        return bool(self.network)

    def set_name(self, name: str) -> None:
        self.name = name
        if not self.username_edited:
            self.username = slug(self.first)

    def set_username(self, text: str) -> str:
        """Typed into the username field: kept to what a username can be."""
        self.username = slug(text)
        self.username_edited = True
        return self.username

    def set_device(self, text: str) -> None:
        self.device = text
        self.device_edited = True

    def pages(self, *, wired: bool, wifi: bool) -> list[str]:
        """The pages this device shows: Get online only without a cable and with Wi-Fi."""
        shown = []
        for page in PAGES:
            if page in self.answered:
                continue
            if page == "wifi" and (wired or not wifi):
                continue
            shown.append(page)
        return shown

    def ready(self, page: str, taken=None) -> bool:
        """Whether the page's key can be pressed."""
        if page == "you":
            return bool(self.name.strip() and not username_problem(self.username, taken)
                        and self.password and self.password == self.again)
        if page == "device":
            return bool(self.device_name.strip())
        if page == "wifi":
            return self.online
        return True

    def payload(self) -> dict:
        """What the root helper applies at Finish (apply.py). The password goes on stdin only."""
        return {
            "full_name": self.name.strip(),
            "username": self.username,
            "password": self.password,
            "face": self.face or None,
            "locale": self.locale,
            "layout": self.layout,
            "timezone": self.timezone,
            "ntp": self.online,
            "pretty_hostname": self.device_name.strip(),
            "static_hostname": host_name(self.device_name),
            "accessibility": dict(self.accessibility),
        }
