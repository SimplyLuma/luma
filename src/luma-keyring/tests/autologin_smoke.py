#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""The automatic sign-in keyring against a real daemon and real systemd-creds.

Uses the caller's own home and session bus, so run it as a throwaway user
with a user manager of its own, never as a person with a keyring. The
installer's step runs first, as root:

    useradd -m keyringtest && systemctl start user@$(id -u keyringtest)
    printf '%s\\n' "$PASSWORD" | luma-keyring --enrol keyringtest
    systemd-run --user -M keyringtest@ --wait --pipe \\
        -E LUMA_KEYRING_SMOKE_PASSWORD="$PASSWORD" -- \\
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
from luma_keyring.repair import enable_auto_unlock  # noqa: E402
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


def opens_with(keyring: Keyring, password: str) -> bool:
    """The recovery path: the account password still opens the keyring."""
    keyring.lock()
    try:
        keyring.unlock_with(password)
    except Exception:  # noqa: BLE001 - any failure is the answer
        return False
    return state.read(keyring).state is state.State.UNLOCKED


def main() -> int:
    password = os.environ["LUMA_KEYRING_SMOKE_PASSWORD"]
    login = state.keyring_path()
    keyrings = login.parent
    credential = autounlock.credential_path()
    if login.exists():
        raise SystemExit(f"{login} exists; run this as a throwaway user")

    # -- installed with "Sign me in automatically" -----------------------
    check("the installer sealed the password for the account",
          credential.exists() and stat.S_IMODE(credential.stat().st_mode) == 0o600
          and credential.stat().st_uid == os.getuid())
    check("the sealed file does not contain the password",
          password.encode() not in credential.read_bytes())
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("the first automatic sign-in makes the login keyring", result == Result.CREATED, result)
        check("it is open", state.read(keyring).state is state.State.UNLOCKED)
        store("hunter2")
        check("a secret stores in the default collection without a prompt", lookup() == "hunter2")
        check("the account password opens it too (the recovery path)", opens_with(keyring, password))
    time.sleep(0.5)
    raw = login.read_bytes()
    check("the keyring file is encrypted", raw.startswith(b"GnomeKeyring\n") and b"hunter2" not in raw)

    with Session() as keyring:
        check("a new session starts with it locked",
              state.read(keyring).state is state.State.LOCKED)
        result = autounlock.ensure(keyring, autologin=True)
        check("the next automatic sign-in opens it", result == Result.UNLOCKED, result)
        check("what was stored is there", lookup() == "hunter2")

    # -- the sealed copy stops working (another board, a changed password)
    autounlock.seal_password("not-the-password")
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("a sealed password that does not open it is reported, not forced",
              result == Result.SEAL_UNUSABLE, result)
        outcome = enable_auto_unlock(password, keyring)
        check("typing the password once seals it again", outcome.ok, outcome.message)
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("and it opens by itself again", result == Result.UNLOCKED, result)
        check("with everything kept", lookup() == "hunter2")

    # -- automatic sign-in turned off ------------------------------------
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=False)
        check("signing in with a password again removes the sealed copy",
              result == Result.RETIRED and not credential.exists(), result)
        check("a password account is then left alone",
              autounlock.ensure(keyring, autologin=False) == Result.NOT_AUTOLOGIN)

    # -- an existing device: keyring made at a password login -------------
    reset(keyrings)
    with Session() as keyring:
        keyring.create_login(password)
        store("kept-secret")
    before = digest(login)

    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("without a sealed password it is left for its owner",
              result == Result.NEEDS_PASSWORD, result)
        check("the keyring file is unchanged", digest(login) == before)

        outcome = enable_auto_unlock("not-it", keyring)
        check("the wrong password seals nothing",
              not outcome.ok and not credential.exists(), outcome.message)

        outcome = enable_auto_unlock(password, keyring)
        check("typing the password once enables automatic unlock", outcome.ok, outcome.message)
        check("the keyring is still locked with the account password", opens_with(keyring, password))

    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("from then on it opens by itself", result == Result.UNLOCKED, result)
        check("everything in it was kept", lookup() == "kept-secret")

    # -- a keyring someone gave an empty password ------------------------
    reset(keyrings)
    with Session() as keyring:
        keyring.create_login("")
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("an unencrypted keyring opens without a prompt", result == Result.UNENCRYPTED, result)

    reset(keyrings)
    print(f"DONE {checked - len(failures)}/{checked} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
