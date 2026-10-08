"""Deterministic, non-executable Mod locks for Luma image composition."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .catalog_publish import RECEIPT_SCHEMA
from .errors import TransactionError
from .model import Plan
from .resolver import HostContext, host_context_sha256
from .trust import VerificationResult

LOCK_SCHEMA = "org.luma.mod-image-composition-lock/v0.1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
HIGH_IMPACT = {"experience", "hardware", "core-system"}
INSTALL_TRUST = {"luma-core", "luma-verified"}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _receipt_digests(receipt: Mapping[str, Any] | None) -> tuple[str | None, set[str]]:
    if receipt is None:
        return None, set()
    fields = {
        "schema", "repository", "snapshot_id", "policy_sha256", "request_sha256",
        "metadata_versions", "targets", "verification",
    }
    if set(receipt) != fields or receipt.get("schema") != RECEIPT_SCHEMA:
        raise TransactionError("image composition receipt has an unsupported schema")
    if not isinstance(receipt.get("repository"), str) or not receipt["repository"].strip():
        raise TransactionError("image composition receipt repository is invalid")
    snapshot = receipt.get("snapshot_id")
    if not isinstance(snapshot, str) or not SNAPSHOT_ID.fullmatch(snapshot):
        raise TransactionError("image composition receipt has an invalid snapshot identity")
    for field in ("policy_sha256", "request_sha256"):
        digest = receipt.get(field)
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise TransactionError(f"image composition receipt {field} is invalid")
    versions = receipt.get("metadata_versions")
    if (
        not isinstance(versions, dict)
        or not versions
        or any(
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 1
            for key, value in versions.items()
        )
    ):
        raise TransactionError("image composition receipt metadata versions are invalid")
    if receipt.get("verification") != "python-tuf-full-refresh-and-target-verification":
        raise TransactionError("image composition receipt verification method is invalid")
    targets = receipt.get("targets")
    if not isinstance(targets, list) or len(targets) > 100_000:
        raise TransactionError("image composition receipt targets are invalid")
    digests: set[str] = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"path", "length", "sha256", "role"}:
            raise TransactionError("image composition receipt target is invalid")
        if (
            not isinstance(target["path"], str)
            or not target["path"]
            or not isinstance(target["role"], str)
            or not target["role"]
            or not isinstance(target["length"], int)
            or isinstance(target["length"], bool)
            or target["length"] < 0
        ):
            raise TransactionError("image composition receipt target metadata is invalid")
        digest = target.get("sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise TransactionError("image composition receipt target digest is invalid")
        digests.add(digest)
    return snapshot, digests


@dataclass(frozen=True)
class ImageCompositionLock:
    document: Mapping[str, Any]
    _authorization_verified: bool = False

    @property
    def lock_sha256(self) -> str:
        return str(self.document["lock_sha256"])

    @property
    def install_authorized(self) -> bool:
        return bool(
            self._authorization_verified
            and self.document["authorization"]["install_authorized"]
        )

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.document))

    @classmethod
    def from_dict(cls, value: Any) -> "ImageCompositionLock":
        if not isinstance(value, dict):
            raise TransactionError("image composition lock must be an object")
        expected = {
            "schema", "lock_sha256", "target_id", "plan_sha256", "base",
            "activation", "impact", "recovery", "provider_transitions", "mods",
            "authorization",
        }
        if set(value) != expected or value.get("schema") != LOCK_SCHEMA:
            raise TransactionError("image composition lock has an invalid shape")
        lock_sha = value.get("lock_sha256")
        if not isinstance(lock_sha, str) or not SHA256.fullmatch(lock_sha):
            raise TransactionError("image composition lock identity is invalid")
        unsigned = dict(value)
        del unsigned["lock_sha256"]
        if hashlib.sha256(_canonical(unsigned)).hexdigest() != lock_sha:
            raise TransactionError("image composition lock identity does not match its content")
        base = value.get("base")
        if not isinstance(base, dict) or base.get("install_mode") != "image-compose":
            raise TransactionError("image composition lock is not bound to image-compose mode")
        authorization = value.get("authorization")
        if not isinstance(authorization, dict) or set(authorization) != {
            "install_authorized", "catalog_snapshot_id", "reason"
        }:
            raise TransactionError("image composition lock authorization is invalid")
        if not isinstance(authorization["install_authorized"], bool):
            raise TransactionError("image composition authorization flag must be boolean")
        # Serialized locks carry review evidence, never ambient authority. A
        # privileged composer must rebuild the lock from live TUF and publisher
        # verification results in its own trusted process.
        return cls(json.loads(json.dumps(value)), False)


def build_image_composition_lock(
    plan: Plan,
    host: HostContext,
    verifications: Mapping[str, VerificationResult] | None = None,
    publication_receipt: Mapping[str, Any] | None = None,
) -> ImageCompositionLock:
    """Lock an already-resolved plan to one image tuple without executing it.

    A lock is install-authorized only when every selected manifest and payload is
    present in a fully accepted TUF publication receipt and every publisher has
    a verified policy result. Community and local content remains previewable,
    but high-impact image composition stays closed.
    """

    if host.install_mode != "image-compose":
        raise TransactionError("image composition requires an image-compose host context")
    if not plan.host_sha256 or plan.host_sha256 != host_context_sha256(host):
        raise TransactionError(
            "image composition host does not match the host tuple used to resolve the plan"
        )
    verification_map = dict(verifications or {})
    snapshot_id, receipt_digests = _receipt_digests(publication_receipt)
    authorization_reasons: list[str] = []
    locked_mods: list[dict[str, Any]] = []
    all_verified = True

    for planned in plan.mods:
        inspection = planned.inspection
        mod = inspection.mod
        verification = verification_map.get(mod.identity.id)
        verified = bool(
            verification
            and verification.verified
            and verification.publisher_id == mod.identity.publisher.id
        )
        trust_level = verification.level if verified and verification else "local-unverified"
        if not verified:
            all_verified = False
            authorization_reasons.append(f"{mod.identity.id} has no verified publisher result")
        if planned.assessment.impact in HIGH_IMPACT and trust_level not in INSTALL_TRUST:
            all_verified = False
            authorization_reasons.append(
                f"{mod.identity.id} requires Luma Core or Luma Verified trust for image composition"
            )
        if inspection.source_sha256 not in receipt_digests:
            all_verified = False
            authorization_reasons.append(
                f"{mod.identity.id} manifest is absent from the accepted catalog snapshot"
            )
        payloads: list[dict[str, Any]] = []
        for payload in mod.payloads:
            digest = str(payload["digest"])
            plain_digest = digest.removeprefix("sha256:")
            if plain_digest not in receipt_digests:
                all_verified = False
                authorization_reasons.append(
                    f"{mod.identity.id} payload {digest} is absent from the accepted catalog snapshot"
                )
            payloads.append(dict(payload))
        locked_mods.append({
            "id": mod.identity.id,
            "version": mod.identity.version,
            "kind": mod.identity.kind,
            "publisher_id": mod.identity.publisher.id,
            "manifest_source_sha256": inspection.source_sha256,
            "manifest_canonical_sha256": inspection.canonical_sha256,
            "trust_level": trust_level,
            "effects": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in mod.effects.populated().items()
            },
            "payloads": payloads,
        })

    if publication_receipt is None:
        authorization_reasons.append("no accepted TUF publication receipt was supplied")
    if not plan.mods:
        all_verified = False
        authorization_reasons.append("the resolved plan contains no Mods")
    install_authorized = all_verified and snapshot_id is not None
    reason = (
        "Every manifest and payload is bound to the accepted TUF snapshot and every "
        "publisher satisfies image-composition policy."
        if install_authorized
        else "; ".join(dict.fromkeys(authorization_reasons))
    )
    document: dict[str, Any] = {
        "schema": LOCK_SCHEMA,
        "target_id": plan.target_id,
        "plan_sha256": plan.composition_sha256,
        "base": {
            "luma_base": host.luma_base,
            "architecture": host.architecture,
            "presentation": host.presentation,
            "kernel_release": host.kernel_release,
            "install_mode": host.install_mode,
            "hardware": sorted(host.hardware),
        },
        "activation": plan.activation,
        "impact": plan.impact,
        "recovery": {
            "retain_unmodified_base": plan.activation in {"reboot", "recovery-reboot"},
            "required_activation": plan.activation,
        },
        "provider_transitions": plan.as_dict()["provider_transitions"],
        "mods": locked_mods,
        "authorization": {
            "install_authorized": install_authorized,
            "catalog_snapshot_id": snapshot_id,
            "reason": reason,
        },
    }
    document["lock_sha256"] = hashlib.sha256(_canonical(document)).hexdigest()
    return ImageCompositionLock(document, install_authorized)


def require_install_authorized(lock: ImageCompositionLock) -> None:
    if not lock.install_authorized:
        raise TransactionError(
            "image composition lock is review-only; accepted catalog and publisher proof are required"
        )
