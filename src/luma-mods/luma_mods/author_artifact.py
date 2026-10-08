"""Bounded deterministic artifact identities for the Mod authoring kit."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import LumaModsError

MAX_ARTIFACT_FILES = 20_000
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class ArtifactIdentity:
    sha256: str
    files: int
    bytes: int


def _regular_digest(path: Path) -> tuple[str, int]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise LumaModsError(f"cannot open artifact safely: {path}: {error.strerror}") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise LumaModsError(f"artifact entry is not a regular file: {path}")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise LumaModsError("artifact exceeds the authoring size limit")
            digest.update(chunk)
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def identity(path: Path) -> ArtifactIdentity:
    """Hash one regular file or a canonical, symlink-free directory tree."""

    try:
        root_info = path.lstat()
    except OSError as error:
        raise LumaModsError(f"artifact is unavailable: {path}: {error.strerror}") from error
    if stat.S_ISREG(root_info.st_mode):
        digest, size = _regular_digest(path)
        return ArtifactIdentity(digest, 1, size)
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        raise LumaModsError("artifact must be a regular file or real directory")

    entries: list[tuple[str, Path]] = []
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            candidate = current_path / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise LumaModsError(f"artifact tree must not contain symlinks: {candidate}")
            if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                raise LumaModsError(f"artifact tree contains an unsupported entry: {candidate}")
        for name in files:
            candidate = current_path / name
            entries.append((candidate.relative_to(path).as_posix(), candidate))
            if len(entries) > MAX_ARTIFACT_FILES:
                raise LumaModsError("artifact tree exceeds the file-count limit")

    aggregate = hashlib.sha256()
    total = 0
    for relative, candidate in sorted(entries):
        digest, size = _regular_digest(candidate)
        total += size
        if total > MAX_ARTIFACT_BYTES:
            raise LumaModsError("artifact tree exceeds the authoring size limit")
        name = relative.encode("utf-8")
        aggregate.update(len(name).to_bytes(4, "big"))
        aggregate.update(name)
        aggregate.update(size.to_bytes(8, "big"))
        aggregate.update(bytes.fromhex(digest))
    return ArtifactIdentity(aggregate.hexdigest(), len(entries), total)
