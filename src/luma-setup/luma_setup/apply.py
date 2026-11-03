# SPDX-License-Identifier: Apache-2.0
"""Finish, as root: the one place a Luma account is created.

/usr/libexec/luma-setup-apply runs this through pkexec from the setup
session (polkit: org.projectluma.setup.apply, the gnome-initial-setup group,
local and active only). It reads one JSON object on stdin -- the answers,
the password among them, never on argv or in the environment -- and writes
one line per step on stdout, so the sheet can tick the rows off in turn:

    step account ok @nick
    step language ok
    step timezone ok
    step name ok
    step keyring ok
    done

It refuses to run once first boot is done or once a person has an account:
it makes the first account only. The account is made by AccountsService
(an administrator: wheel), the password set with chpasswd, then localed,
timedated and hostnamed are told the rest, luma-keyring enrols the saved
passwords with the same password, and the markers are written: first boot
done, and a welcome for the account's first session (the post-login steps).
"""
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .model import host_name, username_problem

__all__ = ("main", "apply", "human_accounts", "STATE_DIR", "Refused")

STATE_DIR = Path(os.environ.get("LUMA_SETUP_STATE_DIR", "/var/lib/luma-setup"))
DONE = "first-boot.json"
WELCOME = "welcome"
KEYRING = "/usr/libexec/luma-keyring"
FIRST_UID, LAST_UID = 1000, 60000


class Refused(Exception):
    pass


def human_accounts() -> list[str]:
    """Accounts a person signs in to: a uid in the login range and a shell that logs in."""
    found = []
    for entry in pwd.getpwall():
        if FIRST_UID <= entry.pw_uid < LAST_UID and not entry.pw_shell.endswith(("nologin", "false")):
            found.append(entry.pw_name)
    return found


