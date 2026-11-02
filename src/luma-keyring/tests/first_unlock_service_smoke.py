#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""The session service's first unlock, end to end, on a real daemon and display.

Run like first_unlock_smoke.py (a throwaway HOME, bus and display). The
account is made to look like an automatic sign-in and PAM is stood in for
(no account has this password); sealing is real systemd-creds when
LUMA_KEYRING_REAL_SEAL is set. Checks that the service tells systemd it is
ready as soon as the sheet is up, holds the prompter while it is, and on
Unlock enrols, gives the prompter back and exits. LUMA_KEYRING_BY_HAND=1
instead opens the keyring page by hand on an unlocked, unenrolled account:
the same sheet comes up, and its password enrols it.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gi.repository import Gio, GLib  # noqa: E402

from luma_keyring import autounlock, first_unlock, ready, repair  # noqa: E402
from luma_keyring.prompt_gate import PROMPTER_NAME  # noqa: E402
from luma_keyring.secrets import Keyring  # noqa: E402

PASSWORD = "first-unlock-service"
CLIENT = """
import gi
gi.require_version('Secret', '1')
from gi.repository import Secret
service = Secret.Service.get_sync(Secret.ServiceFlags.LOAD_COLLECTIONS, None)
login = [c for c in service.get_collections() if c.get_object_path().endswith('/login')][0]
count, _ = service.unlock_sync([login], None)
print('unlocked' if count and not login.get_locked() else 'dismissed', flush=True)
"""
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}{f' -- {detail}' if detail else ''}", flush=True)
    if not ok:
        failures.append(name)


def main() -> int:
    keyring = Keyring()
    if autounlock.state_dir().exists():
        raise SystemExit("refusing to run with key slots in this HOME")
    keyring.create_login(PASSWORD)
    by_hand = bool(os.environ.get("LUMA_KEYRING_BY_HAND"))
    if not by_hand:
        keyring.lock()

    autounlock.is_autologin = lambda user=None, **paths: True
    repair.check_login_password = lambda password, username=None: password == PASSWORD
    if not os.environ.get("LUMA_KEYRING_REAL_SEAL"):
        autounlock.seal_key = lambda key, user=None: b"sealed:" + key.encode()

    folder = tempfile.mkdtemp()
    notify = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    notify.bind(os.path.join(folder, "notify"))
    notify.setblocking(False)
    os.environ["NOTIFY_SOCKET"] = os.path.join(folder, "notify")

    argv = ["luma-keyring", "--gapplication-service"]
    started = time.monotonic()
    ask, prompter = ready.decide(argv)
    # An app asks the instant the session starts, before the sheet is up; another
    # while it is up. Both must be answered by the sheet, never by another prompter.
    early = subprocess.Popen([sys.executable, "-c", CLIENT], stdout=subprocess.PIPE, text=True)
    check("the service decides " + ("not to ask at start" if by_hand else "to ask") + ", before GTK loads",
          ask is not by_hand and "gi.repository.Gtk" not in sys.modules, f"ask={ask}")
    schemas = os.path.join(folder, "schemas")
    os.mkdir(schemas)
    subprocess.run(["glib-compile-schemas", "--targetdir", schemas,
                    str(Path(__file__).resolve().parents[1] / "data" / "glib-2.0")], check=True)
    os.environ["GSETTINGS_SCHEMA_DIR"] = schemas

    from luma_keyring.service import KeyringService
    service = KeyringService(ask_first=ask, prompter=prompter)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    seen: dict = {}

    def owner() -> str:
        try:
            return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                 "org.freedesktop.DBus", "GetNameOwner",
                                 GLib.Variant("(s)", (PROMPTER_NAME,)), GLib.VariantType.new("(s)"),
                                 Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
        except GLib.Error:
            return ""

    def drive() -> bool:
        windows = [w for w in service.get_windows() if w.get_mapped()]
        if not windows:
            return GLib.SOURCE_CONTINUE
        sheet = windows[0]
        try:
            seen["ready"] = notify.recv(64)
            seen["ready_after"] = time.monotonic() - started
        except BlockingIOError:
            seen["ready"] = b""
        seen["gate_ours"] = owner() == bus.get_unique_name()
        seen["title"] = sheet.get_title()
        seen["late"] = subprocess.Popen([sys.executable, "-c", CLIENT], stdout=subprocess.PIPE, text=True)
        time.sleep(1.0)
        seen["owner_during"] = owner()
        sheet.password = PASSWORD
        sheet._on_primary()
        return GLib.SOURCE_REMOVE

    if by_hand:
        GLib.timeout_add(300, lambda: service.activate() and False)
    GLib.timeout_add(100, drive)
    GLib.timeout_add_seconds(60, lambda: service.quit())
    code = service.run(argv)
    check("READY before the sheet (the prompter is taken first)", by_hand or seen.get("ready") == b"READY=1",
          f"{seen.get('ready_after', 0):.1f} s")
    check("the sheet is Luma's", seen.get("title") == "Unlock your saved passwords")
    check("the service was the keyring prompter while asking (not when unlocked)",
          seen.get("gate_ours", False) is not by_hand)
    check("and gave it back", owner() != bus.get_unique_name())
    check("the service exits after the answer", code == 0 and time.monotonic() - started < 45)
    if not by_hand:
        for name, child in (("early", early), ("late", seen.get("late"))):
            try:
                out = child.communicate(timeout=15)[0].strip()
            except Exception:  # noqa: BLE001
                child.kill()
                out = "still waiting"
            check(f"the app that asked {name} got its keyring from the sheet", out == "unlocked", out)
        check("no other prompter was ever asked", seen.get("owner_during") == bus.get_unique_name())
    check("the keyring is unlocked and enrolled",
          not keyring.collection().locked and autounlock.credential_path().exists()
          and first_unlock.is_automatic())
    print(f"{len(failures)} failed" if failures else "all passed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
