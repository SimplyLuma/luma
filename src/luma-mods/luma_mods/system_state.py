"""Crash-safe state for privileged Luma system composition.

The record is deliberately separate from per-user Mod state.  It binds a
reviewed composition to the known-good and candidate OSTree deployments before
activation, and it never stores commands or publisher-controlled paths.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .errors import StateError, TransactionError
from .privileged import SystemCompositionRequest

SYSTEM_STATE_SCHEMA = "org.luma.mod-system-state/v0.1"
MAX_SYSTEM_STATE_BYTES = 1024 * 1024
MAX_SYSTEM_HISTORY = 128
PHASES = {"staging", "staged", "activation-requested", "candidate-booted"}
RESULTS = {"cancelled", "promoted", "rolled-back", "recovered"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checksum(value: Any, where: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) < 32
        or len(value) > 128
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StateError(f"{where} is not a valid deployment checksum")
    return value


def empty_system_state() -> dict[str, Any]:
    return {
        "schema": SYSTEM_STATE_SCHEMA,
        "generation": 0,
        "known_good_checksum": None,
        "pending": None,
        "history": [],
    }


def validate_system_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema", "generation", "known_good_checksum", "pending", "history"
    }:
        raise StateError("system Mod state has an invalid document shape")
    if value["schema"] != SYSTEM_STATE_SCHEMA:
        raise StateError("unsupported system Mod state schema")
    if (
        not isinstance(value["generation"], int)
        or isinstance(value["generation"], bool)
        or value["generation"] < 0
    ):
        raise StateError("system Mod generation is invalid")
    _checksum(value["known_good_checksum"], "known_good_checksum", optional=True)
    pending = value["pending"]
    if pending is not None:
        fields = {
            "transaction_id", "target_id", "composition_sha256", "phase",
            "known_good_checksum", "candidate_checksum", "boot_attempts",
            "boot_attempt_limit", "created_at",
        }
        if not isinstance(pending, dict) or set(pending) != fields:
            raise StateError("pending system transaction has an invalid shape")
        for name in ("transaction_id", "target_id", "created_at"):
            if not isinstance(pending[name], str) or not pending[name]:
                raise StateError(f"pending system transaction {name} is invalid")
        if (
            not isinstance(pending["composition_sha256"], str)
            or len(pending["composition_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in pending["composition_sha256"])
        ):
            raise StateError("pending system composition digest is invalid")
        if pending["phase"] not in PHASES:
            raise StateError("pending system transaction phase is invalid")
        _checksum(pending["known_good_checksum"], "pending known-good checksum")
        _checksum(
            pending["candidate_checksum"], "pending candidate checksum", optional=True
        )
        if pending["phase"] != "staging" and pending["candidate_checksum"] is None:
            raise StateError("a staged system transaction requires a candidate checksum")
        for name in ("boot_attempts", "boot_attempt_limit"):
            number = pending[name]
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                raise StateError(f"pending system transaction {name} is invalid")
        if not 1 <= pending["boot_attempt_limit"] <= 5:
            raise StateError("boot attempt limit must be between one and five")
        if pending["boot_attempts"] > pending["boot_attempt_limit"]:
            raise StateError("boot attempts exceed the configured limit")
    history = value["history"]
    if not isinstance(history, list) or len(history) > MAX_SYSTEM_HISTORY:
        raise StateError("system Mod history is invalid")
    for index, entry in enumerate(history):
        if not isinstance(entry, dict) or set(entry) != {
            "transaction_id", "target_id", "candidate_checksum", "result", "at"
        }:
            raise StateError(f"system Mod history[{index}] has an invalid shape")
        for name in ("transaction_id", "target_id", "at"):
            if not isinstance(entry[name], str) or not entry[name]:
                raise StateError(f"system Mod history[{index}].{name} is invalid")
        _checksum(entry["candidate_checksum"], f"system Mod history[{index}] candidate")
        if entry["result"] not in RESULTS:
            raise StateError(f"system Mod history[{index}].result is invalid")
    return copy.deepcopy(value)


@dataclass
class LockedSystemState:
    store: "SystemStateStore"
    value: dict[str, Any]

    def write(self, value: Mapping[str, Any]) -> dict[str, Any]:
        checked = validate_system_state(dict(value))
        self.store._atomic_write(checked)
        self.value = checked
        return copy.deepcopy(checked)


class SystemStateStore:
    def __init__(self, root: Path = Path("/var/lib/luma/mods")) -> None:
        self.root = root
        self.path = root / "system-state.json"
        self.lock_path = root / "system-state.lock"

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise StateError("system Mod state root must be a real directory")
        os.chmod(self.root, 0o700)

    def _read_unlocked(self) -> dict[str, Any]:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return empty_system_state()
        except OSError as error:
            raise StateError(f"cannot open system Mod state safely: {error.strerror}") from error
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SYSTEM_STATE_BYTES:
                raise StateError("system Mod state must be a bounded regular file")
            payload = os.read(descriptor, MAX_SYSTEM_STATE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(payload) > MAX_SYSTEM_STATE_BYTES:
            raise StateError("system Mod state exceeds its size limit")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StateError(f"system Mod state is invalid UTF-8 JSON: {error}") from error
        return validate_system_state(decoded)

    def _atomic_write(self, value: Mapping[str, Any]) -> None:
        payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        if len(payload) > MAX_SYSTEM_STATE_BYTES:
            raise StateError("system Mod state exceeds its size limit")
        descriptor, temporary = tempfile.mkstemp(prefix=".system-state-", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @contextmanager
    def locked(self) -> Iterator[LockedSystemState]:
        self._ensure_root()
        try:
            descriptor = os.open(
                self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600
            )
        except OSError as error:
            raise StateError(f"cannot open system Mod lock safely: {error.strerror}") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise StateError("system Mod state lock must be a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield LockedSystemState(self, self._read_unlocked())
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def read(self) -> dict[str, Any]:
        with self.locked() as locked:
            return copy.deepcopy(locked.value)

    def prepare(
        self,
        request: SystemCompositionRequest,
        known_good_checksum: str,
        *,
        boot_attempt_limit: int,
    ) -> str:
        _checksum(known_good_checksum, "known-good checksum")
        if not 1 <= boot_attempt_limit <= 5:
            raise TransactionError("boot attempt limit must be between one and five")
        with self.locked() as locked:
            if locked.value["pending"] is not None:
                raise TransactionError("an interrupted system transaction must be recovered first")
            transaction_id = str(uuid.uuid4())
            desired = copy.deepcopy(locked.value)
            desired["known_good_checksum"] = known_good_checksum
            desired["pending"] = {
                "transaction_id": transaction_id,
                "target_id": request.target_id,
                "composition_sha256": request.composition_sha256,
                "phase": "staging",
                "known_good_checksum": known_good_checksum,
                "candidate_checksum": None,
                "boot_attempts": 0,
                "boot_attempt_limit": boot_attempt_limit,
                "created_at": _now(),
            }
            desired["generation"] += 1
            locked.write(desired)
            return transaction_id

    def mark_staged(self, transaction_id: str, candidate_checksum: str) -> None:
        _checksum(candidate_checksum, "candidate checksum")
        with self.locked() as locked:
            desired = copy.deepcopy(locked.value)
            pending = desired["pending"]
            if pending is None or pending["transaction_id"] != transaction_id:
                raise TransactionError("system staging transaction identity changed")
            if pending["phase"] != "staging":
                raise TransactionError("system transaction is not in the staging phase")
            if candidate_checksum == pending["known_good_checksum"]:
                raise TransactionError("candidate deployment equals the known-good deployment")
            pending["candidate_checksum"] = candidate_checksum
            pending["phase"] = "staged"
            desired["generation"] += 1
            locked.write(desired)

    def request_activation(self, candidate_checksum: str) -> None:
        with self.locked() as locked:
            desired = copy.deepcopy(locked.value)
            pending = desired["pending"]
            if (
                pending is None
                or pending["phase"] != "staged"
                or pending["candidate_checksum"] != candidate_checksum
            ):
                raise TransactionError("candidate is not the journaled staged deployment")
            pending["phase"] = "activation-requested"
            desired["generation"] += 1
            locked.write(desired)

    def observe_boot(self, booted_checksum: str) -> str:
        """Record a boot and return none, candidate, known-good, or rollback."""

        _checksum(booted_checksum, "booted checksum")
        with self.locked() as locked:
            desired = copy.deepcopy(locked.value)
            pending = desired["pending"]
            if pending is None:
                if desired["known_good_checksum"] is None:
                    desired["known_good_checksum"] = booted_checksum
                    desired["generation"] += 1
                    locked.write(desired)
                return "none"
            candidate = pending["candidate_checksum"]
            if booted_checksum == pending["known_good_checksum"]:
                self._finish(desired, "rolled-back")
                locked.write(desired)
                return "known-good"
            if candidate is None or booted_checksum != candidate:
                raise TransactionError("booted deployment matches neither candidate nor known-good")
            if pending["phase"] not in {"activation-requested", "candidate-booted"}:
                raise TransactionError("candidate boot occurred before activation was journaled")
            pending["phase"] = "candidate-booted"
            pending["boot_attempts"] += 1
            if pending["boot_attempts"] >= pending["boot_attempt_limit"]:
                desired["generation"] += 1
                locked.write(desired)
                return "rollback"
            desired["generation"] += 1
            locked.write(desired)
            return "candidate"

    def promote(self, candidate_checksum: str) -> None:
        with self.locked() as locked:
            desired = copy.deepcopy(locked.value)
            pending = desired["pending"]
            if (
                pending is None
                or pending["phase"] != "candidate-booted"
                or pending["candidate_checksum"] != candidate_checksum
            ):
                raise TransactionError("only the observed candidate deployment can be promoted")
            desired["known_good_checksum"] = candidate_checksum
            self._finish(desired, "promoted")
            locked.write(desired)

    def cancel(self, result: str = "cancelled") -> None:
        if result not in {"cancelled", "recovered"}:
            raise TransactionError("invalid system transaction cancellation result")
        with self.locked() as locked:
            desired = copy.deepcopy(locked.value)
            if desired["pending"] is None:
                return
            self._finish(desired, result)
            locked.write(desired)

    @staticmethod
    def _finish(value: dict[str, Any], result: str) -> None:
        pending = value["pending"]
        if pending is None or pending["candidate_checksum"] is None:
            if result not in {"cancelled", "recovered"}:
                raise TransactionError("system transaction has no candidate deployment")
            candidate = pending["known_good_checksum"] if pending else value["known_good_checksum"]
        else:
            candidate = pending["candidate_checksum"]
        value["history"].append({
            "transaction_id": pending["transaction_id"],
            "target_id": pending["target_id"],
            "candidate_checksum": candidate,
            "result": result,
            "at": _now(),
        })
        value["history"] = value["history"][-MAX_SYSTEM_HISTORY:]
        value["pending"] = None
        value["generation"] += 1
