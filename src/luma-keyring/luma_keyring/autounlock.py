# SPDX-License-Identifier: MPL-2.0
"""The login keyring of an account that signs in automatically.

pam_gnome_keyring unlocks the login keyring with the password typed at
login. An account that signs in automatically types none, so its login
keyring stays locked and the first thing that stores a secret -- a network,
an app sign-in -- asks for it. On a television there is nobody to ask.

Envelope encryption with two key slots, as LUKS2 keyslots or a TPM
protector plus a recovery password: the login keyring is locked with a
random 256-bit keyring key, and the key is kept twice in
``~/.local/state/luma-keyring`` (0700; files 0600, written atomically):

* ``login.cred`` -- the device seal, for automatic unlock: the key sealed
  with ``systemd-creds --user`` as a user-scoped credential. With a TPM2 it
  is bound to the TPM2 and the host key, and to PCR 7 (Secure Boot state)
  where the system disk is encrypted; without full-disk encryption a PCR
  policy protects nothing an attacker with the disk could not undo, so none
  is used there. Without a TPM2 it is bound to the host key alone.
* ``login.wrap`` -- the recovery slot: the key encrypted with AES-256-GCM
  under Argon2id (RFC 9106: 64 MiB, t=3, p=4) of the account password, both
  from python-cryptography (OpenSSL). Versioned; the whole header is
  authenticated as associated data. It is an offline password verifier, so
  it is marked not to be backed up (``user.xdg.robots.backup=false``).

The account password itself is never kept in any form that can be turned
back into it, and secrets reach this module on stdin only, never argv, the
environment or a log.

Enrolment happens where the password is in hand: the installer
(``enrol_user``), the PAM password stack at a password change
(``pam_rewrap``), or once in the session on devices set up earlier
(repair.enable_auto_unlock). Every change of key goes through ``*.new``
slots that are fsynced before the keyring moves and promoted after, and
``ensure`` and ``recover`` try them too, so at every instant some slot on
disk opens the keyring.

gnome-keyring's own files and formats are untouched.
"""

from __future__ import annotations

import base64
import configparser
import ctypes
import getpass
import json
import logging
import os
import pwd
import secrets as random
import subprocess
import time
import tomllib
from pathlib import Path

from .secrets import Keyring, KeyringError
from .state import keyring_path

__all__ = ("autologin_users", "is_autologin", "state_dir", "credential_path", "wrap_path",
           "new_key", "seal_key", "unseal_key", "wrap_key", "unwrap_key", "WrongPassword",
           "password_changed", "wrap_is_stale", "stored_unencrypted", "ensure",
           "enrol_user", "pam_rewrap", "read_secrets", "no_core_dumps", "Result")

log = logging.getLogger("luma-keyring")

GDM_CONFIG = Path("/etc/gdm/custom.conf")
GREETD_CONFIG = Path("/etc/greetd/config.toml")
CRYPTTAB = Path("/etc/crypttab")
SHADOW = Path("/etc/shadow")
CREDENTIAL_NAME = "luma-login-keyring"
SYSTEMD_CREDS = "/usr/bin/systemd-creds"
# gnome-keyring is socket- or bus-activated; this is a bound, not a wait.
DAEMON_WAIT_SECONDS = 2
WRAP_MAGIC = "luma-login-keyring-wrap"
WRAP_VERSION = 2
# RFC 9106 section 4, second recommended option: about 0.25 s on a Pi 5.
ARGON2ID = {"memory_cost": 65536, "iterations": 3, "lanes": 4}
# What a wrap may ask for before any work is done: no weaker than OWASP's
# Argon2id floor (19 MiB, t=2), no more than a device can afford.
ARGON2ID_BOUNDS = {"memory_cost": (19456, 1048576), "iterations": (2, 16), "lanes": (1, 16)}


class Result:
    """What ensure() did, as a word for logs and tests."""
    NOT_AUTOLOGIN = "not-autologin"
    NO_DAEMON = "no-daemon"
    ALREADY_UNLOCKED = "already-unlocked"
    CREATED = "created"
    UNLOCKED = "unlocked"
    UNENCRYPTED = "unencrypted"
    NEEDS_PASSWORD = "needs-password"
    SEAL_UNUSABLE = "seal-unusable"


class WrongPassword(Exception):
    """The password does not unwrap the keyring key."""


