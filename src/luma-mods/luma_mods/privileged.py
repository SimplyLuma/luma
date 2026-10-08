"""Closed-by-default policy boundary for future system composition backends."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from .errors import TransactionError
from .model import Plan

ALLOWED_BACKENDS = {
    "flatpak",
    "ostree-commit",
    "rpm-set",
    "sysext",
    "confext",
    "portable-service-image",
}
PROTECTED_CAPABILITIES = {
    "org.luma.base",
    "org.luma.platform.recovery",
    "org.luma.platform.mod-transactions",
    "org.luma.platform.authentication",
    "org.luma.apps.core",
}
SYSTEM_EFFECTS = {
    "files", "packages", "services", "dbus_names", "portals", "devices",
    "configuration_domains", "kernel_modules", "firmware", "boot_arguments",
}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


@dataclass(frozen=True)
class ArtifactRequest:
    mod_id: str
    backend: str
    digest: str
    size: int
    architecture: str
    role: str
    source: str


@dataclass(frozen=True)
class SystemCompositionRequest:
    target_id: str
    composition_sha256: str
    activation: str
    artifacts: tuple[ArtifactRequest, ...]
    declared_effects: Mapping[str, tuple[str, ...] | bool | str]
    previous_composition_required: bool

    @classmethod
    def from_dict(cls, value: Any) -> "SystemCompositionRequest":
        """Parse an untrusted D-Bus request without accepting executable input."""

        if not isinstance(value, dict):
            raise TransactionError("system composition request must be an object")
        keys = {
            "target_id", "composition_sha256", "activation", "artifacts",
            "declared_effects", "previous_composition_required",
        }
        if set(value) != keys:
            raise TransactionError("system composition request has an invalid shape")
        target_id = value["target_id"]
        composition = value["composition_sha256"]
        activation = value["activation"]
        if not isinstance(target_id, str) or not TOKEN_PATTERN.fullmatch(target_id):
            raise TransactionError("system composition target ID is invalid")
        if not isinstance(composition, str) or not re.fullmatch(r"[0-9a-f]{64}", composition):
            raise TransactionError("system composition digest is invalid")
        if activation not in {"live", "service-restart", "session-restart", "reboot", "recovery-reboot"}:
            raise TransactionError("system composition activation is invalid")
        if not isinstance(value["previous_composition_required"], bool):
            raise TransactionError("previous composition flag must be boolean")
        raw_artifacts = value["artifacts"]
        if not isinstance(raw_artifacts, list) or len(raw_artifacts) > 64:
            raise TransactionError("system composition artifacts must be a bounded list")
        artifacts: list[ArtifactRequest] = []
        artifact_keys = {"mod_id", "backend", "digest", "size", "architecture", "role", "source"}
        for raw in raw_artifacts:
            if not isinstance(raw, dict) or set(raw) != artifact_keys:
                raise TransactionError("system artifact has an invalid shape")
            if not isinstance(raw["mod_id"], str) or not TOKEN_PATTERN.fullmatch(raw["mod_id"]):
                raise TransactionError("system artifact Mod ID is invalid")
            if raw["backend"] not in ALLOWED_BACKENDS:
                raise TransactionError("system artifact backend is unsupported")
            if not isinstance(raw["digest"], str) or not DIGEST_PATTERN.fullmatch(raw["digest"]):
                raise TransactionError("system artifact digest is invalid")
            if (
                not isinstance(raw["size"], int)
                or isinstance(raw["size"], bool)
                or not 0 < raw["size"] <= 8 * 1024 * 1024 * 1024
            ):
                raise TransactionError("system artifact size is invalid")
            for field in ("architecture", "role"):
                if not isinstance(raw[field], str) or not TOKEN_PATTERN.fullmatch(raw[field]):
                    raise TransactionError(f"system artifact {field} is invalid")
            source = raw["source"]
            if not isinstance(source, str) or urlparse(source).scheme != "https":
                raise TransactionError("system artifact source must be HTTPS")
            artifacts.append(ArtifactRequest(**raw))
        raw_effects = value["declared_effects"]
        if not isinstance(raw_effects, dict) or set(raw_effects) - (SYSTEM_EFFECTS | {"user_data", "initramfs", "secure_boot"}):
            raise TransactionError("system composition effects are invalid")
        effects: dict[str, tuple[str, ...] | bool | str] = {}
        for field, raw in raw_effects.items():
            if isinstance(raw, list):
                if len(raw) > 4096 or any(not isinstance(item, str) or not item for item in raw):
                    raise TransactionError(f"system effect {field} is invalid")
                effects[field] = tuple(raw)
            elif isinstance(raw, bool) or isinstance(raw, str):
                effects[field] = raw
            else:
                raise TransactionError(f"system effect {field} is invalid")
        return cls(
            target_id=target_id,
            composition_sha256=composition,
            activation=activation,
            artifacts=tuple(artifacts),
            declared_effects=effects,
            previous_composition_required=value["previous_composition_required"],
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "composition_sha256": self.composition_sha256,
            "activation": self.activation,
            "artifacts": [artifact.__dict__.copy() for artifact in self.artifacts],
            "declared_effects": {
                key: list(value) if isinstance(value, tuple) else value
                for key, value in self.declared_effects.items()
            },
            "previous_composition_required": self.previous_composition_required,
        }


class SystemBackend(Protocol):
    def stage(self, request: SystemCompositionRequest) -> str: ...
    def activate(self, candidate_id: str) -> None: ...
    def rollback(self, candidate_id: str) -> None: ...


class ClosedSystemBackend:
    """Production-safe default until the privileged D-Bus service passes gates."""

    def stage(self, request: SystemCompositionRequest) -> str:
        raise TransactionError(
            "system Mod activation is disabled; the recovery-capable privileged backend is not installed"
        )

    def activate(self, candidate_id: str) -> None:
        raise TransactionError("system Mod activation is disabled")

    def rollback(self, candidate_id: str) -> None:
        raise TransactionError("system Mod rollback backend is unavailable")


def build_system_request(
    plan: Plan,
    trust_levels: Mapping[str, str],
    *,
    recovery_ready: bool,
) -> SystemCompositionRequest:
    """Translate a reviewed plan into data-only privileged input.

    This deliberately cannot encode an executable, argument vector, shell
    fragment, arbitrary destination, or post-install hook.
    """

    artifacts: list[ArtifactRequest] = []
    combined_effects: dict[str, list[str] | bool | str] = {}
    requires_recovery = plan.activation in {"reboot", "recovery-reboot"}
    for planned in plan.mods:
        inspection = planned.inspection
        mod = inspection.mod
        trust = trust_levels.get(mod.identity.id)
        if trust not in {"luma-core", "luma-verified", "community"}:
            raise TransactionError(
                f"system Mod {mod.identity.id} requires catalog-backed publisher verification"
            )
        protected = {
            capability.id for capability in (*mod.capabilities.provides, *mod.capabilities.replaces)
        } & PROTECTED_CAPABILITIES
        if protected and trust != "luma-core":
            raise TransactionError(
                f"only Luma Core may provide protected capability: {', '.join(sorted(protected))}"
            )
        kernel_facing = bool(
            mod.effects.kernel_modules
            or mod.effects.boot_arguments
            or mod.effects.initramfs
            or mod.effects.secure_boot != "none"
        )
        if kernel_facing and trust not in {"luma-core", "luma-verified"}:
            raise TransactionError(
                "kernel, initramfs, boot, and Secure Boot effects require "
                "Luma Core or Luma Verified policy"
            )
        if kernel_facing and trust == "luma-verified":
            health_gate = mod.recovery.get("health_gate")
            if (
                mod.identity.kind != "hardware"
                or not mod.compatibility.hardware
                or not mod.compatibility.kernel_releases
                or not mod.recovery.get("previous_composition_retained")
                or not isinstance(health_gate, str)
                or health_gate == "none"
            ):
                raise TransactionError(
                    "Luma Verified kernel-facing Mods must be hardware-scoped, "
                    "kernel-pinned, health-gated, and retain the previous composition"
                )
        for key, value in mod.effects.populated().items():
            if isinstance(value, tuple):
                existing = combined_effects.setdefault(key, [])
                if not isinstance(existing, list):
                    raise TransactionError(f"inconsistent effect type for {key}")
                for item in value:
                    if item not in existing:
                        existing.append(item)
            else:
                combined_effects[key] = value
        for payload in mod.payloads:
            backend = payload["backend"]
            if backend not in ALLOWED_BACKENDS:
                raise TransactionError(f"unsupported system payload backend: {backend}")
            artifacts.append(ArtifactRequest(
                mod_id=mod.identity.id,
                backend=backend,
                digest=payload["digest"],
                size=payload["size"],
                architecture=payload["architecture"],
                role=payload["role"],
                source=payload["source"],
            ))
    if requires_recovery and not recovery_ready:
        raise TransactionError(
            "this Mod requires a recovery-capable reboot, but no verified recovery path is ready"
        )
    if any(field in combined_effects for field in SYSTEM_EFFECTS) and not artifacts:
        raise TransactionError("system effects have no immutable payload artifacts")
    normalized: dict[str, tuple[str, ...] | bool | str] = {
        key: tuple(value) if isinstance(value, list) else value
        for key, value in combined_effects.items()
    }
    return SystemCompositionRequest(
        target_id=plan.target_id,
        composition_sha256=plan.composition_sha256,
        activation=plan.activation,
        artifacts=tuple(artifacts),
        declared_effects=normalized,
        previous_composition_required=requires_recovery,
    )
