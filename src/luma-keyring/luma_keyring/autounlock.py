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
  where the root file system is encrypted; without that a PCR policy
  protects nothing an attacker with the disk could not undo, so none is
  used there. Without a TPM2 it is bound to the host key alone.
* ``login.wrap`` -- the recovery slot: the key encrypted with AES-256-GCM
  under Argon2id (RFC 9106: 64 MiB, t=3, p=4) of the account password, both
  from python-cryptography (OpenSSL). Versioned; the whole header is
  authenticated as associated data. It is an offline password verifier, so
  the folder is kept out of backups (``.deja-dup-ignore``, and
  ``user.xdg.robots.backup=false`` for tools that read it).
* ``login.wrap.prev`` -- the wrap a password change replaced, kept until the
  session sees the account's own password hash match the new wrap.
* ``luma-keyring-recover`` -- a copy of the standalone recovery script, so a
  rollback to a luma-keyring that predates the slots still has it.

The account password itself is never kept in any form that can be turned
back into it, and secrets reach this module on stdin only, never argv, the
environment or a log.

Enrolment happens where the password is in hand: the installer
(``enrol_user``), the PAM password stack at a password change
(``pam_rewrap``, run by pam_luma_keyring.so only once the new password is
in /etc/shadow), or once in the session on devices set up earlier
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
import hashlib
import hmac
import json
import logging
import os
import pwd
import secrets as random
import stat
import subprocess
import time
import tomllib
from pathlib import Path

from .secrets import Keyring, KeyringError
from .state import keyring_path

__all__ = ("autologin_users", "is_autologin", "state_dir", "credential_path", "wrap_path",
           "previous", "read_slot", "root_encrypted", "new_key", "seal_key", "unseal_key",
           "wrap_key", "make_wrap", "unwrap_key", "password_id", "WrongPassword",
           "password_changed", "wrap_is_stale", "stored_unencrypted", "ensure",
           "enrol_user", "pam_rewrap", "install_recovery_tool", "read_secrets",
           "no_core_dumps", "Result")

log = logging.getLogger("luma-keyring")

GDM_CONFIG = Path("/etc/gdm/custom.conf")
GREETD_CONFIG = Path("/etc/greetd/config.toml")
MOUNTINFO = Path("/proc/self/mountinfo")
SYSFS = Path("/sys")
SHADOW = Path("/etc/shadow")
CREDENTIAL_NAME = "luma-login-keyring"
SYSTEMD_CREDS = "/usr/bin/systemd-creds"
USERDBCTL = "/usr/bin/userdbctl"
# Installed with the package; copied next to the slots at enrolment.
RECOVERY_TOOL = Path("/usr/share/luma-keyring/luma-keyring-recover-standalone")
RECOVERY_COPY = "luma-keyring-recover"
# Deja Dup (42.1 and later) leaves out any folder holding this file.
BACKUP_IGNORE = ".deja-dup-ignore"
# A seal is a few hundred bytes; anything far larger is not one.
SLOT_MAX_BYTES = 1 << 20
# gnome-keyring is socket- or bus-activated; this is a bound, not a wait.
DAEMON_WAIT_SECONDS = 2
WRAP_MAGIC = "luma-login-keyring-wrap"
WRAP_VERSION = 3
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


def recovery_copy_path(home: Path | None = None) -> Path:
    return state_dir(home) / RECOVERY_COPY


def credential_path(home: Path | None = None) -> Path:
    return state_dir(home) / "login.cred"


def wrap_path(home: Path | None = None) -> Path:
    return state_dir(home) / "login.wrap"


def pending(path: Path) -> Path:
    return path.with_name(path.name + ".new")


def previous(path: Path) -> Path:
    """The wrap a password change replaced, until the change is confirmed."""
    return path.with_name(path.name + ".prev")