def _taken(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


class _Bus:
    """The system bus, synchronously; one call per method."""

    def __init__(self) -> None:
        from gi.repository import Gio

        self.Gio = Gio
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def call(self, name: str, path: str, interface: str, method: str, signature: str | None, args: tuple,
             reply: str | None = None):
        from gi.repository import GLib

        params = GLib.Variant(signature, args) if signature else None
        result = self.bus.call_sync(name, path, interface, method, params,
                                    GLib.VariantType(reply) if reply else None,
                                    self.Gio.DBusCallFlags.NONE, 60000, None)
        return result.unpack() if result is not None else None


def _say(out, *words: str) -> None:
    out.write(" ".join(w for w in words if w) + "\n")
    out.flush()


def _split_layout(layout: str) -> tuple[str, str]:
    name, _, variant = layout.partition("+")
    return name, variant


def _create_account(bus, answers: dict) -> str:
    username = answers["username"]
    problem = username_problem(username, _taken)
    if problem:
        raise Refused(problem)
    if not answers.get("password"):
        raise Refused("An account needs a password.")
    path = bus.call("org.freedesktop.Accounts", "/org/freedesktop/Accounts", "org.freedesktop.Accounts",
                    "CreateUser", "(ssi)", (username, answers["full_name"], 1), "(o)")[0]
    # The password goes to chpasswd on stdin: hashed by the system's own method (yescrypt).
    subprocess.run(["chpasswd"], input=f"{username}:{answers['password']}\n", text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    user = ("org.freedesktop.Accounts", path, "org.freedesktop.Accounts.User")
    if answers.get("locale"):
        bus.call(*user, "SetLanguage", "(s)", (answers["locale"],))
    face = answers.get("face")
    if face and os.path.isfile(face):
        bus.call(*user, "SetIconFile", "(s)", (face,))
    return username


def _language(bus, answers: dict) -> None:
    localed = ("org.freedesktop.locale1", "/org/freedesktop/locale1", "org.freedesktop.locale1")
    bus.call(*localed, "SetLocale", "(asb)", ([f"LANG={answers['locale']}"], False))
    layout, variant = _split_layout(answers.get("layout") or "us")
    # convert: the console keyboard follows, so the disk passphrase and a text console match.
    bus.call(*localed, "SetX11Keyboard", "(ssssbb)", (layout, "", variant, "", True, False))


def _timezone(bus, answers: dict) -> None:
    timedated = ("org.freedesktop.timedate1", "/org/freedesktop/timedate1", "org.freedesktop.timedate1")
    if answers.get("timezone"):
        bus.call(*timedated, "SetTimezone", "(sb)", (answers["timezone"], False))
    bus.call(*timedated, "SetNTP", "(bb)", (True, False))


def _name(bus, answers: dict) -> None:
    hostnamed = ("org.freedesktop.hostname1", "/org/freedesktop/hostname1", "org.freedesktop.hostname1")
    pretty = (answers.get("pretty_hostname") or "").strip()
    static = host_name(answers.get("static_hostname") or pretty)
    if pretty:
        bus.call(*hostnamed, "SetPrettyHostname", "(sb)", (pretty, False))
    bus.call(*hostnamed, "SetStaticHostname", "(sb)", (static, False))


def _keyring(answers: dict) -> None:
    """luma-keyring's own enrolment (as the installer does): no keyring prompt later."""
    if not os.access(KEYRING, os.X_OK):
        raise RuntimeError("luma-keyring is not installed")
    subprocess.run([KEYRING, "--enrol", answers["username"]], input=answers["password"], text=True,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)


def _seal_connect(user: str, token: str) -> None:
    """The Connect token, sealed for the account with luma-keyring's systemd-creds path; never plain."""
    from luma_keyring.autounlock import _as_user, seal_key, write_private
    account = pwd.getpwnam(user)
    sealed = seal_key(token, user=user)

    def write() -> None:
        folder = Path(account.pw_dir) / ".local" / "state" / "luma-setup"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(folder / "connect.cred", sealed)
    _as_user(account, write)


def _write(path: Path, data: dict, mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = path.with_suffix(".new")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _markers(answers: dict, state_dir: Path) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write(state_dir / DONE, {"user": answers["username"], "finished": now})
    # The post-login steps run once, in this account's first session.
    _write(state_dir / WELCOME / answers["username"],
           {"accessibility": answers.get("accessibility") or {}, "created": now,
            "full_name": answers.get("full_name") or "",
            "first_session": answers.get("first_session") or {}})


def apply(answers: dict, out=sys.stdout, bus=None, state_dir: Path | None = None) -> int:
    state_dir = state_dir or STATE_DIR
    if (state_dir / DONE).exists():
        raise Refused("This Luma is already set up.")
    if human_accounts():
        raise Refused("This Luma already has an account.")
    bus = bus or _Bus()
    username = _create_account(bus, answers)
    _say(out, "step", "account", "ok", f"@{username}")
    # The rest is applied even when one step fails: the person is signed in either
    # way, and Settings can put a step right; each failure is said.
    for name, step in (("language", lambda: _language(bus, answers)),
                       ("timezone", lambda: _timezone(bus, answers)),
                       ("name", lambda: _name(bus, answers)),
                       ("keyring", lambda: _keyring(answers))):
        try:
            step()
            _say(out, "step", name, "ok")
        except Exception as error:  # noqa: BLE001 - reported, never fatal after the account
            print(f"luma-setup-apply: {name}: {_reason(error)}", file=sys.stderr)
            _say(out, "step", name, "fail")
    if answers.get("connect_token"):
        try:
            _seal_connect(answers["username"], answers["connect_token"])
        except Exception as error:  # noqa: BLE001 - Connect asks again on the desktop
            print(f"luma-setup-apply: connect: {_reason(error)}", file=sys.stderr)
    _markers(answers, state_dir)
    _say(out, "done")
    return 0


def _reason(error: BaseException) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        detail = (error.stderr or "").strip().splitlines()
        return detail[-1] if detail else f"exit {error.returncode}"
    return str(error)


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        print("luma-setup-apply: run as root (pkexec)", file=sys.stderr)
        return 2
    try:
        answers = json.loads(sys.stdin.readline() or "{}")
    except ValueError:
        print("luma-setup-apply: the answers are not JSON", file=sys.stderr)
        return 2
    face = answers.get("face")
    if face:
        # A picture the setup session chose: copied where AccountsService may read it.
        answers["face"] = _stage_face(face)
    try:
        return apply(answers)
    except Refused as error:
        _say(sys.stdout, "error", str(error))
        return 1
    except Exception as error:  # noqa: BLE001
        print(f"luma-setup-apply: {_reason(error)}", file=sys.stderr)
        _say(sys.stdout, "error", "The account could not be created.")
        return 1


def _stage_face(face: str) -> str | None:
    source = Path(face)
    allowed = (Path("/usr/share/pixmaps/faces"), Path("/run/luma-setup"), Path("/var/lib/luma-setup/faces"))
    try:
        resolved = source.resolve(strict=True)
    except OSError:
        return None
    if not any(resolved.is_relative_to(base) for base in allowed) or resolved.stat().st_size > 4 * 1024 * 1024:
        return None
    target = STATE_DIR / "faces" / resolved.name
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if resolved != target:
        shutil.copyfile(resolved, target)
    return str(target)
