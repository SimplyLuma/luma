"""Immutable data model for inspected Luma Mods."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class Publisher:
    id: str
    name: str


@dataclass(frozen=True)
class Identity:
    id: str
    version: str
    kind: str
    name: str
    summary: str
    licenses: tuple[str, ...]
    publisher: Publisher


@dataclass(frozen=True)
class Dependency:
    id: str
    version: str
    reason: str


@dataclass(frozen=True)
class Capability:
    id: str
    version: str
    ownership: str
    reason: str


@dataclass(frozen=True)
class CapabilitySet:
    provides: tuple[Capability, ...]
    requires: tuple[Capability, ...]
    conflicts: tuple[Capability, ...]
    replaces: tuple[Capability, ...]


@dataclass(frozen=True)
class Compatibility:
    luma_base: str
    architectures: tuple[str, ...]
    presentations: tuple[str, ...]
    hardware: tuple[str, ...]
    kernel_releases: tuple[str, ...]
    install_modes: tuple[str, ...]


@dataclass(frozen=True)
class Effects:
    settings: tuple[str, ...]
    files: tuple[str, ...]
    packages: tuple[str, ...]
    services: tuple[str, ...]
    dbus_names: tuple[str, ...]
    portals: tuple[str, ...]
    devices: tuple[str, ...]
    configuration_domains: tuple[str, ...]
    kernel_modules: tuple[str, ...]
    firmware: tuple[str, ...]
    boot_arguments: tuple[str, ...]
    user_data: tuple[str, ...]
    initramfs: bool
    secure_boot: str

    def populated(self) -> Mapping[str, tuple[str, ...] | bool | str]:
        result: dict[str, tuple[str, ...] | bool | str] = {}
        for name in (
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
        ):
            value = getattr(self, name)
            if value:
                result[name] = value
        if self.initramfs:
            result["initramfs"] = True
        if self.secure_boot != "none":
            result["secure_boot"] = self.secure_boot
        return result


@dataclass(frozen=True)
class Signature:
    signer: str
    algorithm: str
    manifest_digest: str
    bundle: str
    bundle_digest: str


@dataclass(frozen=True)
class Mod:
    schema: str
    identity: Identity
    compatibility: Compatibility
    dependencies: tuple[Dependency, ...]
    capabilities: CapabilitySet
    effects: Effects
    payloads: tuple[Mapping[str, Any], ...]
    recovery: Mapping[str, Any]
    evidence: Mapping[str, Any]
    signatures: tuple[Signature, ...]


@dataclass(frozen=True)
class Inspection:
    path: Path
    source_sha256: str
    canonical_sha256: str
    canonical_json: bytes
    signing_sha256: str
    signing_json: bytes
    mod: Mod


@dataclass(frozen=True)
class ModAssessment:
    impact: str
    activation: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DependencyEdge:
    parent_id: str
    dependency_id: str
    dependency_name: str
    dependency_publisher: str
    version: str
    selected_version: str
    reason: str
    already_installed: bool


@dataclass(frozen=True)
class PlannedMod:
    inspection: Inspection
    assessment: ModAssessment


@dataclass(frozen=True)
class ProviderTransition:
    capability_id: str
    ownership: str
    previous_provider: str
    previous_version: str
    next_provider: str
    next_version: str
    reason: str


@dataclass(frozen=True)
class Plan:
    target_id: str
    mods: tuple[PlannedMod, ...]
    dependencies: tuple[DependencyEdge, ...]
    impact: str
    activation: str
    composition_sha256: str
    provider_transitions: tuple[ProviderTransition, ...] = ()
    host_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "impact": self.impact,
            "activation": self.activation,
            "composition_sha256": self.composition_sha256,
            "host_sha256": self.host_sha256,
            "provider_transitions": [
                {
                    "capability": transition.capability_id,
                    "ownership": transition.ownership,
                    "from": {
                        "provider": transition.previous_provider,
                        "version": transition.previous_version,
                    },
                    "to": {
                        "provider": transition.next_provider,
                        "version": transition.next_version,
                    },
                    "reason": transition.reason,
                }
                for transition in self.provider_transitions
            ],
            "mods": [
                {
                    "id": item.inspection.mod.identity.id,
                    "version": item.inspection.mod.identity.version,
                    "name": item.inspection.mod.identity.name,
                    "publisher": item.inspection.mod.identity.publisher.name,
                    "kind": item.inspection.mod.identity.kind,
                    "impact": item.assessment.impact,
                    "activation": item.assessment.activation,
                    "reasons": list(item.assessment.reasons),
                    "manifest_sha256": item.inspection.canonical_sha256,
                    "effects": {
                        key: list(value) if isinstance(value, tuple) else value
                        for key, value in item.inspection.mod.effects.populated().items()
                    },
                }
                for item in self.mods
            ],
            "dependencies": [
                {
                    "required_by": edge.parent_id,
                    "id": edge.dependency_id,
                    "name": edge.dependency_name,
                    "publisher": edge.dependency_publisher,
                    "version": edge.version,
                    "selected_version": edge.selected_version,
                    "reason": edge.reason,
                    "already_installed": edge.already_installed,
                }
                for edge in self.dependencies
            ],
            "mutated_host": False,
        }