def read_slot(path: Path) -> bytes | None:
    """A slot's bytes, or None when there is none.

    Opened without following a link and without blocking, and only read if
    it is a regular file of sane size: a FIFO or device planted in its place
    can neither hang nor feed the caller.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise KeyringError(f"{path.name} cannot be opened: {error}") from None
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > SLOT_MAX_BYTES:
            raise KeyringError(f"{path.name} is not a key slot")
        return handle.read(SLOT_MAX_BYTES + 1)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _no_backup(path: Path) -> None:
    # freedesktop Common Extended Attributes: not to be backed up. Few backup
    # tools read it; the folder's marker file below is what Deja Dup honours.
    try:
        os.setxattr(path, "user.xdg.robots.backup", b"false", follow_symlinks=False)
    except OSError:
        pass


def _mark_folder_no_backup(directory: Path) -> None:
    marker = directory / BACKUP_IGNORE
    try:
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600))
    except FileExistsError:
        pass
    except OSError as error:
        log.info("could not mark %s as not for backup: %s", directory, error)
    _no_backup(directory)


def write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
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
        if mode != 0o600:
            os.fchmod(handle.fileno(), mode & 0o700)
        os.fsync(handle.fileno())
    _no_backup(partial)
    os.replace(partial, path)
    _mark_folder_no_backup(directory)
    _fsync_dir(directory)


def promote(*paths: Path) -> None:
    """Put ``*.new`` slots in place of the current ones, durably, in order.

    Callers pass the wrap before the seal: until the seal is promoted, the
    pending seal is still tried, so a crash between the two loses neither.
    A previous wrap belongs to the key being replaced and goes with it.
    """
    for path in paths:
        if pending(path).exists():
            os.replace(pending(path), path)
            previous(path).unlink(missing_ok=True)
    for directory in {path.parent for path in paths}:
        _fsync_dir(directory)


def discard_pending(*paths: Path) -> None:
    for path in paths:
        pending(path).unlink(missing_ok=True)


def install_recovery_tool(home: Path | None = None) -> None:
    """Keep a copy of the standalone recovery script next to the slots.

    /usr/share goes with the deployment: after a rollback to a luma-keyring
    that predates the slots, the copy here is what is left.
    """
    try:
        data = RECOVERY_TOOL.read_bytes()
    except OSError:
        return
    target = recovery_copy_path(home)
    try:
        if read_slot(target) == data:
            return
    except KeyringError:
        pass
    try:
        write_private(target, data, mode=0o700)
    except (OSError, KeyringError) as error:
        log.info("the recovery script could not be copied: %s", error)


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


def _unescape_mount(text: str) -> str:
    # /proc/self/mountinfo escapes space, tab, newline and backslash as \ooo.
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and text[i + 1:i + 4].isdigit():
            out.append(chr(int(text[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _mount_source(target: str, mountinfo: Path) -> str | None:
    source = None
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) < 10 or "-" not in fields[6:]:
            continue
        separator = fields.index("-", 6)
        if _unescape_mount(fields[4]) == target and len(fields) > separator + 2:
            source = _unescape_mount(fields[separator + 2])  # the last mount wins
    return source


def _block_encrypted(device: str, sysfs: Path, seen: set[str] | None = None) -> bool:
    """Whether a block device is, or sits on, a dm-crypt mapping."""
    seen = set() if seen is None else seen
    if device in seen or len(seen) > 64:
        return False
    seen.add(device)
    node = sysfs / "dev" / "block" / device
    try:
        if (node / "dm" / "uuid").read_text(encoding="ascii").startswith("CRYPT-"):
            return True
    except OSError:
        pass
    try:
        lower = list((node / "slaves").iterdir())
    except OSError:
        lower = []
    for slave in lower:
        try:
            number = (slave / "dev").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if _block_encrypted(number, sysfs, seen):
            return True
    return False


def root_encrypted(mountinfo: Path | None = None, sysfs: Path | None = None,
                   device_number=None) -> bool:
    """Whether the root file system itself is on dm-crypt (LUKS).

    A line in /etc/crypttab can be any volume (a data disk, swap); what the
    seal policy turns on is whether the host key under /var is encrypted at
    rest. On OSTree systems the real root is /sysroot. The mount source is
    followed through device-mapper and its lower devices in sysfs, so LVM on
    LUKS and btrfs (whose st_dev is anonymous) are both seen.
    """
    mountinfo = mountinfo or MOUNTINFO
    sysfs = sysfs or SYSFS
    for target in ("/sysroot", "/"):
        source = _mount_source(target, mountinfo)
        if not source or not source.startswith("/dev/"):
            continue
        try:
            number = device_number(source) if device_number else os.stat(source).st_rdev
        except OSError:
            continue
        if isinstance(number, int):
            number = f"{os.major(number)}:{os.minor(number)}"
        return _block_encrypted(number, sysfs)
    return False


def seal_args(encrypted: bool | None = None) -> tuple[str, ...]:
    """TPM2 + host key where there is a TPM2 (host key otherwise); PCR 7 when
    the root file system is encrypted.

    Without it anyone holding the disk can read the host key outright, so a
    PCR policy would only add lock-outs. With it, PCR 7 (the Secure Boot
    state and the authorities that verified the boot) keeps the seal shut
    when Secure Boot is off or other keys are enrolled. It does not tell
    apart other media signed under the same chain (a distribution live
    image); those are kept out by the host key being on the encrypted root.
    A Secure Boot database update then needs the password once.
    """
    encrypted = root_encrypted() if encrypted is None else encrypted
    pcrs = "7" if encrypted else ""
    return ("--with-key=auto", f"--tpm2-pcrs={pcrs}")


def seal_key(key: str, user: str | None = None) -> bytes:
    """The keyring key sealed to this user on this machine (not yet written)."""
    return _creds(*seal_args(), "encrypt", "-", "-", data=key.encode("ascii"), user=user)


def unseal_key(path: Path | None = None, user: str | None = None) -> str:
    """Open a seal. The file is read here (see read_slot) and handed over on
    standard input, so systemd-creds never opens a path itself."""
    path = path or credential_path()
    data = read_slot(path)
    if data is None:
        raise KeyringError(f"{path.name} is missing")
    return _creds("decrypt", "-", "-", data=data, user=user).decode("ascii").strip()


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


# Version 2 wraps (written before password_id) still open; version 3 adds it.
_HEADERS = {
    2: ("magic", "version", "kdf", "kdf_params", "aead", "salt", "nonce",
        "password_changed", "wrapped_at"),
    3: ("magic", "version", "kdf", "kdf_params", "aead", "salt", "nonce",
        "password_changed", "wrapped_at", "password_id"),
}
PASSWORD_ID_CONTEXT = b"luma-login-keyring/password-id/v1\0"


def _aad(record: dict) -> bytes:
    """Every header field, canonically encoded, is authenticated with the key."""
    return json.dumps({name: record.get(name) for name in _HEADERS[record["version"]]},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def password_id(key: str, hashed: str | None) -> str | None:
    """Which password hash a wrap was made for, without revealing it.

    HMAC-SHA256 under the keyring key of the account's crypt(3) hash: equal
    for the same hash entry, so the session can tell whether the password
    changed since the wrap, and useless without the key, so it is no
    password verifier even for a weak legacy hash.
    """
    if not hashed:
        return None
    digest = hmac.new(key.encode("ascii"), PASSWORD_ID_CONTEXT + hashed.encode("utf-8"),
                      hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def wrap_key(key: str, password: str, params: dict | None = None,
             changed: int | None = None, now: float | None = None,
             hashed: str | None = None) -> bytes:
    """The keyring key, encrypted under a key derived from the account password.

    ``hashed`` is the account's password hash where the caller can read it,
    recorded only as password_id.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    params = dict(params or ARGON2ID)
    salt, nonce = random.token_bytes(16), random.token_bytes(12)
    record = {"magic": WRAP_MAGIC, "version": WRAP_VERSION, "kdf": "argon2id",
              "kdf_params": params, "aead": "aes-256-gcm",
              "salt": base64.b64encode(salt).decode("ascii"),
              "nonce": base64.b64encode(nonce).decode("ascii"),
              "password_changed": changed,
              "wrapped_at": int(now if now is not None else time.time()),
              "password_id": password_id(key, hashed)}
    sealed = AESGCM(_kdf(password, salt, params)).encrypt(nonce, key.encode("ascii"), _aad(record))
    record["ciphertext"] = base64.b64encode(sealed).decode("ascii")
    return (json.dumps(record, indent=1, sort_keys=True) + "\n").encode("ascii")


