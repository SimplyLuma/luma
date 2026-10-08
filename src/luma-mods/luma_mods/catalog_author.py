"""Assemble deterministic unsigned catalog targets for an offline TUF signer.

This tool deliberately never creates, loads, or receives signing keys.  Its
output is the content-addressed review handoff consumed by release operations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import LumaModsError
from .manifest import inspect_manifest

SPEC_SCHEMA = "org.luma.mod-catalog-release/v0.1"
INDEX_SCHEMA = "org.luma.mod-catalog/v0.1"
HANDOFF_SCHEMA = "org.luma.mod-catalog-signing-handoff/v0.1"
MAX_SPEC_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 5_000
TRUST_ROLES = {"luma-core", "luma-verified", "community"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_relative(value: Any, where: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise LumaModsError(f"{where} must be a normalized relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise LumaModsError(f"{where} must be a normalized relative path")
    return Path(*pure.parts)


def _read_regular(path: Path, limit: int, label: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise LumaModsError(f"cannot open {label} safely: {error.strerror}") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise LumaModsError(f"{label} must be a bounded regular file")
        payload = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(payload) > limit:
        raise LumaModsError(f"{label} exceeds its size limit")
    return payload


def _decode_spec(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular(path, MAX_SPEC_BYTES, "release specification"))
    except json.JSONDecodeError as error:
        raise LumaModsError(f"release specification is invalid JSON: {error}") from error
    if not isinstance(value, dict) or set(value) != {"schema", "entries"}:
        raise LumaModsError("release specification has an invalid shape")
    if value["schema"] != SPEC_SCHEMA:
        raise LumaModsError("release specification schema is unsupported")
    if not isinstance(value["entries"], list) or not 1 <= len(value["entries"]) <= MAX_ENTRIES:
        raise LumaModsError("release specification entries are invalid")
    return value


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _target(root: Path, relative: str, payload: bytes, inventory: list[dict[str, Any]]) -> None:
    destination = root / relative
    if destination.exists():
        existing = _read_regular(destination, max(len(payload), 1), "catalog target")
        if existing != payload:
            raise LumaModsError(f"catalog target collision: {relative}")
        return
    _write(destination, payload)
    inventory.append({
        "path": relative,
        "length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    })


def assemble(spec_path: Path, output: Path) -> dict[str, Any]:
    spec = _decode_spec(spec_path)
    if output.exists() or output.is_symlink():
        raise LumaModsError("catalog output already exists; refusing to merge or overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    base = spec_path.parent
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    targets = temporary / "targets"
    inventory: list[dict[str, Any]] = []
    index_entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        for number, raw in enumerate(spec["entries"]):
            fields = {"manifest", "signature_bundles", "evidence", "payloads", "trust_role"}
            if not isinstance(raw, dict) or set(raw) != fields:
                raise LumaModsError(f"entries[{number}] has an invalid shape")
            if raw["trust_role"] not in TRUST_ROLES:
                raise LumaModsError(f"entries[{number}].trust_role is invalid")
            manifest_source = base / _safe_relative(raw["manifest"], f"entries[{number}].manifest")
            inspection = inspect_manifest(manifest_source)
            identifier = inspection.mod.identity.id
            version = inspection.mod.identity.version
            if identifier in seen_ids:
                raise LumaModsError(f"release contains duplicate Mod {identifier}")
            seen_ids.add(identifier)
            trust_role = raw["trust_role"]
            # The role is part of the path so standard TUF path delegations,
            # rather than a self-asserted index field, enforce publication
            # authority for every Mod target.
            prefix = f"mods/{trust_role}/{identifier}/{version}"
            manifest_target = f"{prefix}/manifest.mod.json"
            manifest_payload = _read_regular(manifest_source, 1024 * 1024, "manifest")
            _target(targets, manifest_target, manifest_payload, inventory)

            bundles: list[str] = []
            supplied_bundle_digests: set[str] = set()
            raw_bundles = raw["signature_bundles"]
            if not isinstance(raw_bundles, list) or len(raw_bundles) > 32:
                raise LumaModsError(f"entries[{number}].signature_bundles is invalid")
            for item in raw_bundles:
                relative = _safe_relative(item, f"entries[{number}].signature_bundles[]")
                payload = _read_regular(base / relative, 16 * 1024 * 1024, "signature bundle")
                digest = hashlib.sha256(payload).hexdigest()
                supplied_bundle_digests.add(f"sha256:{digest}")
                target = f"{prefix}/{relative.name}"
                _target(targets, target, payload, inventory)
                bundles.append(target)
            declared_bundles = {signature.bundle_digest for signature in inspection.mod.signatures}
            if supplied_bundle_digests != declared_bundles:
                raise LumaModsError(f"signature bundles do not match manifest {identifier}")

            evidence_targets: list[str] = []
            raw_evidence = raw["evidence"]
            if not isinstance(raw_evidence, list) or len(raw_evidence) > 128:
                raise LumaModsError(f"entries[{number}].evidence is invalid")
            for item in raw_evidence:
                relative = _safe_relative(item, f"entries[{number}].evidence[]")
                payload = _read_regular(base / relative, 64 * 1024 * 1024, "evidence")
                digest = hashlib.sha256(payload).hexdigest()
                target = f"evidence/{trust_role}/sha256/{digest}"
                _target(targets, target, payload, inventory)
                evidence_targets.append(target)

            payload_targets: list[str] = []
            supplied_payloads: dict[str, int] = {}
            raw_payloads = raw["payloads"]
            if not isinstance(raw_payloads, list) or len(raw_payloads) > 128:
                raise LumaModsError(f"entries[{number}].payloads is invalid")
            for item in raw_payloads:
                relative = _safe_relative(item, f"entries[{number}].payloads[]")
                payload = _read_regular(base / relative, 8 * 1024 * 1024 * 1024, "payload")
                digest = hashlib.sha256(payload).hexdigest()
                supplied_payloads[f"sha256:{digest}"] = len(payload)
                target = f"payloads/{trust_role}/sha256/{digest}"
                _target(targets, target, payload, inventory)
                payload_targets.append(target)
            declared_payloads = {item["digest"]: item["size"] for item in inspection.mod.payloads}
            if supplied_payloads != declared_payloads:
                raise LumaModsError(f"payload files do not match manifest {identifier}")

            index_entries.append({
                "id": identifier,
                "version": version,
                "manifest": manifest_target,
                "signature_bundles": sorted(bundles),
                "evidence": sorted(evidence_targets),
                "payloads": sorted(payload_targets),
                "trust_role": raw["trust_role"],
            })

        index_entries.sort(key=lambda item: item["id"])
        snapshot_id = "sha256:" + hashlib.sha256(_canonical(index_entries)).hexdigest()
        index = {"schema": INDEX_SCHEMA, "snapshot_id": snapshot_id, "entries": index_entries}
        index_payload = json.dumps(index, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        _target(targets, "catalog/index.json", index_payload, inventory)
        inventory.sort(key=lambda item: item["path"])
        handoff = {
            "schema": HANDOFF_SCHEMA,
            "snapshot_id": snapshot_id,
            "targets": inventory,
        }
        _write(
            temporary / "signing-handoff.json",
            json.dumps(handoff, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        os.replace(temporary, output)
        return handoff
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Assemble unsigned Luma catalog targets.")
    root.add_argument("spec", type=Path)
    root.add_argument("output", type=Path)
    return root


def main(arguments: list[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    try:
        handoff = assemble(args.spec, args.output)
    except (LumaModsError, OSError) as error:
        print(f"luma-mod-catalog-author: {error}", file=os.sys.stderr)
        return 2
    print(json.dumps(handoff, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
