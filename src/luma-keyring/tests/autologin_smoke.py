#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""The automatic sign-in keyring against a real daemon and real systemd-creds.

Uses the caller's own home and session bus, so run it as a throwaway user
with a user manager of its own, never as a person with a keyring:

    useradd -m keyringtest && systemctl start user@$(id -u keyringtest)
    systemd-run --user -M keyringtest@ --wait --pipe -- \\
        python3 /path/to/tests/autologin_smoke.py

It refuses to run when a login keyring already exists. Each "session" is a
fresh gnome-keyring-daemon, which starts with every keyring locked, as a
sign-in that typed no password leaves it.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("Secret", "1")
from gi.repository import Secret  # noqa: E402

from luma_keyring import autounlock, state  # noqa: E402
from luma_keyring.autounlock import Result  # noqa: E402
from luma_keyring.repair import switch_to_device_key  # noqa: E402
from luma_keyring.secrets import Keyring  # noqa: E402

SCHEMA = Secret.Schema.new("org.projectluma.AutologinSmoke", Secret.SchemaFlags.NONE,
                           {"what": Secret.SchemaAttributeType.STRING})
failures: list[str] = []
checked = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checked
    checked += 1
    print(f"{'PASS' if ok else 'FAIL'} {name}{f' -- {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


class Session:
    """One keyring daemon, as one sign-in would have it."""

    def __enter__(self) -> Keyring:
        subprocess.run(["systemctl", "--user", "stop", "gnome-keyring-daemon.socket",
                        "gnome-keyring-daemon.service"], capture_output=True)
        self.daemon = subprocess.Popen(
            ["gnome-keyring-daemon", "--foreground", "--replace", "--components=secrets"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        keyring = Keyring()
        for _ in range(50):
            time.sleep(0.2)
            if keyring.running():
                return keyring
        raise SystemExit("the keyring daemon never came up")

    def __exit__(self, *_exc) -> None:
        Secret.Service.disconnect()
        self.daemon.terminate()
        self.daemon.wait(10)
        time.sleep(0.5)


def store(value: str) -> None:
    # The default collection, as NetworkManager and app sign-ins use it.
    Secret.password_store_sync(SCHEMA, {"what": "wifi"}, Secret.COLLECTION_DEFAULT,
                               "Home Wi-Fi", value, None)


def lookup() -> str | None:
    return Secret.password_lookup_sync(SCHEMA, {"what": "wifi"}, None)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reset(keyrings: Path) -> None:
    shutil.rmtree(keyrings, ignore_errors=True)
    keyrings.mkdir(parents=True)


def main() -> int:
    login = state.keyring_path()
    keyrings = login.parent
    if login.exists() and not os.environ.get("LUMA_KEYRING_SMOKE_OWNS_HOME"):
        raise SystemExit(f"{login} exists; run this as a throwaway user")
    credential = autounlock.credential_path()
    reset(keyrings)

    # -- a new account that signs in automatically -----------------------
    with Session() as keyring:
        check("a password account is not touched",
              autounlock.ensure(keyring, autologin=False) == Result.NOT_AUTOLOGIN)
        check("no keyring was made for it", not login.exists())

        result = autounlock.ensure(keyring, autologin=True)
        check("the first automatic sign-in makes a login keyring", result == Result.CREATED, result)
        check("the sealed key is kept, readable by its owner only",
              credential.exists() and stat.S_IMODE(credential.stat().st_mode) == 0o600)
        check("the keyring is open", state.read(keyring).state is state.State.UNLOCKED)
        store("hunter2")
        check("a secret stores in the default collection without a prompt", lookup() == "hunter2")
    time.sleep(0.5)
    raw = login.read_bytes()
    check("the keyring file is encrypted, not plain",
          raw.startswith(b"GnomeKeyring\n") and b"hunter2" not in raw)

    with Session() as keyring:
        check("a new session starts with it locked",
              state.read(keyring).state is state.State.LOCKED)
        result = autounlock.ensure(keyring, autologin=True)
        check("the next automatic sign-in opens it with the sealed key",
              result == Result.UNLOCKED, result)
        check("what was stored is there", lookup() == "hunter2")

    # -- an existing account whose keyring has a password ----------------
    reset(keyrings)
    with Session() as keyring:
        keyring.create_login("their-password")
        store("kept-secret")
    before = digest(login)

    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("a password-locked keyring is left for its owner", result == Result.NEEDS_PASSWORD, result)
        check("the keyring file is unchanged", digest(login) == before)
        check("no key was sealed for it", not credential.exists())

        outcome = switch_to_device_key("not-it", keyring)
        check("the wrong password changes nothing",
              not outcome.ok and digest(login) == before and not credential.exists(), outcome.message)

        outcome = switch_to_device_key("their-password", keyring)
        check("typing its password once switches it to the device key", outcome.ok, outcome.message)
        check("it is open", state.read(keyring).state is state.State.UNLOCKED)

    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("from then on it opens by itself", result == Result.UNLOCKED, result)
        check("everything in it was kept", lookup() == "kept-secret")

    # -- a keyring someone gave an empty password ------------------------
    reset(keyrings)
    with Session() as keyring:
        keyring.create_login("")
        store("plain-secret")
    time.sleep(0.5)
    check("an empty password keeps it in plain text", login.read_bytes().startswith(b"[keyring]"))
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("it is sealed to the device without asking", result == Result.SEALED, result)
    time.sleep(0.5)
    raw = login.read_bytes()
    check("and encrypted from then on", raw.startswith(b"GnomeKeyring\n") and b"plain-secret" not in raw)
    with Session() as keyring:
        check("it opens by itself", autounlock.ensure(keyring, autologin=True) == Result.UNLOCKED)
        check("its contents were kept", lookup() == "plain-secret")

    # -- a key that no longer opens it -----------------------------------
    credential.unlink()
    autounlock.seal_new_key(credential)
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("a key that does not open it is reported, not forced",
              result == Result.KEY_UNUSABLE, result)

    reset(keyrings)
    print(f"DONE {checked - len(failures)}/{checked} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
