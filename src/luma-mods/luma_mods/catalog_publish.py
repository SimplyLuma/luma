"""Accept a signed TUF staging repository before an atomic publication step."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from .catalog_ceremony import KEY_ID, REQUEST_SCHEMA, ROLES, _load_json
from .catalog_update import TufCatalogClient
from .errors import LumaModsError

RECEIPT_SCHEMA = "org.luma.mod-catalog-publication-receipt/v0.1"


def _request_targets(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if set(value) != {
        "schema", "repository", "snapshot_id", "policy_sha256",
        "metadata_versions", "roles",
    } or value.get("schema") != REQUEST_SCHEMA:
        raise LumaModsError("catalog ceremony request has an invalid shape")
    if not isinstance(value["repository"], str) or not value["repository"].strip():
        raise LumaModsError("catalog ceremony request repository is invalid")
    if not isinstance(value["snapshot_id"], str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", value["snapshot_id"]
    ) or not isinstance(value["policy_sha256"], str) or not KEY_ID.fullmatch(
        value["policy_sha256"]
    ):
        raise LumaModsError("catalog ceremony request identities are invalid")
    versions = value["metadata_versions"]
    if not isinstance(versions, dict) or set(versions) != set(ROLES) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 1
        for item in versions.values()
    ):
        raise LumaModsError("catalog ceremony request metadata versions are invalid")
    roles = value["roles"]
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise LumaModsError("catalog ceremony request roles are incomplete")
    targets: dict[str, dict[str, Any]] = {}
    for role_name in ROLES:
        role = roles[role_name]
        if not isinstance(role, dict) or set(role) != {
            "threshold", "key_ids", "custody", "expires_days", "targets"
        } or not isinstance(role["targets"], list):
            raise LumaModsError(f"catalog ceremony request role {role_name} is invalid")
        if (
            not isinstance(role["threshold"], int)
            or isinstance(role["threshold"], bool)
            or not isinstance(role["key_ids"], list)
            or not 1 <= role["threshold"] <= len(role["key_ids"])
            or any(not isinstance(key, str) or not KEY_ID.fullmatch(key) for key in role["key_ids"])
            or len(role["key_ids"]) != len(set(role["key_ids"]))
            or role["custody"] not in {"offline", "online"}
            or not isinstance(role["expires_days"], int)
            or isinstance(role["expires_days"], bool)
            or role["expires_days"] < 1
        ):
            raise LumaModsError(f"catalog ceremony request role {role_name} policy is invalid")
        for target in role["targets"]:
            if not isinstance(target, dict) or set(target) != {"path", "length", "sha256"}:
                raise LumaModsError("catalog ceremony request target is invalid")
            path = target["path"]
            pure = PurePosixPath(path) if isinstance(path, str) else None
            if (
                pure is None
                or pure.is_absolute()
                or any(part in {"", ".", ".."} for part in pure.parts)
                or "\\" in path
                or path in targets
                or not isinstance(target["length"], int)
                or isinstance(target["length"], bool)
                or target["length"] < 0
                or not isinstance(target["sha256"], str)
                or not KEY_ID.fullmatch(target["sha256"])
            ):
                raise LumaModsError("catalog ceremony request repeats or misnames a target")
            targets[path] = {**target, "role": role_name}
    return targets


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    length = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            length += len(chunk)
            digest.update(chunk)
    return length, digest.hexdigest()


def accept(
    client: TufCatalogClient,
    request_path: Path,
    output: Path,
) -> dict[str, Any]:
    """Verify the complete signed staging repository and emit a release receipt."""

    if output.exists() or output.is_symlink():
        raise LumaModsError("publication receipt output already exists")
    request = _load_json(request_path, "catalog ceremony request")
    targets = _request_targets(request)
    snapshot = client.refresh()
    if snapshot.snapshot_id != request["snapshot_id"]:
        raise LumaModsError("signed staging repository has a different catalog snapshot")

    expected = {"catalog/index.json"}
    for entry in snapshot.entries.values():
        expected.update((entry.manifest, *entry.signature_bundles, *entry.evidence, *entry.payloads))
        client.inspect_entry(snapshot, entry.id)
    if expected != set(targets):
        raise LumaModsError("signed staging repository target set differs from the ceremony request")

    accepted: list[dict[str, Any]] = []
    for name, record in sorted(targets.items()):
        maximum = record["length"]
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
            raise LumaModsError(f"ceremony target {name} has invalid length")
        path = client.acquire_target(name, maximum_bytes=maximum)
        length, digest = _digest(path)
        if length != record["length"] or digest != record["sha256"]:
            raise LumaModsError(f"TUF-verified target {name} differs from the ceremony request")
        accepted.append({
            "path": name,
            "length": length,
            "sha256": digest,
            "role": record["role"],
        })

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "repository": request["repository"],
        "snapshot_id": snapshot.snapshot_id,
        "policy_sha256": request["policy_sha256"],
        "request_sha256": hashlib.sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "metadata_versions": request["metadata_versions"],
        "targets": accepted,
        "verification": "python-tuf-full-refresh-and-target-verification",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
    try:
        payload = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return receipt
