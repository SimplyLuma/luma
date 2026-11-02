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
from luma_keyring.repair import (enable_auto_unlock, recover, rewrap,  # noqa: E402
                                 rewrap_current, use_login_password)
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
    keyring.lock()
    try:
        keyring.unlock_with(password)
    except Exception:  # noqa: BLE001 - any failure is the answer
        return False
    return state.read(keyring).state is state.State.UNLOCKED


def private(path: Path) -> bool:
    info = path.stat()
    return stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid()


def main() -> int:
    password = os.environ["LUMA_KEYRING_SMOKE_PASSWORD"]
    verify = lambda typed: typed == password  # noqa: E731 - PAM stands in
    login = state.keyring_path()
    keyrings = login.parent
    credential, wrap = autounlock.credential_path(), autounlock.wrap_path()
    if login.exists():
        raise SystemExit(f"{login} exists; run this as a throwaway user")

    # -- installed with "Sign me in automatically" -----------------------
    check("the installer sealed and wrapped a keyring key",
          private(credential) and private(wrap)
          and stat.S_IMODE(autounlock.state_dir().stat().st_mode) == 0o700)
    check("neither file contains the password",
          password.encode() not in credential.read_bytes() + wrap.read_bytes())
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("the first automatic sign-in makes the login keyring", result == Result.CREATED, result)
        store("hunter2")
        check("a secret stores in the default collection without a prompt", lookup() == "hunter2")
        check("the keyring is on the random key, not the password",
              not opens_with(keyring, password))
    time.sleep(0.5)
    raw = login.read_bytes()
    check("the keyring file keeps gnome-keyring's encrypted format",
          raw.startswith(b"GnomeKeyring\n") and b"hunter2" not in raw)

    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("the next automatic sign-in unlocks it", result == Result.UNLOCKED, result)
        check("what was stored is there", lookup() == "hunter2")

    # -- another board: the seal no longer opens -------------------------
    autounlock.write_private(credential, b"sealed on another board")
    before = digest(login)
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("an unusable seal is reported and the keyring left locked",
              result == Result.SEAL_UNUSABLE and digest(login) == before
              and state.read(keyring).state is state.State.LOCKED, result)
        outcome = recover("not-it", keyring)
        check("the wrong password recovers nothing", not outcome.ok, outcome.message)
        started = time.monotonic()
        outcome = recover(password, keyring)
        took = time.monotonic() - started
        check("the account password recovers the key and seals it again",
              outcome.ok and state.read(keyring).state is state.State.UNLOCKED, outcome.message)
        check("recovery takes under a second here", took < 1.0, f"{took:.2f} s")
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("and it unlocks by itself again", result == Result.UNLOCKED, result)
        check("with everything kept", lookup() == "hunter2")

    # -- a password change ------------------------------------------------
    outcome = rewrap(password, "a-new-password")
    check("a password change re-wraps the key", outcome.ok, outcome.message)
    autounlock.write_private(credential, b"sealed on another board")
    with Session() as keyring:
        autounlock.ensure(keyring, autologin=True)
        check("the old password no longer recovers it", not recover(password, keyring).ok)
        check("the new one does", recover("a-new-password", keyring).ok)
    # After a change outside Settings: the key is unsealed here, so only the
    # new password is needed.
    outcome = rewrap_current(password, verify=verify)
    check("the current password alone re-wraps it", outcome.ok, outcome.message)
    autounlock.write_private(credential, b"sealed on another board")
    with Session() as keyring:
        autounlock.ensure(keyring, autologin=True)
        check("and recovers it", recover(password, keyring).ok)

    # -- automatic sign-in turned off ------------------------------------
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=False)
        check("with automatic sign-in off it still unlocks", result == Result.UNLOCKED, result)
        outcome = use_login_password(password, keyring, verify=verify)
        check("it can go back onto the login password", outcome.ok, outcome.message)
        check("the sealed and wrapped keys are gone", not credential.exists() and not wrap.exists())
        check("the login password opens it", opens_with(keyring, password))
        check("still with everything in it", lookup() == "hunter2")

    # -- an existing device: keyring made at a password login -------------
    reset(keyrings)
    with Session() as keyring:
        keyring.create_login(password)
        store("kept-secret")
    before = digest(login)
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("not enrolled: left for its owner", result == Result.NEEDS_PASSWORD, result)
        check("the keyring file is unchanged", digest(login) == before)
        outcome = enable_auto_unlock("not-it", keyring, verify=verify)
        check("the wrong password enrols nothing",
              not outcome.ok and not credential.exists() and digest(login) == before, outcome.message)
        outcome = enable_auto_unlock(password, keyring, verify=verify)
        check("typing the password once enrols it", outcome.ok, outcome.message)
    with Session() as keyring:
        result = autounlock.ensure(keyring, autologin=True)
        check("from then on it unlocks by itself", result == Result.UNLOCKED, result)
        check("everything in it was kept", lookup() == "kept-secret")

    # -- a keyring someone gave an empty password ------------------------
    reset(keyrings)
    for path in (credential, wrap):
        path.unlink(missing_ok=True)
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
