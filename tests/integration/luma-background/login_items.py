#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""Open at Login round trips, each proven by a real login of the person's user manager.

Run as root inside the disposable container after install.sh (and after run.py,
or on its own). For every source of a login start -- an entry in the person's
autostart folder, one in /etc/xdg/autostart, none at all, the Background
portal's entry and a Luma agent's fallback -- the dock's switch (called with
the dock's identity) is turned off and on, the user manager is restarted, the
session's autostart target is started as gnome-session starts it, and what
actually ran is read back.

The container: Fedora 44 booted with systemd (`podman run --systemd=always`),
packages systemd, systemd-pam, dbus-daemon, dbus-tools, python3-gobject,
xdg-desktop-portal and util-linux, user `luma` (uid 1000) lingering. A rootless
or unprivileged container cannot set a login uid, so in that container only
`/etc/pam.d/systemd-user` makes pam_loginuid, pam_namespace and pam_selinux
optional.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402
from run import CONTROL, RESULTS, as_user, busctl, login, record, shell, wait  # noqa: E402

MARKS = Path("/tmp/bga-login-marks")
AUTOSTART = Path("/home/luma/.config/autostart")


def item(app: str) -> dict:
    reply = shell("GetLoginItem", app)
    return reply.get("value") or reply


def switch(app: str, on: bool) -> dict:
    reply = shell("SetLoginItem", app, "true" if on else "false")
    return reply.get("value") or reply


def fresh_login() -> set[str]:
    """Log in again and return the marks the session's autostart left."""
    for mark in MARKS.glob("*"):
        mark.unlink()
    Path(CONTROL / "flatpak-argv").unlink(missing_ok=True)
    login()
    # gnome-session pulls the autostart target in as a dependency of its own
    # session target; it refuses a manual start, so a stand-in target does the same.
    as_user("systemctl", "--user", "start", "bga-graphical-session.target")
    time.sleep(4)
    return {mark.name for mark in MARKS.glob("*")}


def desktop_file(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(["[Desktop Entry]", "Type=Application", *lines]) + "\n")


