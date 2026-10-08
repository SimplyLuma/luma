"""Prepare a keyless, deterministic handoff for an offline TUF ceremony.

The command validates custody/threshold policy and every unsigned catalog
target, then assigns exact target hashes to release roles.  It never accepts a
private key and deliberately does not implement cryptography: a standard TUF
signer consumes this request in the separately controlled release environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from .catalog_author import HANDOFF_SCHEMA
from .catalog_update import _parse_index
from .errors import LumaModsError

POLICY_SCHEMA = "org.luma.mod-catalog-ceremony-policy/v0.1"
REQUEST_SCHEMA = "org.luma.mod-catalog-ceremony-request/v0.1"
ROLES = (
    "root", "targets", "snapshot", "timestamp",
    "luma-core", "luma-verified", "community",
)
ONLINE_ROLES = {"snapshot", "timestamp", "luma-verified", "community"}
OFFLINE_ROLES = {"root", "targets", "luma-core"}
MAX_EXPIRY_DAYS = {
    "root": 730,
    "targets": 180,
    "snapshot": 14,
    "timestamp": 2,
    "luma-core": 180,
    "luma-verified": 90,
    "community": 90,
}
KEY_ID = re.compile(r"^[0-9a-f]{64}$")
MAX_CONTROL_BYTES = 4 * 1024 * 1024


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise LumaModsError(f"cannot open {label} safely: {error.strerror}") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONTROL_BYTES:
            raise LumaModsError(f"{label} must be a bounded regular file")
        chunks: list[bytes] = []
        remaining = MAX_CONTROL_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LumaModsError(f"{label} is invalid UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise LumaModsError(f"{label} must be an object")
    return value


def load_policy(path: Path) -> dict[str, Any]:
    value = _load_json(path, "catalog ceremony policy")
    if set(value) != {"schema", "repository", "metadata_versions", "roles"}:
        raise LumaModsError("catalog ceremony policy has an invalid shape")
    if value["schema"] != POLICY_SCHEMA:
        raise LumaModsError("catalog ceremony policy schema is unsupported")
    if not isinstance(value["repository"], str) or not value["repository"].strip():
        raise LumaModsError("catalog ceremony repository must be non-empty text")
    versions = value["metadata_versions"]
    if not isinstance(versions, dict) or set(versions) != set(ROLES) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in versions.values()
    ):
        raise LumaModsError("catalog ceremony metadata_versions are invalid")
    roles = value["roles"]
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise LumaModsError("catalog ceremony roles are incomplete")
    used: dict[str, str] = {}
    for name in ROLES:
        role = roles[name]
        if not isinstance(role, dict) or set(role) != {
            "threshold", "key_ids", "custody", "expires_days"
        }:
            raise LumaModsError(f"catalog ceremony role {name} has an invalid shape")
        keys = role["key_ids"]
        threshold = role["threshold"]
        if not isinstance(keys, list) or not keys or any(
            not isinstance(key, str) or not KEY_ID.fullmatch(key) for key in keys
        ) or len(keys) != len(set(keys)):
            raise LumaModsError(f"catalog ceremony role {name} has invalid public key IDs")
        if not isinstance(threshold, int) or isinstance(threshold, bool) or not 1 <= threshold <= len(keys):
            raise LumaModsError(f"catalog ceremony role {name} has an invalid threshold")
        custody = role["custody"]
        expected = "online" if name in ONLINE_ROLES else "offline"
        if custody != expected:
            raise LumaModsError(f"catalog ceremony role {name} must use {expected} custody")
        expiry = role["expires_days"]
        if not isinstance(expiry, int) or isinstance(expiry, bool) or not 1 <= expiry <= MAX_EXPIRY_DAYS[name]:
            raise LumaModsError(f"catalog ceremony role {name} has an unsafe expiry")
        if name in OFFLINE_ROLES and threshold < 2:
            raise LumaModsError(f"catalog ceremony role {name} requires a threshold of at least two")
        for key in keys:
            if key in used:
                raise LumaModsError(
                    f"catalog key {key} is reused by {used[key]} and {name}; role keys must be independent"
                )
            used[key] = name
    if len(roles["root"]["key_ids"]) < 3:
        raise LumaModsError("catalog root requires at least three independently held keys")
    return value


def _hash_target(path: Path, expected_length: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise LumaModsError(f"cannot open catalog target safely: {error.strerror}") from error
    digest = hashlib.sha256()
    length = 0
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_length:
            raise LumaModsError(f"catalog target length changed: {path.name}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            length += len(chunk)
            digest.update(chunk)
    finally:
        os.close(descriptor)
    if length != expected_length:
        raise LumaModsError(f"catalog target length changed while reading: {path.name}")
    return digest.hexdigest()


def prepare(policy_path: Path, handoff_root: Path, output: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    if output.exists() or output.is_symlink():
        raise LumaModsError("ceremony request output already exists")
    handoff = _load_json(handoff_root / "signing-handoff.json", "catalog signing handoff")
    if set(handoff) != {"schema", "snapshot_id", "targets"} or handoff.get("schema") != HANDOFF_SCHEMA:
        raise LumaModsError("catalog signing handoff has an invalid shape")
    raw_targets = handoff["targets"]
    if not isinstance(raw_targets, list) or not raw_targets:
        raise LumaModsError("catalog signing handoff has no targets")
    targets_root = handoff_root / "targets"
    inventory: dict[str, dict[str, Any]] = {}
    for number, item in enumerate(raw_targets):
        if not isinstance(item, dict) or set(item) != {"path", "length", "sha256"}:
            raise LumaModsError(f"catalog signing target {number} has an invalid shape")
        target = item["path"]
        if not isinstance(target, str) or "\\" in target:
            raise LumaModsError("catalog signing target path is invalid")
        pure = PurePosixPath(target)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise LumaModsError("catalog signing target path is invalid")
        if target in inventory:
            raise LumaModsError(f"catalog signing handoff repeats {target}")
        length = item["length"]
        digest = item["sha256"]
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise LumaModsError(f"catalog signing target {target} has invalid length")
        if not isinstance(digest, str) or not KEY_ID.fullmatch(digest):
            raise LumaModsError(f"catalog signing target {target} has invalid digest")
        actual = _hash_target(targets_root.joinpath(*pure.parts), length)
        if actual != digest:
            raise LumaModsError(f"catalog signing target digest changed: {target}")
        inventory[target] = dict(item)

    actual_files: set[str] = set()
    for path in targets_root.rglob("*"):
        if path.is_symlink():
            raise LumaModsError("catalog targets must not contain symlinks")
        if path.is_file():
            actual_files.add(path.relative_to(targets_root).as_posix())
    if actual_files != set(inventory):
        raise LumaModsError("catalog targets differ from the reviewed signing handoff")

    index_item = inventory.get("catalog/index.json")
    if index_item is None:
        raise LumaModsError("catalog signing handoff omits catalog/index.json")
    snapshot = _parse_index(targets_root / "catalog/index.json")
    if snapshot.snapshot_id != handoff.get("snapshot_id"):
        raise LumaModsError("catalog snapshot identity changed after author review")

    assignments: dict[str, str] = {"catalog/index.json": "targets"}
    for entry in snapshot.entries.values():
        for target in (
            entry.manifest, *entry.signature_bundles, *entry.evidence, *entry.payloads
        ):
            previous = assignments.get(target)
            if previous is not None and previous != entry.trust_role:
                raise LumaModsError(
                    f"catalog target {target} crosses trust roles {previous} and {entry.trust_role}"
                )
            allowed_prefixes = (
                f"mods/{entry.trust_role}/",
                f"evidence/{entry.trust_role}/",
                f"payloads/{entry.trust_role}/",
            )
            if not target.startswith(allowed_prefixes):
                raise LumaModsError(
                    f"catalog target {target} is outside delegated role path {entry.trust_role}"
                )
            assignments[target] = entry.trust_role
    if set(assignments) != set(inventory):
        missing = sorted(set(inventory) - set(assignments))
        raise LumaModsError(
            "catalog signing handoff contains unassigned targets: " + ", ".join(missing)
        )

    grouped: dict[str, list[dict[str, Any]]] = {role: [] for role in ROLES}
    for target, role in sorted(assignments.items()):
        grouped[role].append(inventory[target])
    request = {
        "schema": REQUEST_SCHEMA,
        "repository": policy["repository"],
        "snapshot_id": snapshot.snapshot_id,
        "policy_sha256": hashlib.sha256(_canonical(policy)).hexdigest(),
        "metadata_versions": policy["metadata_versions"],
        "roles": {
            role: {
                **policy["roles"][role],
                "targets": grouped[role],
            }
            for role in ROLES
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
    try:
        payload = json.dumps(request, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return request
