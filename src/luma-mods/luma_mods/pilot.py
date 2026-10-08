"""Deterministic class-specific pilot evidence for high-impact Mods."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import LumaModsError
from .manifest import inspect_manifest
from .resolver import HostContext

PILOT_SCHEMA = "org.luma.mod-pilot-evidence/v0.1"
RESULTS = {"pending", "pass", "fail", "not-applicable"}
EXPERIENCE_CHECKS = (
    "shared-core-package-pin", "compositor", "shell", "screen-locker",
    "notifications", "polkit-agent", "secret-service", "desktop-portals",
    "power-policy", "accessibility", "display-and-input", "session-health",
    "defaults-and-favorites", "activate", "fallback", "remove", "reinstall",
    "attribution-and-licenses",
)
HARDWARE_CHECKS = (
    "positive-hardware-identity", "unsupported-device-refusal",
    "declared-functions", "cold-boot", "suspend-resume", "power-impact",
    "next-kernel-compatibility", "secure-boot", "candidate-rollback",
    "vendor-recovery", "upstream-contribution-path", "physical-device-proof",
)


def template(manifest_path: Path, host_path: Path) -> dict[str, Any]:
    inspection = inspect_manifest(manifest_path)
    kind = inspection.mod.identity.kind
    if kind not in {"experience", "hardware"}:
        raise LumaModsError("pilot evidence is defined only for experience and hardware Mods")
    try:
        host_value = json.loads(host_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LumaModsError(f"cannot read pilot host evidence: {error}") from error
    context = HostContext.from_dict(host_value)
    checks = EXPERIENCE_CHECKS if kind == "experience" else HARDWARE_CHECKS
    return {
        "schema": PILOT_SCHEMA,
        "mod_id": inspection.mod.identity.id,
        "mod_version": inspection.mod.identity.version,
        "manifest_sha256": inspection.canonical_sha256,
        "kind": kind,
        "host": {
            "luma_base": context.luma_base,
            "architecture": context.architecture,
            "presentation": context.presentation,
            "hardware": list(context.hardware),
        },
        "checks": [
            {"id": check, "required": True, "result": "pending", "evidence_sha256": None}
            for check in checks
        ],
        "release_ready": False,
    }


def validate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema", "mod_id", "mod_version", "manifest_sha256", "kind",
        "host", "checks", "release_ready",
    }:
        raise LumaModsError("pilot evidence has an invalid shape")
    if value["schema"] != PILOT_SCHEMA or value["kind"] not in {"experience", "hardware"}:
        raise LumaModsError("pilot evidence schema or kind is invalid")
    for name in ("mod_id", "mod_version"):
        if not isinstance(value[name], str) or not value[name]:
            raise LumaModsError(f"pilot evidence {name} is invalid")
    digest = value["manifest_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise LumaModsError("pilot evidence manifest digest is invalid")
    host = value["host"]
    if not isinstance(host, dict) or set(host) != {
        "luma_base", "architecture", "presentation", "hardware"
    }:
        raise LumaModsError("pilot host evidence is invalid")
    if any(not isinstance(host[name], str) or not host[name] for name in (
        "luma_base", "architecture", "presentation"
    )) or not isinstance(host["hardware"], list) or any(
        not isinstance(item, str) or not item for item in host["hardware"]
    ):
        raise LumaModsError("pilot host tuple is invalid")
    expected = set(EXPERIENCE_CHECKS if value["kind"] == "experience" else HARDWARE_CHECKS)
    checks = value["checks"]
    if not isinstance(checks, list) or {item.get("id") for item in checks if isinstance(item, dict)} != expected:
        raise LumaModsError("pilot evidence does not contain the complete class checklist")
    for item in checks:
        if not isinstance(item, dict) or set(item) != {
            "id", "required", "result", "evidence_sha256"
        }:
            raise LumaModsError("pilot check has an invalid shape")
        if not isinstance(item["required"], bool) or item["result"] not in RESULTS:
            raise LumaModsError(f"pilot check {item['id']} has an invalid result")
        evidence = item["evidence_sha256"]
        if evidence is not None and (
            not isinstance(evidence, str)
            or len(evidence) != 64
            or any(c not in "0123456789abcdef" for c in evidence)
        ):
            raise LumaModsError(f"pilot check {item['id']} evidence digest is invalid")
        if item["result"] == "pass" and evidence is None:
            raise LumaModsError(f"pilot check {item['id']} cannot pass without evidence")
    if not isinstance(value["release_ready"], bool):
        raise LumaModsError("pilot release_ready must be boolean")
    required_pass = all(
        not item["required"] or item["result"] == "pass" for item in checks
    )
    if value["release_ready"] != required_pass:
        raise LumaModsError("pilot release_ready does not match required evidence results")
    return value
