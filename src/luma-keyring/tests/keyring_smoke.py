#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""The repair against a real keyring daemon.

Starts gnome-keyring-daemon in a session of its own, makes a login keyring
locked with one password while the "account password" is another, and
checks that Luma sees the mismatch, repairs it in place with everything
still in it, and -- in the second run -- starts a fresh keyring that opens
with the account password while the old file is kept.

Run it inside a container with gnome-keyring and dbus:
    dbus-run-session -- python3 tests/keyring_smoke.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

from luma_keyring import state  # noqa: E402
from luma_keyring.repair import repair_in_place, start_again  # noqa: E402
from luma_keyring.secrets import Keyring  # noqa: E402

OLD = "the-old-password"
ACCOUNT = "the-account-password"
failures = []
checked = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checked
    checked += 1
    print(f"{'PASS' if ok else 'FAIL'} {name}{f' -- {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


def start_daemon() -> subprocess.Popen:
    daemon = subprocess.Popen(
        ["gnome-keyring-daemon", "--foreground", "--components=secrets"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.2)
        if Keyring().running():
            return daemon
    raise SystemExit("the keyring daemon never came up")


def make_locked_login(keyring: Keyring) -> None:
    """A login keyring locked with a password that is not the account's."""
    keyring.create_login(OLD)
    schema = Secret.Schema.new("org.projectluma.KeyringSmoke",
                               Secret.SchemaFlags.NONE,
                               {"what": Secret.SchemaAttributeType.STRING})
    Secret.password_store_sync(schema, {"what": "wifi"}, "login",
                               "Home Wi-Fi", "hunter2", None)
    # Lock it, as a login that could not unlock it would leave it.
    keyring.lock()


def main() -> int:
    home = os.environ["HOME"]
    Path(home, ".local/share/keyrings").mkdir(parents=True, exist_ok=True)
    daemon = start_daemon()
    try:
        keyring = Keyring()
        make_locked_login(keyring)
        time.sleep(0.5)

        reading = state.read(keyring)
        check("a login keyring locked with another password is the broken state",
              reading.state is state.State.LOCKED, reading.sentence)
        check("this keyring daemon can be repaired in place", reading.repairable)

        outcome = repair_in_place("not-it", ACCOUNT, keyring, verify=lambda p: p == ACCOUNT)
        check("the wrong old password changes nothing", not outcome.ok, outcome.message)

        outcome = repair_in_place(OLD, "not-my-login", keyring, verify=lambda p: p == ACCOUNT)
        check("a login password that is not the account's changes nothing",
              not outcome.ok, outcome.message)

        outcome = repair_in_place(OLD, ACCOUNT, keyring, verify=lambda p: p == ACCOUNT)
        check("the repair reports success", outcome.ok, outcome.message)
        check("the keyring is open afterwards",
              state.read(keyring).state is state.State.UNLOCKED)

        schema = Secret.Schema.new("org.projectluma.KeyringSmoke",
                                   Secret.SchemaFlags.NONE,
                                   {"what": Secret.SchemaAttributeType.STRING})
        kept = Secret.password_lookup_sync(schema, {"what": "wifi"}, None)
        check("what was in the keyring is still in it", kept == "hunter2", str(kept))

        keyring.lock()
        keyring.unlock_with(ACCOUNT)
        check("it opens with the account password from now on",
              state.read(keyring).state is state.State.UNLOCKED)

        # Starting again: the old file is kept, the new keyring opens.
        outcome = start_again(ACCOUNT, keyring, verify=lambda p: p == ACCOUNT)
        check("starting again reports success", outcome.ok, outcome.message)
        check("the old keyring file is kept, not deleted",
              outcome.kept_file is not None and outcome.kept_file.exists(),
              str(outcome.kept_file))
    finally:
        daemon.terminate()

    print(f"DONE {checked - len(failures)}/{checked} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
