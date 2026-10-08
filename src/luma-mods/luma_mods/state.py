"""Crash-safe, versioned persistent state for Luma Mod lifecycle work.

This module owns records only. It never invokes a package manager, changes a
setting, or crosses a privilege boundary. Callers must journal a transaction
before touching an external backend and either commit or recover it.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .errors import StateError
from .manifest import ID_PATTERN, VERSION_PATTERN

STATE_SCHEMA = "org.luma.mod-state/v0.1"
TRANSACTION_PHASES = {
    "prepared",
    "backend-applying",
    "backend-applied",
    "committing",
}
OPERATIONS = {"install", "update", "enable", "disable", "remove"}
EFFECT_FIELDS = {
    "settings",
    "files",
    "packages",
    "services",
    "dbus_names",
    "portals",
    "devices",
    "configuration_domains",
    "kernel_modules",
    "firmware",
    "boot_arguments",
    "user_data",
}
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_AUDIT_ENTRIES = 512


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def state_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def empty_state() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "generation": 0,
        "composition_sha256": hashlib.sha256(b"").hexdigest(),
        "installed": {},
        "effect_owners": {},
        "dependency_references": {},
        "pending_transaction": None,
        "audit": [],
    }


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{where} must be non-empty text")
    return value


def _identifier(value: Any, where: str) -> str:
    result = _text(value, where)
    if not ID_PATTERN.fullmatch(result):
        raise StateError(f"{where} is not a valid reverse-domain identifier")
    return result


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StateError(f"{where} must be a text list")
    if len(value) != len(set(value)):
        raise StateError(f"{where} must not contain duplicates")
    return value


def _validate_effect_owners(value: Any) -> None:
    if not isinstance(value, dict):
        raise StateError("effect_owners must be an object")
    unknown = set(value) - EFFECT_FIELDS
    if unknown:
        raise StateError(f"effect_owners has unknown fields: {', '.join(sorted(unknown))}")
    for field, owners in value.items():
        if not isinstance(owners, dict):
            raise StateError(f"effect_owners.{field} must be an object")
        for resource, owner in owners.items():
            _text(resource, f"effect_owners.{field} resource")
            _identifier(owner, f"effect_owners.{field}.{resource}")


def validate_state(value: Any, *, allow_pending_snapshot: bool = False) -> dict[str, Any]:
    """Validate an untrusted decoded state document and return a deep copy."""

    if not isinstance(value, dict):
        raise StateError("Mod state must be a JSON object")
    required = {
        "schema",
        "generation",
        "composition_sha256",
        "installed",
        "effect_owners",
        "dependency_references",
        "pending_transaction",
        "audit",
    }
    unknown = set(value) - required
    missing = required - set(value)
    if unknown:
        raise StateError(f"Mod state has unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        raise StateError(f"Mod state is missing fields: {', '.join(sorted(missing))}")
    if value["schema"] != STATE_SCHEMA:
        raise StateError(f"unsupported Mod state schema: {value['schema']!r}")
    if not isinstance(value["generation"], int) or isinstance(value["generation"], bool) or value["generation"] < 0:
        raise StateError("generation must be a non-negative integer")
    digest = value["composition_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise StateError("composition_sha256 must be a lowercase SHA-256 digest")

    installed = value["installed"]
    if not isinstance(installed, dict):
        raise StateError("installed must be an object")
    for identifier, record in installed.items():
        _identifier(identifier, "installed Mod ID")
        if not isinstance(record, dict):
            raise StateError(f"installed.{identifier} must be an object")
        fields = {
            "version", "manifest_sha256", "dependencies", "enabled",
            "trust_level", "installed_at", "effect_claims", "restoration", "activation",
        }
        if set(record) != fields:
            raise StateError(f"installed.{identifier} has an invalid record shape")
        if not isinstance(record["version"], str) or not VERSION_PATTERN.fullmatch(record["version"]):
            raise StateError(f"installed.{identifier}.version is invalid")
        manifest_digest = record["manifest_sha256"]
        if not isinstance(manifest_digest, str) or len(manifest_digest) != 64 or any(c not in "0123456789abcdef" for c in manifest_digest):
            raise StateError(f"installed.{identifier}.manifest_sha256 is invalid")
        for dependency in _string_list(record["dependencies"], f"installed.{identifier}.dependencies"):
            _identifier(dependency, f"installed.{identifier}.dependencies[]")
        if not isinstance(record["enabled"], bool):
            raise StateError(f"installed.{identifier}.enabled must be true or false")
        _text(record["trust_level"], f"installed.{identifier}.trust_level")
        _text(record["installed_at"], f"installed.{identifier}.installed_at")
        claims = record["effect_claims"]
        if not isinstance(claims, dict) or set(claims) - EFFECT_FIELDS:
            raise StateError(f"installed.{identifier}.effect_claims is invalid")
        for field, resources in claims.items():
            _string_list(resources, f"installed.{identifier}.effect_claims.{field}")
        if not isinstance(record["restoration"], dict):
            raise StateError(f"installed.{identifier}.restoration must be an object")
        if not isinstance(record["activation"], dict):
            raise StateError(f"installed.{identifier}.activation must be an object")

    _validate_effect_owners(value["effect_owners"])
    references = value["dependency_references"]
    if not isinstance(references, dict):
        raise StateError("dependency_references must be an object")
    for dependency, dependents in references.items():
        _identifier(dependency, "dependency reference")
        for dependent in _string_list(dependents, f"dependency_references.{dependency}"):
            _identifier(dependent, f"dependency_references.{dependency}[]")

    expected_references: dict[str, list[str]] = {}
    expected_owners: dict[str, dict[str, str]] = {}
    for identifier, record in installed.items():
        for dependency in record["dependencies"]:
            if dependency not in installed:
                raise StateError(f"installed dependency {dependency} is missing")
            expected_references.setdefault(dependency, []).append(identifier)
        for field, resources in record["effect_claims"].items():
            owners = expected_owners.setdefault(field, {})
            for resource in resources:
                previous = owners.setdefault(resource, identifier)
                if previous != identifier:
                    raise StateError(f"effect {field}:{resource} has multiple installed owners")
    expected_references = {
        dependency: sorted(dependents)
        for dependency, dependents in expected_references.items()
    }
    if references != expected_references:
        raise StateError("dependency reference index does not match installed records")
    if value["effect_owners"] != expected_owners:
        raise StateError("effect ownership index does not match installed records")

    pending = value["pending_transaction"]
    if pending is not None:
        if allow_pending_snapshot:
            raise StateError("a recovery snapshot cannot contain another transaction")
        _validate_pending(pending)
    audit = value["audit"]
    if not isinstance(audit, list) or len(audit) > MAX_AUDIT_ENTRIES:
        raise StateError(f"audit must be a list of at most {MAX_AUDIT_ENTRIES} entries")
    for index, entry in enumerate(audit):
        if not isinstance(entry, dict) or set(entry) != {
            "transaction_id", "operation", "target_id", "result", "at", "from_generation", "to_generation"
        }:
            raise StateError(f"audit[{index}] has an invalid record shape")
        _text(entry["transaction_id"], f"audit[{index}].transaction_id")
        if entry["operation"] not in OPERATIONS:
            raise StateError(f"audit[{index}].operation is invalid")
        _identifier(entry["target_id"], f"audit[{index}].target_id")
        if entry["result"] not in {"committed", "recovered", "cancelled"}:
            raise StateError(f"audit[{index}].result is invalid")
        _text(entry["at"], f"audit[{index}].at")
        for field in ("from_generation", "to_generation"):
            if not isinstance(entry[field], int) or entry[field] < 0:
                raise StateError(f"audit[{index}].{field} is invalid")
    return copy.deepcopy(value)


def _validate_pending(value: Any) -> None:
    if not isinstance(value, dict):
        raise StateError("pending_transaction must be an object")
    fields = {
        "id", "operation", "target_id", "phase", "created_at",
        "expected_generation", "before_sha256", "desired_sha256",
        "before_state", "backend", "backend_recovery",
    }
    if set(value) != fields:
        raise StateError("pending_transaction has an invalid record shape")
    _text(value["id"], "pending_transaction.id")
    if value["operation"] not in OPERATIONS:
        raise StateError("pending_transaction.operation is invalid")
    _identifier(value["target_id"], "pending_transaction.target_id")
    if value["phase"] not in TRANSACTION_PHASES:
        raise StateError("pending_transaction.phase is invalid")
    _text(value["created_at"], "pending_transaction.created_at")
    if not isinstance(value["expected_generation"], int) or value["expected_generation"] < 0:
        raise StateError("pending_transaction.expected_generation is invalid")
    for name in ("before_sha256", "desired_sha256"):
        digest = value[name]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise StateError(f"pending_transaction.{name} is invalid")
    before = validate_state(value["before_state"], allow_pending_snapshot=True)
    if state_digest(before) != value["before_sha256"]:
        raise StateError("pending transaction recovery snapshot digest does not match")
    if not isinstance(value["backend_recovery"], dict):
        raise StateError("pending_transaction.backend_recovery must be an object")
    if value["backend"] != "luma-preferences-v1":
        raise StateError("pending_transaction.backend is unsupported")


@dataclass
class LockedState:
    store: "StateStore"
    value: dict[str, Any]

    def write(self, value: Mapping[str, Any]) -> dict[str, Any]:
        checked = validate_state(dict(value))
        self.store._atomic_write(checked)
        self.value = checked
        return copy.deepcopy(checked)


class StateStore:
    """One-user state store with mandatory locking and durable replacement."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "state.json"
        self.lock_path = root / "state.lock"

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise StateError("Mod state root must be a real directory")
        os.chmod(self.root, 0o700)

    def _read_unlocked(self) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return empty_state()
        except OSError as error:
            raise StateError(f"cannot open Mod state safely: {error.strerror}") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise StateError("Mod state must be a regular file")
            if metadata.st_size > MAX_STATE_BYTES:
                raise StateError("Mod state exceeds its size limit")
            payload = os.read(descriptor, MAX_STATE_BYTES + 1)
            if len(payload) > MAX_STATE_BYTES:
                raise StateError("Mod state exceeds its size limit")
        finally:
            os.close(descriptor)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StateError(f"Mod state is not valid UTF-8 JSON: {error}") from error
        return validate_state(value)

    def _atomic_write(self, value: Mapping[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_STATE_BYTES:
            raise StateError("Mod state exceeds its size limit")
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.path)
            directory_fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @contextmanager
    def locked(self) -> Iterator[LockedState]:
        self._ensure_root()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except OSError as error:
            raise StateError(f"cannot open Mod state lock safely: {error.strerror}") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise StateError("Mod state lock must be a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield LockedState(self, self._read_unlocked())
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def read(self) -> dict[str, Any]:
        with self.locked() as locked:
            return copy.deepcopy(locked.value)

    def update(self, expected_generation: int, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Apply a state-only compare-and-swap update under the store lock."""

        with self.locked() as locked:
            if locked.value["pending_transaction"] is not None:
                raise StateError("an interrupted transaction must be recovered first")
            if locked.value["generation"] != expected_generation:
                raise StateError(
                    f"stale Mod state: expected generation {expected_generation}, "
                    f"found {locked.value['generation']}"
                )
            desired = copy.deepcopy(locked.value)
            change(desired)
            desired["generation"] += 1
            return locked.write(desired)