# -- process hygiene -----------------------------------------------------------
def no_core_dumps() -> None:
    """A process holding a password or key leaves no core and cannot be traced."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE
    except (OSError, ValueError, AttributeError) as error:
        log.info("core dumps could not be switched off: %s", error)


def read_secrets(stream, count: int = 1) -> list[str]:
    """Secrets from a stream, never argv or the environment.

    One secret: the whole input, less one trailing newline or NUL, so a
    password may hold any other character. Several: each ends with a NUL.
    """
    data = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
    if isinstance(data, str):
        data = data.encode("utf-8")
    if count == 1:
        if data.endswith(b"\0") or data.endswith(b"\n"):
            data = data[:-1]
        return [data.decode("utf-8")]
    parts = data.split(b"\0")
    if len(parts) < count or any(parts[count:]):
        raise ValueError(f"expected {count} NUL-terminated secrets")
    return [part.decode("utf-8") for part in parts[:count]]


# -- who signs in automatically ----------------------------------------------
def _true(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes")


def autologin_users(gdm: Path | None = None, greetd: Path | None = None) -> set[str]:
    """Accounts the display manager signs in without a password."""
    users: set[str] = set()

    gdm = gdm or GDM_CONFIG
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(gdm, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError) as error:
        log.info("could not read %s: %s", gdm, error)
    if parser.has_section("daemon"):
        daemon = parser["daemon"]
        for enable, user in (("AutomaticLoginEnable", "AutomaticLogin"),
                             ("TimedLoginEnable", "TimedLogin")):
            name = (daemon.get(user) or "").strip()
            if _true(daemon.get(enable)) and name:
                users.add(name)

    greetd = greetd or GREETD_CONFIG
    try:
        with open(greetd, "rb") as handle:
            initial = tomllib.load(handle).get("initial_session") or {}
        name = str(initial.get("user") or "").strip()
        if name:
            users.add(name)
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as error:
        log.info("could not read %s: %s", greetd, error)

    return users


def is_autologin(user: str | None = None, **paths) -> bool:
    return (user or getpass.getuser()) in autologin_users(**paths)


# -- where the slots live ----------------------------------------------------------
def state_dir(home: Path | None = None) -> Path:
    """Always ~/.local/state/luma-keyring, so the installer, PAM and the
    session agree whatever the session's environment says."""
    return (home or Path.home()) / ".local" / "state" / "luma-keyring"


def credential_path(home: Path | None = None) -> Path:
    return state_dir(home) / "login.cred"


def wrap_path(home: Path | None = None) -> Path:
    return state_dir(home) / "login.wrap"


def pending(path: Path) -> Path:
    return path.with_name(path.name + ".new")


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _no_backup(path: Path) -> None:
    # freedesktop Common Extended Attributes: not to be backed up.
    try:
        os.setxattr(path, "user.xdg.robots.backup", b"false", follow_symlinks=False)
    except OSError:
        pass


def write_private(path: Path, data: bytes) -> None:
    """Write a slot as the calling user: temp file, fsync, rename, fsync dir.

    The temporary file is created with O_EXCL | O_NOFOLLOW after removing
    whatever was there, so a planted link is never followed; rename replaces
    a link rather than writing through it.
    """
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or (directory.stat().st_mode & 0o077):
        raise KeyringError(f"{directory} is not private to this user")
    partial = path.with_name(path.name + ".partial")
    try:
        partial.unlink()
    except FileNotFoundError:
        pass
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    _no_backup(partial)
    os.replace(partial, path)
    _no_backup(directory)
    _fsync_dir(directory)


def promote(*paths: Path) -> None:
    """Put ``*.new`` slots in place of the current ones, durably, in order.

    Callers pass the wrap before the seal: until the seal is promoted, the
    pending seal is still tried, so a crash between the two loses neither.
    """
    for path in paths:
        if pending(path).exists():
            os.replace(pending(path), path)
    for directory in {path.parent for path in paths}:
        _fsync_dir(directory)


def discard_pending(*paths: Path) -> None:
    for path in paths:
        pending(path).unlink(missing_ok=True)


# -- the keyring key -----------------------------------------------------------------
def new_key() -> str:
    """A random keyring key, as the text gnome-keyring takes as a password."""
    return base64.b64encode(random.token_bytes(32)).decode("ascii")