def _record(wrapped: bytes) -> dict:
    record = json.loads(wrapped)
    if not isinstance(record, dict):
        raise ValueError("not a wrap")
    if (record.get("magic") != WRAP_MAGIC or record.get("version") not in _HEADERS
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
def _shadow_entry(user: str) -> list[str] | None:
    try:
        with open(SHADOW, encoding="utf-8") as shadow:
            for line in shadow:
                fields = line.rstrip("\n").split(":")
                if fields[0] == user and len(fields) > 2:
                    return fields
    except OSError:
        pass
    return None


def _shadow_lastchg(user: str) -> int | None:
    fields = _shadow_entry(user)
    if fields and fields[2].isdigit():
        return int(fields[2]) * 86400
    return None


def shadow_hash(user: str) -> str | None:
    """The account's crypt(3) hash from /etc/shadow (root only)."""
    fields = _shadow_entry(user)
    return fields[1] if fields and fields[1] else None


def own_password_hash() -> str | None:
    """The calling account's own hash, from systemd-userdbd.

    userdbd includes a record's privileged section (hashedPassword) only for
    root and for the account itself, read from /etc/shadow on its behalf.
    None where there is no userdbd or no such section.
    """
    try:
        done = subprocess.run([USERDBCTL, "--output=json", "user", str(os.getuid())],
                              capture_output=True, timeout=5, check=False)
        if done.returncode != 0:
            return None
        record = json.loads(done.stdout)
        hashes = (record.get("privileged") or {}).get("hashedPassword") or []
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired) as error:
        log.info("could not read this account's password hash: %s", error)
        return None
    return hashes[0] if hashes and isinstance(hashes[0], str) and hashes[0] else None


