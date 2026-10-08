"""Bounded Luma-owned preference profile contract and reference backend."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import TransactionError
from .manifest import ID_PATTERN, VERSION_PATTERN
from .registry import validate_preference_value

PROFILE_SCHEMA = "org.luma.mod-profile/v0.1"
MAX_PROFILE_BYTES = 256 * 1024
_MISSING = {"present": False}


def _bounded_value(value: Any, *, depth: int = 0) -> None:
    if depth > 16:
        raise TransactionError("profile value nesting is too deep")
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and (value != value or abs(value) == float("inf")):
            raise TransactionError("profile values cannot contain non-finite numbers")
        if isinstance(value, str) and len(value.encode("utf-8")) > 16_384:
            raise TransactionError("profile contains an oversized text value")
        return
    if isinstance(value, list):
        if len(value) > 1024:
            raise TransactionError("profile contains an oversized list")
        for item in value:
            _bounded_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 1024 or any(not isinstance(key, str) for key in value):
            raise TransactionError("profile contains an invalid object")
        for item in value.values():
            _bounded_value(item, depth=depth + 1)
        return
    raise TransactionError("profile contains an unsupported value type")


@dataclass(frozen=True)
class PreferenceProfile:
    mod_id: str
    version: str
    values: Mapping[str, Any]
    source_sha256: str

    @classmethod
    def from_file(cls, path: Path) -> "PreferenceProfile":
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise TransactionError(f"cannot open profile safely: {error.strerror}") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_PROFILE_BYTES:
                raise TransactionError("profile must be a bounded regular file")
            payload = os.read(descriptor, MAX_PROFILE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(payload) > MAX_PROFILE_BYTES:
            raise TransactionError("profile exceeds its size limit")
        try:
            root = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransactionError(f"profile is not valid UTF-8 JSON: {error}") from error
        if not isinstance(root, dict) or set(root) != {"schema", "mod_id", "version", "values"}:
            raise TransactionError("profile has an invalid document shape")
        if root["schema"] != PROFILE_SCHEMA:
            raise TransactionError(f"unsupported profile schema: {root['schema']!r}")
        if not isinstance(root["mod_id"], str) or not ID_PATTERN.fullmatch(root["mod_id"]):
            raise TransactionError("profile mod_id is invalid")
        if not isinstance(root["version"], str) or not VERSION_PATTERN.fullmatch(root["version"]):
            raise TransactionError("profile version is invalid")
        values = root["values"]
        if not isinstance(values, dict) or not values:
            raise TransactionError("profile values must be a non-empty object")
        for domain, value in values.items():
            if not isinstance(domain, str) or not ID_PATTERN.fullmatch(domain):
                raise TransactionError(f"profile setting domain is invalid: {domain!r}")
            _bounded_value(value)
        return cls(
            root["mod_id"],
            root["version"],
            copy.deepcopy(values),
            hashlib.sha256(payload).hexdigest(),
        )


class FilePreferenceBackend:
    """Reference backend for a Luma-owned declarative setting-domain store.

    Production shell/application components may consume this state directly or
    implement the same snapshot/apply/restore protocol. It is intentionally not
    a generic dconf, CSS, command, or file-copy escape hatch.
    """

    def __init__(self, path: Path, supported_domains: set[str]):
        self.path = path
        self.supported_domains = frozenset(supported_domains)

    def _read(self) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise TransactionError(f"cannot open preference state safely: {error.strerror}") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_PROFILE_BYTES:
                raise TransactionError("preference state must be a bounded regular file")
            payload = os.read(descriptor, MAX_PROFILE_BYTES + 1)
        finally:
            os.close(descriptor)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TransactionError(f"preference state is invalid: {error}") from error
        if not isinstance(value, dict):
            raise TransactionError("preference state must be an object")
        for domain, item in value.items():
            if domain not in self.supported_domains:
                raise TransactionError(f"preference state contains unsupported domain {domain}")
            _bounded_value(item)
            try:
                validate_preference_value(domain, item)
            except ValueError as error:
                raise TransactionError(f"invalid value for {domain}: {error}") from error
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise TransactionError("preference state parent cannot be a symlink")
        payload = json.dumps(value, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        descriptor, temporary = tempfile.mkstemp(prefix=".profile-", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
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

    def ensure_supported(self, domains: set[str]) -> None:
        unsupported = domains - self.supported_domains
        if unsupported:
            raise TransactionError(
                "no supported Luma preference API exists for: " + ", ".join(sorted(unsupported))
            )

    def snapshot(self, domains: set[str]) -> dict[str, Any]:
        self.ensure_supported(domains)
        current = self._read()
        return {
            domain: {"present": True, "value": copy.deepcopy(current[domain])}
            if domain in current else dict(_MISSING)
            for domain in sorted(domains)
        }

    def apply(self, values: Mapping[str, Any]) -> None:
        self.ensure_supported(set(values))
        for domain, value in values.items():
            try:
                validate_preference_value(domain, value)
            except ValueError as error:
                raise TransactionError(f"invalid value for {domain}: {error}") from error
        current = self._read()
        current.update(copy.deepcopy(values))
        self._write(current)

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.ensure_supported(set(snapshot))
        current = self._read()
        for domain, record in snapshot.items():
            if not isinstance(record, dict) or set(record) not in ({"present"}, {"present", "value"}):
                raise TransactionError(f"invalid recovery record for {domain}")
            if record["present"] is True and "value" in record:
                current[domain] = copy.deepcopy(record["value"])
            elif record == _MISSING:
                current.pop(domain, None)
            else:
                raise TransactionError(f"invalid recovery record for {domain}")
        self._write(current)