def _creds(*args: str, data: bytes | None = None, user: str | None = None) -> bytes:
    scope = [f"--uid={user}"] if user else ["--user"]
    try:
        done = subprocess.run([SYSTEMD_CREDS, *scope, f"--name={CREDENTIAL_NAME}", *args],
                              input=data, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise KeyringError(f"systemd-creds could not run: {error}") from None
    if done.returncode != 0:
        raise KeyringError(done.stderr.decode(errors="replace").strip()
                           or f"systemd-creds exited {done.returncode}")
    return done.stdout


def disk_encrypted(crypttab: Path | None = None) -> bool:
    """Whether the system has full-disk encryption (it lists a volume in crypttab)."""
    try:
        lines = (crypttab or CRYPTTAB).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.strip() and not line.lstrip().startswith("#") for line in lines)


def seal_args(crypttab: Path | None = None) -> tuple[str, ...]:
    """TPM2 + host key where there is a TPM2 (host key otherwise); PCR 7 with FDE.

    Without full-disk encryption anyone holding the disk can boot something
    else that the same PCR values allow, or read the host key outright, so a
    PCR policy would only add lock-outs; with FDE, binding the seal to the
    Secure Boot state (PCR 7) keeps it from opening under another boot chain.
    A Secure Boot database update then needs the password once.
    """
    pcrs = "7" if disk_encrypted(crypttab) else ""
    return ("--with-key=auto", f"--tpm2-pcrs={pcrs}")


def seal_key(key: str, user: str | None = None) -> bytes:
    """The keyring key sealed to this user on this machine (not yet written)."""
    return _creds(*seal_args(), "encrypt", "-", "-", data=key.encode("ascii"), user=user)


def unseal_key(path: Path | None = None, user: str | None = None) -> str:
    return _creds("decrypt", str(path or credential_path()), "-",
                  user=user).decode("ascii").strip()


def _kdf(password: str, salt: bytes, params: dict) -> bytes:
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    for name, (low, high) in ARGON2ID_BOUNDS.items():
        value = params.get(name)
        if not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{name} out of bounds")
    if len(salt) < 16:
        raise ValueError("salt too short")
    return Argon2id(salt=salt, length=32, iterations=params["iterations"],
                    lanes=params["lanes"],
                    memory_cost=params["memory_cost"]).derive(password.encode("utf-8"))


_HEADER = ("magic", "version", "kdf", "kdf_params", "aead", "salt", "nonce",
           "password_changed", "wrapped_at")