def account_password_hash(user: str | None = None) -> str | None:
    """The account's hash, however this process may read it."""
    if os.geteuid() == 0:
        return shadow_hash(user or getpass.getuser())
    return own_password_hash()


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


def make_wrap(key: str, password: str, user: str | None = None) -> bytes:
    """A wrap recording when the password changed and which hash it matches."""
    return wrap_key(key, password, changed=password_changed(user),
                    hashed=account_password_hash(user))


def _header(path: Path) -> dict | None:
    """A wrap's header, unauthenticated (no password here): a hint only."""
    try:
        data = read_slot(path)
        record = json.loads(data) if data is not None else None
    except (KeyringError, ValueError):
        return None
    return record if isinstance(record, dict) else None


_AUTO = object()


def _same(recorded, expected: str) -> bool:
    if not isinstance(recorded, str):
        return False
    return hmac.compare_digest(recorded.encode("utf-8"), expected.encode("utf-8"))


def wrap_is_stale(path: Path | None = None, changed: int | None = None,
                  hashed=_AUTO, unseal=None) -> bool:
    """Whether the password changed since the wrap was made (a change the
    PAM module did not see), settling a pending password change on the way.

    Where the account's own hash can be read (userdbd), it is compared with
    the wrap's password_id under the keyring key: equal confirms the wrap
    and drops the previous one; equal to the previous wrap's means the
    change did not stick (an /etc rolled back), so the previous wrap is put
    back. Otherwise the change day is compared, and only a later day counts:
    nothing that merely writes /etc/shadow raises a prompt.
    """
    path = path or wrap_path()
    record = _header(path)
    if record is None:
        return False
    if hashed is _AUTO:
        hashed = own_password_hash()
    if hashed and record.get("password_id"):
        try:
            key = (unseal or unseal_key)(path.with_name(credential_path().name))
        except KeyringError as error:
            log.info("the seal did not open to compare the password: %s", error)
            key = None
        if key is not None:
            now = password_id(key, hashed)
            if _same(record.get("password_id"), now):
                previous(path).unlink(missing_ok=True)
                return False
            earlier = _header(previous(path))
            if earlier and _same(earlier.get("password_id"), now):
                log.info("the password change did not stick; the previous wrap is back")
                os.replace(previous(path), path)
                _fsync_dir(path.parent)
                return False
            return True
    changed = password_changed() if changed is None else changed
    if changed is None:
        return False
    recorded = record.get("password_changed")
    if not isinstance(recorded, int) or changed > recorded:
        return True
    if changed == recorded:
        previous(path).unlink(missing_ok=True)
    return False


