"""Explicit, optional FEX backend; the default Relay install remains native.

Only the companion RPM admits a rootfs. Extraction handles trusted runtime
bytes, never an application's installer, and needs no privileged mount helper.
The resulting shared cache is bound read-only inside each private capsule.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from .config import cache_home
from .errors import RelayError

PROFILE = Path("/usr/share/luma-relay/backends/fex.json")
IMAGE = Path("/usr/share/fex-emu/RootFS/default.erofs")
INTERPRETER = Path("/usr/bin/FEXInterpreter")
SANDBOX_ROOTFS = "/relay-rootfs"


def available() -> bool:
    return (
        platform.machine().lower() in {"aarch64", "arm64"}
        and os.sysconf("SC_PAGE_SIZE") == 4096
        and PROFILE.is_file()
        and IMAGE.is_file()
        and INTERPRETER.is_file()
        and shutil.which("fsck.erofs") is not None
    )


def _trusted_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RelayError("Relay's FEX runtime must be installed by the package manager.")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RelayError("Relay's runtime cache must be a private directory.")


def prepare_rootfs() -> Path:
    """Atomically materialize the exact admitted image with bounded I/O.

    No FUSE service, global FEX socket, binfmt registration, or home-directory
    grant is needed. An interrupted extraction is never admitted as complete.
    """
    if not available():
        raise RelayError("The optional ARM Windows runtime is not installed or needs 4 KiB pages.")
    try:
        _trusted_file(PROFILE)
        _trusted_file(IMAGE)
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        expected = profile["rootfs_sha256"]
        if not isinstance(expected, str) or len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            raise ValueError("invalid image fingerprint")
        cache = cache_home() / "luma-relay" / "fex"
        _private_directory(cache)
        lock_fd = os.open(cache / "prepare.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            destination = cache / expected
            if destination.exists() or destination.is_symlink():
                _private_directory(destination)
                if (destination / ".luma-complete").read_text() == expected:
                    return destination
                raise RelayError("Relay's cached ARM runtime is incomplete; remove its cache and retry.")
            image_fd = os.open(IMAGE, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(image_fd, "rb") as image:
                digest = hashlib.file_digest(image, "sha256").hexdigest()
                if digest != expected:
                    raise RelayError("The installed FEX filesystem does not match the admitted version.")
                image.seek(0)
                temporary = Path(tempfile.mkdtemp(prefix=".extract-", dir=cache))
                try:
                    result = subprocess.run(
                        ["fsck.erofs", f"--extract={temporary}", f"/proc/self/fd/{image.fileno()}"],
                        pass_fds=(image.fileno(),), check=False, capture_output=True, text=True,
                        timeout=600,
                    )
                    if result.returncode:
                        raise RelayError("Relay could not prepare the ARM Windows filesystem.")
                    # Check concrete binaries: Fedora's wine alternatives use
                    # absolute symlinks resolved inside FEX, not on the host.
                    for program in ("usr/bin/wine64", "usr/bin/wineserver64", "usr/bin/bash"):
                        if not (temporary / program).is_file():
                            raise RelayError("The FEX filesystem lacks its expected Wine runtime.")
                    temporary.chmod(0o700)
                    (temporary / ".luma-complete").write_text(expected)
                    temporary.rename(destination)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
                return destination
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise RelayError(f"Relay could not prepare its ARM Windows runtime: {error}") from error


def environment() -> dict[str, str]:
    return {
        "FEX_ROOTFS": SANDBOX_ROOTFS,
        "FEX_SERVERSOCKETPATH": "/relay/run/fex-server.sock",
        "FEX_APP_CONFIG_LOCATION": "/relay/fex/config",
        "FEX_APP_DATA_LOCATION": "/relay/fex/data",
        "FEX_APP_CACHE_LOCATION": "/relay/cache/fex",
    }
