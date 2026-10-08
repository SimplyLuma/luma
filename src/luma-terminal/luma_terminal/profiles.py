"""Read the existing Ptyxis profile without altering its settings or store.

Terminal is replacing Ptyxis, so it inherits the default shell behavior from
the already installed profile. This module never creates, migrates or writes
GSettings values. Fixture mode does not import or call it.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


_UUID = re.compile(r"[0-9a-fA-F]{32}\Z")


@dataclass(frozen=True)
class Profile:
    uuid: str
    label: str
    login_shell: bool
    custom_command: str
    scrollback_lines: int
    limit_scrollback: bool
    container: str


def argv_for_profile(profile: Profile | None, shell: str) -> list[str]:
    """An argv for VTE; a saved custom command is parsed, never evaluated."""
    if profile and profile.custom_command:
        argv = shlex.split(profile.custom_command)
        if argv:
            return argv
    return [shell, "-l"] if profile and profile.login_shell else [shell]


def read_default_profile() -> Profile | None:
    """Read Ptyxis's current default. A missing schema/profile means shell defaults."""
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.Ptyxis", True) is None:
        return None
    app = Gio.Settings.new("org.gnome.Ptyxis")
    uuid = app.get_string("default-profile-uuid")
    if not _UUID.fullmatch(uuid):
        return None
    if uuid not in app.get_strv("profile-uuids"):
        return None
    if source.lookup("org.gnome.Ptyxis.Profile", True) is None:
        return None
    profile = Gio.Settings.new_with_path("org.gnome.Ptyxis.Profile",
                                         f"/org/gnome/Ptyxis/Profiles/{uuid}/")
    return Profile(
        uuid=uuid,
        label=profile.get_string("label") or "Default profile",
        login_shell=profile.get_boolean("login-shell"),
        custom_command=profile.get_string("custom-command") if profile.get_boolean("use-custom-command") else "",
        scrollback_lines=max(0, profile.get_int("scrollback-lines")),
        limit_scrollback=profile.get_boolean("limit-scrollback"),
        container=profile.get_string("default-container"),
    )
