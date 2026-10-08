"""Narrow rpm-ostree deployment adapter for reviewed system Mod requests.

This module contains no shell evaluation and no publisher-controlled argv.
The packaged service remains closed until the recovery/boot-health release gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .errors import TransactionError
from .privileged import ArtifactRequest, SystemCompositionRequest

MAX_STATUS_BYTES = 4 * 1024 * 1024


def _run(command: Sequence[str]) -> str:
    result = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown failure"
        raise TransactionError(f"system deployment command failed: {message}")
    return result.stdout


@dataclass(frozen=True)
class ArtifactStore:
    root: Path = Path("/var/lib/luma/mods/artifacts/sha256")

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise TransactionError("verified artifact store must be a real directory")
        os.chmod(self.root, 0o700)

    def admit(self, artifact: ArtifactRequest, source: Path) -> Path:
        """Copy verified TUF output into the root-owned content store.

        The target name is derived only from the declared digest.  Bytes are
        independently hashed while copied with no symlink following, then
        linked into place without overwriting an existing object.
        """

        self._ensure_root()
        digest = artifact.digest.removeprefix("sha256:")
        target = self.root / digest
        if target.exists():
            return self.require(artifact)
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            source_descriptor = os.open(source, flags)
        except OSError as error:
            raise TransactionError(
                f"cannot open verified artifact source safely: {error.strerror}"
            ) from error
        temporary_descriptor = -1
        temporary = ""
        try:
            source_info = os.fstat(source_descriptor)
            if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != artifact.size:
                raise TransactionError(f"verified artifact metadata changed: {artifact.digest}")
            temporary_descriptor, temporary = tempfile.mkstemp(
                prefix=".artifact-", dir=self.root
            )
            os.fchmod(temporary_descriptor, 0o600)
            hasher = hashlib.sha256()
            length = 0
            while chunk := os.read(source_descriptor, 1024 * 1024):
                length += len(chunk)
                if length > artifact.size:
                    raise TransactionError(
                        f"verified artifact exceeded its declared size: {artifact.digest}"
                    )
                hasher.update(chunk)
                offset = 0
                while offset < len(chunk):
                    offset += os.write(temporary_descriptor, chunk[offset:])
            if length != artifact.size or hasher.hexdigest() != digest:
                raise TransactionError(f"verified artifact identity changed: {artifact.digest}")
            os.fsync(temporary_descriptor)
            os.close(temporary_descriptor)
            temporary_descriptor = -1
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                pass
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.close(source_descriptor)
            if temporary_descriptor >= 0:
                os.close(temporary_descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return self.require(artifact)

    def require(self, artifact: ArtifactRequest) -> Path:
        self._ensure_root()
        digest = artifact.digest.removeprefix("sha256:")
        path = self.root / digest
        try:
            info = path.lstat()
        except FileNotFoundError as error:
            raise TransactionError(f"verified artifact is unavailable: {artifact.digest}") from error
        if not stat.S_ISREG(info.st_mode) or info.st_size != artifact.size:
            raise TransactionError(f"verified artifact metadata changed: {artifact.digest}")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            hasher = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                hasher.update(chunk)
        finally:
            os.close(descriptor)
        if hasher.hexdigest() != digest:
            raise TransactionError(f"verified artifact digest changed: {artifact.digest}")
        return path

    def require_rpm(self, artifact: ArtifactRequest) -> Path:
        """Return a verified, rpm-ostree-recognizable name for an RPM artifact.

        The canonical store is content-addressed and deliberately has no file
        extension. rpm-ostree only classifies a local argument as an RPM when
        its basename ends in ``.rpm``. Materialize a persistent hard link so
        the parser gets that suffix without copying or changing the verified
        bytes. A pre-existing link must resolve to the exact same inode.
        """
        path = self.require(artifact)
        rpm_path = path.with_name(f"{path.name}.rpm")
        try:
            os.link(path, rpm_path, follow_symlinks=False)
        except FileExistsError:
            pass
        except OSError as error:
            raise TransactionError(
                f"could not materialize verified RPM artifact: {artifact.digest}"
            ) from error

        source_info = path.lstat()
        rpm_info = rpm_path.lstat()
        if (
            not stat.S_ISREG(rpm_info.st_mode)
            or source_info.st_dev != rpm_info.st_dev
            or source_info.st_ino != rpm_info.st_ino
        ):
            raise TransactionError(
                f"RPM staging name does not reference verified artifact: {artifact.digest}"
            )
        return rpm_path


class RpmOstreeBackend:
    """Stage fixed local RPM sets and identify the resulting pending deployment."""

    def __init__(
        self,
        artifact_store: ArtifactStore | None = None,
        runner: Callable[[Sequence[str]], str] = _run,
    ) -> None:
        self.artifact_store = artifact_store or ArtifactStore()
        self.runner = runner

    def _status(self) -> dict:
        raw = self.runner(("rpm-ostree", "status", "--json"))
        if len(raw.encode("utf-8")) > MAX_STATUS_BYTES:
            raise TransactionError("rpm-ostree status exceeded the response limit")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise TransactionError("rpm-ostree returned invalid status JSON") from error
        if not isinstance(value, dict) or not isinstance(value.get("deployments"), list):
            raise TransactionError("rpm-ostree status has an invalid shape")
        return value

    def booted_checksum(self) -> str:
        booted = [item for item in self._status()["deployments"] if item.get("booted") is True]
        if len(booted) != 1 or not isinstance(booted[0].get("checksum"), str):
            raise TransactionError("rpm-ostree did not report one booted deployment")
        return booted[0]["checksum"]

    def staged_checksum(self) -> str | None:
        staged = [item for item in self._status()["deployments"] if item.get("staged") is True]
        if not staged:
            return None
        if len(staged) != 1 or not isinstance(staged[0].get("checksum"), str):
            raise TransactionError("rpm-ostree reported an ambiguous staged deployment")
        return staged[0]["checksum"]

    def stage(self, request: SystemCompositionRequest) -> str:
        if not request.artifacts:
            raise TransactionError("system composition contains no artifacts")
        unsupported = sorted({item.backend for item in request.artifacts} - {"rpm-set"})
        if unsupported:
            raise TransactionError(
                "rpm-ostree adapter does not handle backend(s): " + ", ".join(unsupported)
            )
        paths = [str(self.artifact_store.require_rpm(item)) for item in request.artifacts]
        # rpm-ostree's install parser does not accept the conventional `--`
        # option terminator here: it treats everything after it as absent and
        # reports that no package was supplied. ArtifactStore always returns
        # an absolute path, so no untrusted value can be parsed as an option.
        self.runner(("rpm-ostree", "install", "--idempotent", *paths))
        status = self._status()
        staged = [item for item in status["deployments"] if item.get("staged") is True]
        if len(staged) != 1 or not isinstance(staged[0].get("checksum"), str):
            raise TransactionError("rpm-ostree did not produce one identifiable staged deployment")
        return staged[0]["checksum"]

    def activate(self, candidate_id: str) -> None:
        if not candidate_id or any(character not in "0123456789abcdef" for character in candidate_id):
            raise TransactionError("candidate deployment identity is invalid")
        deployments = self._status()["deployments"]
        if not any(item.get("staged") is True and item.get("checksum") == candidate_id for item in deployments):
            raise TransactionError("candidate is not the current staged deployment")
        self.runner(("systemctl", "reboot"))

    def rollback(self, candidate_id: str) -> None:
        if not candidate_id or any(character not in "0123456789abcdef" for character in candidate_id):
            raise TransactionError("candidate deployment identity is invalid")
        deployments = self._status()["deployments"]
        if not any(item.get("booted") is True and item.get("checksum") == candidate_id for item in deployments):
            raise TransactionError("candidate is not the booted deployment")
        self.runner(("rpm-ostree", "rollback", "--reboot"))

    def cancel_staged(self, candidate_id: str | None = None) -> None:
        staged = self.staged_checksum()
        if staged is None:
            return
        if candidate_id is not None and staged != candidate_id:
            raise TransactionError("staged deployment does not match the recovery journal")
        self.runner(("rpm-ostree", "cleanup", "-p"))