# -- writing as the account -----------------------------------------------------
def _as_user(account: pwd.struct_passwd, work) -> bool:
    """Run `work` as the account (root callers), so its home is never touched as root.

    Returns False when `work` returned False, True otherwise.
    """
    if os.geteuid() != 0:
        if os.geteuid() != account.pw_uid:
            raise KeyringError("refusing to write another account's keyring slots")
        return work() is not False
    child = os.fork()
    if child == 0:
        code = 1
        try:
            os.setgroups([])
            os.setresgid(account.pw_gid, account.pw_gid, account.pw_gid)
            os.setresuid(account.pw_uid, account.pw_uid, account.pw_uid)
            os.environ["HOME"] = account.pw_dir
            os.chdir("/")
            code = 3 if work() is False else 0
        except BaseException as error:  # noqa: BLE001 - the parent reports it
            os.write(2, f"luma-keyring: {error}\n".encode(errors="replace"))
        finally:
            os._exit(code)
    _, status = os.waitpid(child, 0)
    code = os.waitstatus_to_exitcode(status)
    if code == 3:
        return False
    if code != 0:
        raise KeyringError("the keyring slots could not be written as the account")
    return True


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
    wrapped = make_wrap(key, password, user)

    def write() -> None:
        local = home / ".local"
        local.mkdir(mode=0o755, exist_ok=True)
        (local / "state").mkdir(mode=0o700, exist_ok=True)
        write_private(wrap_path(home), wrapped)
        write_private(credential_path(home), sealed)
        install_recovery_tool(home)

    _as_user(account, write)
    return state_dir(home)


def pam_rewrap(user: str, new_password: str) -> bool:
    """The password stack, once the new password is the account's.

    pam_luma_keyring.so runs this only after the new password verified
    against /etc/shadow, as root (passwd is set-user-ID) or as the person.
    Everything that touches the account's files -- reading the seals,
    opening them with ``systemd-creds --user``, writing the wraps -- runs as
    the account, so nothing planted there is opened with root's rights. The
    key is unsealed on the device, so the old password is not needed.

    The last confirmed wrap is kept as ``login.wrap.prev`` until the session
    confirms the change (wrap_is_stale). An existing ``.prev`` means the
    current wrap is itself unconfirmed, so it is kept rather than replaced:
    after several unconfirmed changes and an /etc rolled back, the
    confirmed password still opens it. Each pending seal keeps its own
    wrap, so a later promotion never puts back a wrap with an old password.
    An account without slots is left alone. False when no seal opened.
    """
    account = pwd.getpwnam(user)
    if os.geteuid() not in (0, account.pw_uid):
        raise KeyringError("refusing to re-wrap another account's keyring key")
    home = Path(account.pw_dir)
    hashed = account_password_hash(user)
    changed = password_changed(user)
    if changed is None:
        changed = int(time.time()) // 86400 * 86400

    def work() -> bool:
        credential, wrap = credential_path(home), wrap_path(home)
        found = opened = False
        for seal_path, wrap_slot in ((credential, wrap), (pending(credential), pending(wrap))):
            if not os.path.lexists(seal_path):
                continue
            found = True
            try:
                key = unseal_key(seal_path)
            except KeyringError as error:
                log.info("%s did not open: %s", seal_path.name, error)
                continue
            rewrapped = wrap_key(key, new_password, changed=changed, hashed=hashed)
            if wrap_slot == wrap and not os.path.lexists(previous(wrap)):
                try:
                    current = read_slot(wrap)
                except KeyringError:
                    current = None
                if current is not None:
                    write_private(previous(wrap), current)
            write_private(wrap_slot, rewrapped)
            opened = True
        if opened:
            install_recovery_tool(home)
        return opened or not found

    return _as_user(account, work)


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
            install_recovery_tool()
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
        install_recovery_tool()
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
