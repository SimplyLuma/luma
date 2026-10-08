from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import InstallerError

MAX_PACKAGE_BYTES = 8 * 1024 * 1024 * 1024
READ_CHUNK = 1024 * 1024
MINIMUM_SPACE_HEADROOM = 64 * 1024 * 1024


def _validate_fingerprint(sha256: str) -> None:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise InstallerError("The transaction fingerprint is invalid.")


def staging_root(sha256: str) -> Path:
    _validate_fingerprint(sha256)
    return Path.home() / ".local/share/luma/installer/staging" / sha256


@contextmanager
def transaction_lock(sha256: str) -> Iterator[None]:
    """Serialize one reviewed payload without blocking unrelated installs."""
    root = staging_root(sha256)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = root / ".transaction.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallerError("This application package already has an active transaction.") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def cleanup_abandoned_user_transactions() -> None:
    """Remove incomplete user payloads only after proving no owner is alive."""
    staging = Path.home() / ".local/share/luma/installer/staging"
    appimages = Path.home() / ".local/share/luma/appimages"
    if not staging.is_dir() or staging.is_symlink():
        return
    for root in staging.iterdir():
        if not root.is_dir() or root.is_symlink():
            continue
        try:
            _validate_fingerprint(root.name)
            metadata = root.stat()
            if metadata.st_uid != os.getuid():
                continue
            lock_path = root / ".transaction.lock"
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        except (OSError, InstallerError):
            continue
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            for partial in root.glob(".*.partial"):
                try:
                    partial_metadata = partial.lstat()
                    if partial_metadata.st_uid == os.getuid() and stat.S_ISREG(partial_metadata.st_mode):
                        partial.unlink()
                except OSError:
                    continue
            appimage_partial = appimages / root.name / ".AppDir.partial"
            try:
                partial_metadata = appimage_partial.lstat()
            except OSError:
                continue
            if partial_metadata.st_uid != os.getuid():
                continue
            if stat.S_ISLNK(partial_metadata.st_mode):
                appimage_partial.unlink(missing_ok=True)
            elif stat.S_ISDIR(partial_metadata.st_mode):
                shutil.rmtree(appimage_partial, ignore_errors=True)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _existing_storage_root(path: Path) -> Path:
    """Return the nearest existing parent without crossing filesystem policy."""
    candidate = path.expanduser().absolute()
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return candidate


def require_free_space(path: Path, payload_bytes: int, purpose: str) -> None:
    """Reject a transaction before writing when its destination is clearly full.

    This is intentionally a lower-bound check, not a promise about a package's
    eventual installed size. Ecosystem backends still own their authoritative
    quota and ENOSPC handling. The extra headroom prevents Luma's own staging
    copy from consuming the final usable bytes of the user's filesystem.
    """
    if payload_bytes < 0:
        raise InstallerError("The package reported an invalid size.")
    required = payload_bytes + max(MINIMUM_SPACE_HEADROOM, payload_bytes // 10)
    try:
        available = shutil.disk_usage(_existing_storage_root(path)).free
    except OSError as error:
        raise InstallerError(f"Available storage could not be checked: {error.strerror}") from error
    if available < required:
        required_gib = required / (1024 ** 3)
        available_gib = available / (1024 ** 3)
        raise InstallerError(
            f"Not enough free space to {purpose}. "
            f"At least {required_gib:.1f} GB is required, but {available_gib:.1f} GB is available."
        )


def open_regular(path: Path, maximum: int = MAX_PACKAGE_BYTES) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise InstallerError(f"The package cannot be opened safely: {error.strerror}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise InstallerError("The package must be a regular local file.")
        if metadata.st_size <= 0:
            raise InstallerError("The package is empty.")
        if metadata.st_size > maximum:
            raise InstallerError("The package exceeds Luma's 8 GB safety limit.")
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def fingerprint(path: Path) -> tuple[int, str]:
    descriptor, metadata = open_regular(path)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            for chunk in iter(lambda: stream.read(READ_CHUNK), b""):
                digest.update(chunk)
    except OSError as error:
        raise InstallerError(f"The package could not be read: {error.strerror}") from error
    return metadata.st_size, digest.hexdigest()


def stage_user_copy(path: Path, sha256: str) -> Path:
    root = staging_root(sha256)
    destination = root / path.name
    temporary = root / f".{path.name}.partial"
    if destination.is_file() and fingerprint(destination)[1] == sha256:
        return destination
    descriptor, metadata = open_regular(path)
    require_free_space(root, metadata.st_size, "stage this application package")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target, READ_CHUNK)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o600)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    if fingerprint(destination)[1] != sha256:
        destination.unlink(missing_ok=True)
        raise InstallerError("The staged package did not match its reviewed fingerprint.")
    return destination