def _aad(record: dict) -> bytes:
    """Every header field, canonically encoded, is authenticated with the key."""
    return json.dumps({name: record.get(name) for name in _HEADER},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def wrap_key(key: str, password: str, params: dict | None = None,
             changed: int | None = None, now: float | None = None) -> bytes:
    """The keyring key, encrypted under a key derived from the account password."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    params = dict(params or ARGON2ID)
    salt, nonce = random.token_bytes(16), random.token_bytes(12)
    record = {"magic": WRAP_MAGIC, "version": WRAP_VERSION, "kdf": "argon2id",
              "kdf_params": params, "aead": "aes-256-gcm",
              "salt": base64.b64encode(salt).decode("ascii"),
              "nonce": base64.b64encode(nonce).decode("ascii"),
              "password_changed": changed,
              "wrapped_at": int(now if now is not None else time.time())}
    sealed = AESGCM(_kdf(password, salt, params)).encrypt(nonce, key.encode("ascii"), _aad(record))
    record["ciphertext"] = base64.b64encode(sealed).decode("ascii")
    return (json.dumps(record, indent=1, sort_keys=True) + "\n").encode("ascii")


def _record(wrapped: bytes) -> dict:
    record = json.loads(wrapped)
    if not isinstance(record, dict):
        raise ValueError("not a wrap")
    if (record.get("magic") != WRAP_MAGIC or record.get("version") != WRAP_VERSION
            or record.get("kdf") != "argon2id" or record.get("aead") != "aes-256-gcm"):
        raise ValueError("unknown format or version")
    return record


def unwrap_key(wrapped: bytes, password: str) -> str:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        record = _record(wrapped)
        salt = base64.b64decode(record["salt"], validate=True)
        nonce = base64.b64decode(record["nonce"], validate=True)
        sealed = base64.b64decode(record["ciphertext"], validate=True)
        if len(nonce) != 12:
            raise ValueError("bad nonce")
        derived = _kdf(password, salt, record["kdf_params"])
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise KeyringError(f"the recovery file cannot be read: {error}") from None
    try:
        return AESGCM(derived).decrypt(nonce, sealed, _aad(record)).decode("ascii")
    except InvalidTag:
        raise WrongPassword() from None


# -- password changes ----------------------------------------------------------
def _shadow_lastchg(user: str) -> int | None:
    try:
        with open(SHADOW, encoding="utf-8") as shadow:
            for line in shadow:
                fields = line.split(":")
                if fields[0] == user and len(fields) > 2 and fields[2].isdigit():
                    return int(fields[2]) * 86400
    except OSError:
        pass
    return None


def password_changed(user: str | None = None) -> int | None:
    """When the account password last changed (Unix time, to the day).

    From /etc/shadow when it can be read (root), else from AccountsService.
    """
    if os.geteuid() == 0:
        return _shadow_lastchg(user or getpass.getuser())
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        path = bus.call_sync("org.freedesktop.Accounts", "/org/freedesktop/Accounts",
                             "org.freedesktop.Accounts", "FindUserById",
                             GLib.Variant("(x)", (os.getuid(),)), GLib.VariantType.new("(o)"),
                             Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        policy = bus.call_sync("org.freedesktop.Accounts", path,
                               "org.freedesktop.Accounts.User", "GetPasswordExpirationPolicy",
                               None, GLib.VariantType.new("(xxxxxx)"),
                               Gio.DBusCallFlags.NONE, 5000, None).unpack()
    except Exception as error:  # noqa: BLE001 - no AccountsService is an answer
        log.info("could not ask when the password changed: %s", error)
        return None
    return int(policy[1]) if policy[1] > 0 else None


def wrap_is_stale(path: Path | None = None, changed: int | None = None,
                  shadow_mtime: float | None = None) -> bool:
    """A fallback for password changes the PAM hook did not see.

    The change date is only to the day, so a change on the day the wrap was
    made counts (``>=`` against the wrap's day) when /etc/shadow was
    written after the wrap -- on a single-person device, that is the
    password changing again.
    """
    try:
        record = json.loads((path or wrap_path()).read_bytes())
    except (OSError, ValueError):
        return False
    changed = password_changed() if changed is None else changed
    if changed is None:
        return False
    recorded = record.get("password_changed")
    wrapped_at = record.get("wrapped_at") or 0
    if not isinstance(recorded, int) or changed > recorded:
        return True
    if shadow_mtime is None:
        try:
            shadow_mtime = SHADOW.stat().st_mtime
        except OSError:
            return False
    wrapped_day = wrapped_at - wrapped_at % 86400
    return changed >= wrapped_day and shadow_mtime > wrapped_at


# -- writing as the account -----------------------------------------------------
def _as_user(account: pwd.struct_passwd, work) -> None:
    """Run `work` as the account (root callers), so its home is never written as root."""
    if os.geteuid() != 0:
        if os.geteuid() != account.pw_uid:
            raise KeyringError("refusing to write another account's keyring slots")
        work()
        return
    child = os.fork()
    if child == 0:
        code = 1
        try:
            os.setgroups([])
            os.setresgid(account.pw_gid, account.pw_gid, account.pw_gid)
            os.setresuid(account.pw_uid, account.pw_uid, account.pw_uid)
            os.environ["HOME"] = account.pw_dir
            work()
            code = 0
        except BaseException as error:  # noqa: BLE001 - the parent reports it
            os.write(2, f"luma-keyring: {error}\n".encode(errors="replace"))
        finally:
            os._exit(code)
    _, status = os.waitpid(child, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise KeyringError("the keyring slots could not be written as the account")


def enrol_user(user: str, password: str) -> Path:
    """The installer's step, as root: a keyring key sealed and wrapped for USER.

    Sealing needs root (``--uid``); the files are written by a child running
    as the account. The login keyring itself is made with the key at the
    first automatic sign-in, by the session's own keyring daemon.
    """
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    key = new_key()
    sealed = seal_key(key, user=user)
    wrapped = wrap_key(key, password, changed=password_changed(user))

    def write() -> None:
        local = home / ".local"
        local.mkdir(mode=0o755, exist_ok=True)
        (local / "state").mkdir(mode=0o700, exist_ok=True)
        write_private(wrap_path(home), wrapped)
        write_private(credential_path(home), sealed)

    _as_user(account, write)
    return state_dir(home)


def pam_rewrap(user: str, new_password: str) -> bool:
    """The PAM password stack, at the moment the password changes.

    pam_exec runs this with the new password on stdin, as the person (their
    own change) or as root (an administrator's). The key is unsealed on the
    device, so the old password is not needed; the wrap now opens with the
    new one. An account without slots is left alone.
    """
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    if os.geteuid() not in (0, account.pw_uid):
        raise KeyringError("refusing to re-wrap another account's keyring key")
    credential = credential_path(home)
    if not credential.exists() and not pending(credential).exists():
        return True
    scope = user if os.geteuid() == 0 else None
    today = int(time.time()) // 86400 * 86400
    # Each seal keeps its own wrap: a pending pair stays a pair, so a later
    # promotion never puts back a wrap with the old password.
    rewrapped = {}
    for seal_path, wrap in ((credential, wrap_path(home)),
                            (pending(credential), pending(wrap_path(home)))):
        if not seal_path.exists():
            continue
        try:
            key = unseal_key(seal_path, user=scope)
        except KeyringError as error:
            log.info("%s did not open: %s", seal_path.name, error)
            continue
        rewrapped[wrap] = wrap_key(key, new_password, changed=today)
    if not rewrapped:
        return False

    def write() -> None:
        for wrap, data in rewrapped.items():
            write_private(wrap, data)

    _as_user(account, write)
    return True


def stored_unencrypted() -> bool:
    """gnome-keyring writes a keyring with an empty password as plain text."""
    try:
        with open(keyring_path(), "rb") as handle:
            return handle.read(9) == b"[keyring]"
    except OSError:
        return False


# -- the session-start step ----------------------------------------------------
def _wait_for_daemon(keyring: Keyring, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if keyring.running():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def open_with_slots(keyring: Keyring, unseal=unseal_key, create: bool = False) -> str | None:
    """Unlock with whichever seal opens the keyring; promote a ``*.new`` that did.

    Returns the Result, or None when no seal is on disk.
    """
    credential = credential_path()
    candidates = [path for path in (credential, pending(credential)) if path.exists()]
    if not candidates:
        return None
    exists = keyring_path().exists()
    for candidate in candidates:
        try:
            key = unseal(candidate)
        except KeyringError as error:
            log.warning("%s could not be opened: %s", candidate.name, error)
            continue
        if not exists:
            if not create:
                return Result.NOT_AUTOLOGIN
            if candidate != credential:
                promote(wrap_path(), credential)
            keyring.create_login(key)
            return Result.CREATED
        try:
            keyring.unlock_with(key)
        except KeyringError as error:
            log.info("%s did not open the keyring: %s", candidate.name, error)
            continue
        if candidate == credential:
            # A key change that never reached the keyring: its slots are stale.
            discard_pending(credential, wrap_path())
        else:
            promote(wrap_path(), credential)
        return Result.UNLOCKED
    return Result.SEAL_UNUSABLE


def ensure(keyring: Keyring | None = None, autologin: bool | None = None,
           unseal=unseal_key, wait: float = DAEMON_WAIT_SECONDS) -> str:
    """Open the login keyring without a prompt where that is the policy."""
    autologin = is_autologin() if autologin is None else autologin
    credential = credential_path()
    sealed = credential.exists() or pending(credential).exists()
    if not autologin and not sealed:
        return Result.NOT_AUTOLOGIN

    keyring = keyring or Keyring()
    if not _wait_for_daemon(keyring, 0.5):
        keyring.activate()
        if not _wait_for_daemon(keyring, wait):
            return Result.NO_DAEMON

    collection = keyring.collection()
    if collection is not None and not collection.locked:
        return Result.ALREADY_UNLOCKED

    if sealed:
        # Also after automatic sign-in is turned off: the keyring is on the
        # keyring key until the person moves it back.
        return open_with_slots(keyring, unseal, create=autologin)

    if stored_unencrypted():
        try:
            keyring.unlock_with("")
            return Result.UNENCRYPTED
        except KeyringError:
            pass
    return Result.NEEDS_PASSWORD