def main() -> int:
    session = Path("/etc/systemd/user/bga-graphical-session.target")
    session.write_text("[Unit]\nDescription=Stand-in for gnome-session's session target\n"
                       "Wants=xdg-desktop-autostart.target\nBefore=xdg-desktop-autostart.target\n")
    MARKS.mkdir(mode=0o1777, exist_ok=True)
    MARKS.chmod(0o1777)
    as_user("rm", "-rf", str(AUTOSTART), check=False)
    as_user("mkdir", "-p", str(AUTOSTART))
    for name in ("Notes", "Tray", "Diary"):
        program = Path(f"/usr/bin/luma-test-{name.lower()}")
        program.write_text(f"#!/bin/sh\nexec /usr/bin/touch {MARKS}/{name.lower()}\n")
        program.chmod(0o755)
        desktop_file(Path(f"/usr/share/applications/org.example.{name}.desktop"),
                     [f"Name={name}", f"Exec={program}"])
    # An app's own entry in the person's folder, under another file name.
    as_user("sh", "-c", f"printf '%s\\n' '[Desktop Entry]' 'Type=Application' 'Name=Notes' "
            f"'Exec=/usr/bin/luma-test-notes --hidden' > {AUTOSTART}/notes-helper.desktop")
    # A system entry.
    desktop_file(Path("/etc/xdg/autostart/org.example.Tray.desktop"),
                 ["Name=Tray", "Exec=/usr/bin/luma-test-tray --tray"])
    # The portal's entry for a sandboxed app, decided "allow" in the portal's table.
    as_user("busctl", "--user", "call", "org.freedesktop.impl.portal.PermissionStore",
            "/org/freedesktop/impl/portal/PermissionStore", "org.freedesktop.impl.portal.PermissionStore",
            "SetPermission", "sbssas", "background", "true", "background", "org.example.Flatchat", "1", "yes", check=False)
    as_user("sh", "-c", f"printf '%s\\n' '[Desktop Entry]' 'Type=Application' 'Name=Flatchat' "
            f"'X-XDP-Autostart=org.example.Flatchat' 'X-Flatpak=org.example.Flatchat' "
            f"'Exec=flatpak run --command=flatchat org.example.Flatchat --background' "
            f"> {AUTOSTART}/org.example.Flatchat.desktop")

    marks = fresh_login()
    before = {app: item(f"org.example.{app}") for app in ("Notes", "Tray", "Diary", "Flatchat", "Chatter")}
    record("every source is read: user entry, system entry, no entry, portal agent, Luma agent",
           before["Notes"].get("enabled") is True and before["Notes"].get("sources") == ["autostart-user"]
           and before["Tray"].get("sources") == ["autostart-system"] and before["Diary"].get("enabled") is False
           and before["Flatchat"].get("controlled-by") == "background"
           and before["Chatter"].get("controlled-by") == "autostart" and before["Chatter"].get("enabled") is False
           and {"notes", "tray"} <= marks and "diary" not in marks,
           {"items": before, "ran at login": sorted(marks)})

    for app, mark in (("Notes", "notes"), ("Tray", "tray")):
        off = switch(f"org.example.{app}", False)
        marks = fresh_login()
        after_off = item(f"org.example.{app}")
        on = switch(f"org.example.{app}", True)
        marks_on = fresh_login()
        after_on = item(f"org.example.{app}")
        record(f"{app}: off stops it at the next login, on brings it back",
               off.get("enabled") is False and mark not in marks and after_off.get("enabled") is False
               and on.get("enabled") is True and mark in marks_on and after_on.get("enabled") is True,
               {"off": off, "ran after off": sorted(marks), "on": on, "ran after on": sorted(marks_on)})
    system_entry = Path("/etc/xdg/autostart/org.example.Tray.desktop").read_text()
    record("the system's entry is never written; the person's override is removed when turned back on",
           "Hidden" not in system_entry and not (AUTOSTART / "org.example.Tray.desktop").exists(),
           {"system entry": system_entry, "override": (AUTOSTART / "org.example.Tray.desktop").exists()})

    on = switch("org.example.Diary", True)
    marks = fresh_login()
    written = (AUTOSTART / "org.example.Diary.desktop").read_text()
    off = switch("org.example.Diary", False)
    marks_off = fresh_login()
    record("an app with no entry gets the standard one from its own desktop entry, and loses it again",
           on.get("enabled") is True and "diary" in marks and "Exec=/usr/bin/luma-test-diary" in written
           and off.get("enabled") is False and "diary" not in marks_off,
           {"on": on, "entry": written, "ran": sorted(marks), "ran after off": sorted(marks_off)})

    off = switch("org.example.Flatchat", False)
    marks = fresh_login()
    ran_off = wait(lambda: (CONTROL / "flatpak-argv").exists(), 8)
    agent_off = run.agent("org.example.Flatchat")
    on = switch("org.example.Flatchat", True)
    marks_on = fresh_login()
    ran_on = wait(lambda: (CONTROL / "flatpak-argv").exists() and (CONTROL / "flatpak-argv").read_text(), 30)
    record("portal autostart: the switch is the agent's decision, and the login start follows it",
           off.get("enabled") is False and not ran_off and agent_off["decision"] == "deny"
           and on.get("enabled") is True and bool(ran_on) and run.agent("org.example.Flatchat")["decision"] == "allow",
           {"off": off, "agent after off": {k: agent_off[k] for k in ("decision", "state")},
            "started after on": ran_on})

    refused = busctl("call", "org.projectluma.Background1", "/org/projectluma/Background1",
                     "org.projectluma.Background1", "GetLoginItem", "s", "org.example.Notes")
    try:
        as_user("busctl", "--user", "call", "org.projectluma.Background1", "/org/projectluma/Background1",
                "org.projectluma.Background1", "SetLoginItem", "sb", "org.example.Notes", "false")
        other_refused = False
    except RuntimeError as error:
        other_refused = "only Settings or the dock" in str(error)
    record("anyone may read, only the dock and Settings may change",
           bool(refused["data"][0]) and other_refused, {"read": refused["data"][0]["enabled"], "write refused": other_refused})

    out = Path("/tmp/bga-login-items.json")
    out.write_text(json.dumps(RESULTS, indent=1, default=str))
    failed = [item for item in RESULTS if not item["pass"]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed; record: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
