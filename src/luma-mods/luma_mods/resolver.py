"""Deterministic, read-only Mod dependency and impact planning."""

from __future__ import annotations

import hashlib
import json
import platform
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .errors import CatalogError, ResolutionError
from .manifest import ID_PATTERN, INSTALL_MODES, KERNEL_RELEASE_PATTERN, inspect_manifest
from .model import (
    DependencyEdge,
    Inspection,
    ModAssessment,
    Plan,
    PlannedMod,
    ProviderTransition,
)

IMPACT_ORDER = {
    "application": 0,
    "appearance": 1,
    "behavior": 2,
    "capability": 3,
    "developer": 3,
    "compatibility": 4,
    "experience": 5,
    "hardware": 6,
    "core-system": 7,
}
ACTIVATION_ORDER = {
    "live": 0,
    "service-restart": 1,
    "session-restart": 2,
    "reboot": 3,
    "recovery-reboot": 4,
}
EXCLUSIVE_EFFECT_FIELDS = (
    "settings",
    "files",
    "services",
    "dbus_names",
    "portals",
    "configuration_domains",
    "kernel_modules",
    "devices",
    "firmware",
)
HIERARCHICAL_DOMAIN_FIELDS = {"settings", "configuration_domains"}
CONSTRAINT = re.compile(r"^(>=|<=|>|<|=)?([0-9]+\.[0-9]+\.[0-9]+)$")
EXPERIENCE_CAPABILITIES = {
    "org.luma.session.compositor",
    "org.luma.session.shell",
    "org.luma.session.screen-locker",
    "org.luma.session.notifications",
    "org.luma.session.polkit-agent",
    "org.luma.session.power",
}
PROVIDER_OWNERSHIP = {"exclusive", "selectable", "shared"}


@dataclass(frozen=True)
class ActiveCapabilityProvider:
    provider: str
    version: str
    ownership: str


def _version_tuple(version: str) -> tuple[int, int, int]:
    core = version.split("-", 1)[0].split("+", 1)[0]
    try:
        parts = tuple(int(part) for part in core.split("."))
    except ValueError as error:
        raise ResolutionError(f"unsupported version value: {version}") from error
    if len(parts) != 3:
        raise ResolutionError(f"unsupported version value: {version}")
    return parts


def version_satisfies(version: str, expression: str) -> bool:
    candidate = _version_tuple(version)
    for token in expression.split():
        match = CONSTRAINT.fullmatch(token)
        if not match:
            raise ResolutionError(f"unsupported version constraint: {expression}")
        operator = match.group(1) or "="
        wanted = _version_tuple(match.group(2))
        comparisons = {
            "=": candidate == wanted,
            ">": candidate > wanted,
            ">=": candidate >= wanted,
            "<": candidate < wanted,
            "<=": candidate <= wanted,
        }
        if not comparisons[operator]:
            return False
    return True


def _normalize_architecture(value: str) -> str:
    aliases = {"amd64": "x86_64", "arm64": "aarch64"}
    return aliases.get(value.lower(), value.lower())


def _path_contains(parent: str, child: str) -> bool:
    if not parent.startswith("/") or posixpath.normpath(parent) != parent:
        raise ResolutionError(f"host effect path is not normalized and absolute: {parent}")
    if not child.startswith("/") or posixpath.normpath(child) != child:
        raise ResolutionError(f"Mod effect path is not normalized and absolute: {child}")
    parent_parts = tuple(part for part in parent.split("/") if part)
    child_parts = tuple(part for part in child.split("/") if part)
    return len(parent_parts) <= len(child_parts) and child_parts[: len(parent_parts)] == parent_parts


def _domain_contains(parent: str, child: str) -> bool:
    if not ID_PATTERN.fullmatch(parent):
        raise ResolutionError(f"host effect domain is invalid: {parent}")
    if not ID_PATTERN.fullmatch(child):
        raise ResolutionError(f"Mod effect domain is invalid: {child}")
    parent_parts = tuple(parent.split("."))
    child_parts = tuple(child.split("."))
    return len(parent_parts) <= len(child_parts) and child_parts[: len(parent_parts)] == parent_parts


def _effects_overlap(field: str, first: str, second: str) -> bool:
    if field == "files":
        return _path_contains(first, second) or _path_contains(second, first)
    if field in HIERARCHICAL_DOMAIN_FIELDS:
        return _domain_contains(first, second) or _domain_contains(second, first)
    return first == second


def _validate_effect_owners(
    effect_owners: Mapping[str, Mapping[str, str]],
) -> None:
    allowed = set(EXCLUSIVE_EFFECT_FIELDS) | {"boot_arguments"}
    unknown = sorted(set(effect_owners) - allowed)
    if unknown:
        raise ResolutionError(
            f"host effect owners contain unknown fields: {', '.join(unknown)}"
        )
    for field, owners in effect_owners.items():
        for effect, owner in owners.items():
            if not effect or not owner:
                raise ResolutionError(f"host effect owner in {field} must not be empty")
            if field == "files":
                _path_contains(effect, effect)
            elif field in HIERARCHICAL_DOMAIN_FIELDS:
                _domain_contains(effect, effect)
            elif field == "devices" and any(character in effect for character in "*?["):
                raise ResolutionError("host device ownership does not permit wildcards")


@dataclass(frozen=True)
class HostContext:
    luma_base: str
    architecture: str
    presentation: str
    kernel_release: str
    install_mode: str
    hardware: tuple[str, ...]
    capabilities: Mapping[str, str]
    capability_providers: Mapping[str, ActiveCapabilityProvider]
    installed_mods: Mapping[str, str]
    effect_owners: Mapping[str, Mapping[str, str]]

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HostContext":
        allowed = {
            "luma_base",
            "architecture",
            "presentation",
            "kernel_release",
            "install_mode",
            "hardware",
            "capabilities",
            "capability_providers",
            "installed_mods",
            "effect_owners",
        }
        unknown = sorted(set(value) - allowed)
        required = allowed - {
            "effect_owners",
            "capability_providers",
            "kernel_release",
            "install_mode",
        }
        missing = sorted(required - set(value))
        if unknown:
            raise ResolutionError(f"host context contains unknown fields: {', '.join(unknown)}")
        if missing:
            raise ResolutionError(f"host context is missing fields: {', '.join(missing)}")
        try:
            hardware = tuple(str(item) for item in value["hardware"])  # type: ignore[arg-type]
            capabilities = {str(key): str(item) for key, item in value["capabilities"].items()}  # type: ignore[union-attr]
            raw_providers = value.get("capability_providers", {})
            capability_providers: dict[str, ActiveCapabilityProvider] = {}
            for capability_id, raw_provider in raw_providers.items():  # type: ignore[union-attr]
                provider = {str(key): str(item) for key, item in raw_provider.items()}
                unknown_provider_fields = sorted(
                    set(provider) - {"provider", "version", "ownership"}
                )
                missing_provider_fields = sorted(
                    {"provider", "version", "ownership"} - set(provider)
                )
                if unknown_provider_fields:
                    raise ResolutionError(
                        f"host capability provider {capability_id} contains unknown fields: "
                        f"{', '.join(unknown_provider_fields)}"
                    )
                if missing_provider_fields:
                    raise ResolutionError(
                        f"host capability provider {capability_id} is missing fields: "
                        f"{', '.join(missing_provider_fields)}"
                    )
                capability_providers[str(capability_id)] = ActiveCapabilityProvider(
                    provider=provider["provider"],
                    version=provider["version"],
                    ownership=provider["ownership"],
                )
            installed = {str(key): str(item) for key, item in value["installed_mods"].items()}  # type: ignore[union-attr]
            raw_effect_owners = value.get("effect_owners", {})
            effect_owners = {
                str(field): {str(item): str(owner) for item, owner in owners.items()}
                for field, owners in raw_effect_owners.items()  # type: ignore[union-attr]
            }
        except (AttributeError, TypeError) as error:
            raise ResolutionError("host context collections have invalid types") from error
        for capability_id, provider in capability_providers.items():
            if not ID_PATTERN.fullmatch(capability_id):
                raise ResolutionError(f"host capability provider ID is invalid: {capability_id}")
            if capability_id not in capabilities:
                raise ResolutionError(
                    f"host capability provider {capability_id} has no matching capability"
                )
            if not ID_PATTERN.fullmatch(provider.provider):
                raise ResolutionError(
                    f"host capability provider identity is invalid: {provider.provider}"
                )
            if provider.version != capabilities[capability_id]:
                raise ResolutionError(
                    f"host capability provider {capability_id} version {provider.version} "
                    f"does not match capability version {capabilities[capability_id]}"
                )
            if provider.ownership not in PROVIDER_OWNERSHIP:
                raise ResolutionError(
                    f"host capability provider {capability_id} has invalid ownership: "
                    f"{provider.ownership}"
                )
        kernel_release = str(value.get("kernel_release", ""))
        if kernel_release and not KERNEL_RELEASE_PATTERN.fullmatch(kernel_release):
            raise ResolutionError("host kernel_release is not an exact kernel identity")
        install_mode = str(value.get("install_mode", "running-system"))
        if install_mode not in INSTALL_MODES:
            raise ResolutionError(f"host install mode is invalid: {install_mode}")
        return cls(
            luma_base=str(value["luma_base"]),
            architecture=_normalize_architecture(str(value["architecture"])),
            presentation=str(value["presentation"]),
            kernel_release=kernel_release,
            install_mode=install_mode,
            hardware=hardware,
            capabilities=capabilities,
            capability_providers=capability_providers,
            installed_mods=installed,
            effect_owners=effect_owners,
        )

    @classmethod
    def minimal(cls, luma_base: str = "0.1.0", presentation: str = "desktop") -> "HostContext":
        return cls(
            luma_base=luma_base,
            architecture=_normalize_architecture(platform.machine()),
            presentation=presentation,
            kernel_release=platform.release(),
            install_mode="running-system",
            hardware=(),
            capabilities={},
            capability_providers={},
            installed_mods={},
            effect_owners={},
        )


class Catalog:
    """An immutable local index of already-inspected Mod manifests."""

    def __init__(self, inspections: Iterable[Inspection]):
        entries: dict[str, Inspection] = {}
        for inspection in inspections:
            identifier = inspection.mod.identity.id
            if identifier in entries:
                raise CatalogError(f"catalog contains duplicate Mod ID: {identifier}")
            entries[identifier] = inspection
        self._entries = entries

    @classmethod
    def from_directory(cls, path: str | Path) -> "Catalog":
        root = Path(path)
        if not root.is_dir():
            raise CatalogError(f"catalog is not a directory: {root}")
        inspections: list[Inspection] = []
        for manifest in sorted(root.glob("*.mod.json")):
            inspections.append(inspect_manifest(manifest))
        if not inspections:
            raise CatalogError(f"catalog contains no *.mod.json manifests: {root}")
        return cls(inspections)

    def require(self, identifier: str) -> Inspection:
        try:
            return self._entries[identifier]
        except KeyError as error:
            raise ResolutionError(f"required Mod is not available: {identifier}") from error

    def inspections(self) -> tuple[Inspection, ...]:
        """Return catalog entries in stable identity order for discovery UI."""

        return tuple(self._entries[key] for key in sorted(self._entries))


def host_context_record(host: HostContext) -> dict[str, object]:
    """Return the canonical host tuple used by planning and image locking."""

    return {
        "luma_base": host.luma_base,
        "architecture": host.architecture,
        "presentation": host.presentation,
        "kernel_release": host.kernel_release,
        "install_mode": host.install_mode,
        "hardware": sorted(host.hardware),
        "capabilities": dict(sorted(host.capabilities.items())),
        "capability_providers": {
            capability_id: {
                "provider": provider.provider,
                "version": provider.version,
                "ownership": provider.ownership,
            }
            for capability_id, provider in sorted(host.capability_providers.items())
        },
        "installed_mods": dict(sorted(host.installed_mods.items())),
        "effect_owners": {
            field: dict(sorted(owners.items()))
            for field, owners in sorted(host.effect_owners.items())
        },
    }


def host_context_sha256(host: HostContext) -> str:
    return hashlib.sha256(
        json.dumps(host_context_record(host), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def assess_mod(inspection: Inspection) -> ModAssessment:
    mod = inspection.mod
    impact = mod.identity.kind
    activation = "live"
    reasons: list[str] = [f"Declared Mod kind: {mod.identity.kind}"]
    effects = mod.effects

    if effects.services or effects.dbus_names:
        activation = "service-restart"
        reasons.append("Adds or changes a background/system service")
    if mod.identity.kind == "experience" or any(
        capability.id in EXPERIENCE_CAPABILITIES for capability in mod.capabilities.provides
    ):
        impact = "experience"
        activation = "session-restart"
        reasons.append("Adds or replaces a session experience provider")
    if effects.files or effects.packages or effects.configuration_domains or effects.firmware:
        activation = max((activation, "reboot"), key=ACTIVATION_ORDER.__getitem__)
        reasons.append("Changes deployment-owned system content")
    if effects.devices or effects.kernel_modules or effects.firmware:
        impact = max((impact, "hardware"), key=IMPACT_ORDER.__getitem__)
        reasons.append("Changes hardware-facing support")
    if (
        effects.kernel_modules
        or effects.initramfs
        or effects.boot_arguments
        or effects.secure_boot != "none"
    ):
        impact = "core-system"
        activation = "recovery-reboot"
        reasons.append("Changes the kernel, initramfs, boot, or Secure Boot boundary")
    return ModAssessment(impact=impact, activation=activation, reasons=tuple(dict.fromkeys(reasons)))


def _validate_compatibility(inspection: Inspection, host: HostContext) -> None:
    mod = inspection.mod
    compatibility = mod.compatibility
    if not version_satisfies(host.luma_base, compatibility.luma_base):
        raise ResolutionError(
            f"{mod.identity.name} requires Luma {compatibility.luma_base}; host is {host.luma_base}"
        )
    if _normalize_architecture(host.architecture) not in {
        _normalize_architecture(item) for item in compatibility.architectures
    }:
        raise ResolutionError(
            f"{mod.identity.name} does not support architecture {host.architecture}"
        )
    if host.presentation not in compatibility.presentations:
        raise ResolutionError(
            f"{mod.identity.name} does not support presentation {host.presentation}"
        )
    if host.install_mode not in {"running-system", "image-compose"}:
        raise ResolutionError(f"host install mode is invalid: {host.install_mode}")
    if host.install_mode not in compatibility.install_modes:
        raise ResolutionError(
            f"{mod.identity.name} does not support install mode {host.install_mode}"
        )
    if compatibility.kernel_releases:
        if not host.kernel_release:
            raise ResolutionError(
                f"{mod.identity.name} is kernel-bound, but the host kernel identity is absent"
            )
        if host.kernel_release not in compatibility.kernel_releases:
            raise ResolutionError(
                f"{mod.identity.name} does not support kernel {host.kernel_release}"
            )
    if compatibility.hardware and not set(compatibility.hardware).issubset(host.hardware):
        missing = sorted(set(compatibility.hardware) - set(host.hardware))
        raise ResolutionError(
            f"{mod.identity.name} requires hardware identity: {', '.join(missing)}"
        )


def resolve_mod(target_id: str, catalog: Catalog, host: HostContext) -> Plan:
    """Resolve a Mod and its declared dependencies without changing the host."""

    _validate_effect_owners(host.effect_owners)
    ordered: list[Inspection] = []
    edges: list[DependencyEdge] = []
    visiting: list[str] = []
    selected: dict[str, Inspection] = {}
    installed_dependencies: dict[str, Inspection] = {}

    def visit(identifier: str) -> None:
        if identifier in selected:
            return
        if identifier in visiting:
            cycle = " -> ".join((*visiting, identifier))
            raise ResolutionError(f"Mod dependency cycle: {cycle}")
        inspection = catalog.require(identifier)
        _validate_compatibility(inspection, host)
        visiting.append(identifier)
        for dependency in inspection.mod.dependencies:
            installed_version = host.installed_mods.get(dependency.id)
            already_installed = installed_version is not None and version_satisfies(
                installed_version, dependency.version
            )
            dependency_manifest = catalog.require(dependency.id)
            edges.append(
                DependencyEdge(
                    parent_id=identifier,
                    dependency_id=dependency.id,
                    dependency_name=dependency_manifest.mod.identity.name,
                    dependency_publisher=dependency_manifest.mod.identity.publisher.name,
                    version=dependency.version,
                    selected_version=dependency_manifest.mod.identity.version,
                    reason=dependency.reason,
                    already_installed=already_installed,
                )
            )
            if already_installed:
                if dependency_manifest.mod.identity.version != installed_version:
                    raise ResolutionError(
                        f"installed {dependency.id} is {installed_version}, but the local catalog "
                        f"describes {dependency_manifest.mod.identity.version}; its capabilities "
                        "cannot be inferred safely"
                    )
                installed_dependencies[dependency.id] = dependency_manifest
            else:
                if not version_satisfies(dependency_manifest.mod.identity.version, dependency.version):
                    raise ResolutionError(
                        f"{inspection.mod.identity.name} requires {dependency.id} "
                        f"{dependency.version}; catalog has {dependency_manifest.mod.identity.version}"
                    )
                visit(dependency.id)
        visiting.pop()
        selected[identifier] = inspection
        ordered.append(inspection)

    visit(target_id)

    providers: dict[str, list[tuple[str, str, str]]] = {}
    for capability_id, version in host.capabilities.items():
        provider = host.capability_providers.get(capability_id)
        providers.setdefault(capability_id, []).append(
            (
                provider.provider if provider else "host",
                version,
                provider.ownership if provider else "shared",
            )
        )
    for inspection in installed_dependencies.values():
        for capability in inspection.mod.capabilities.provides:
            providers.setdefault(capability.id, []).append(
                (inspection.mod.identity.id, capability.version, capability.ownership)
            )
    for inspection in ordered:
        for capability in inspection.mod.capabilities.provides:
            providers.setdefault(capability.id, []).append(
                (inspection.mod.identity.id, capability.version, capability.ownership)
            )

    provider_transitions: list[ProviderTransition] = []
    replaced_host_capabilities: set[str] = set()
    for inspection in ordered:
        mod = inspection.mod
        provided = {capability.id: capability for capability in mod.capabilities.provides}
        replacements = {capability.id: capability for capability in mod.capabilities.replaces}
        for capability_id, replacement in replacements.items():
            offered = provided.get(capability_id)
            if offered is None:
                raise ResolutionError(
                    f"{mod.identity.name} declares replacement of {capability_id} but does not "
                    "provide its replacement"
                )
            if offered.ownership != replacement.ownership:
                raise ResolutionError(
                    f"{mod.identity.name} changes {capability_id} ownership while replacing it"
                )
            current = host.capability_providers.get(capability_id)
            if current is None:
                raise ResolutionError(
                    f"{mod.identity.name} cannot replace {capability_id}: the active provider "
                    "is not identified by the host"
                )
            if current.ownership != replacement.ownership:
                raise ResolutionError(
                    f"{mod.identity.name} replacement ownership for {capability_id} does not "
                    "match the active provider"
                )
            if not version_satisfies(current.version, replacement.version):
                raise ResolutionError(
                    f"{mod.identity.name} replaces {capability_id} {replacement.version}, but "
                    f"the active provider has {current.version}"
                )
            if current.provider == mod.identity.id:
                continue
            replaced_host_capabilities.add(capability_id)
            provider_transitions.append(
                ProviderTransition(
                    capability_id=capability_id,
                    ownership=replacement.ownership,
                    previous_provider=current.provider,
                    previous_version=current.version,
                    next_provider=mod.identity.id,
                    next_version=offered.version,
                    reason=replacement.reason,
                )
            )

        for capability_id, offered in provided.items():
            current = host.capability_providers.get(capability_id)
            if (
                capability_id in host.capabilities
                and current is None
                and offered.ownership in {"exclusive", "selectable"}
            ):
                raise ResolutionError(
                    f"{mod.identity.name} cannot select {capability_id}: the active provider "
                    "is not identified by the host"
                )
            if (
                current
                and current.provider != mod.identity.id
                and offered.ownership in {"exclusive", "selectable"}
                and capability_id not in replacements
            ):
                raise ResolutionError(
                    f"{mod.identity.name} would replace active {offered.ownership} provider "
                    f"{current.provider} for {capability_id} without declaring it in "
                    "capabilities.replaces"
                )

    experience_mods = [
        inspection for inspection in ordered if inspection.mod.identity.kind == "experience"
    ]
    for inspection in experience_mods:
        provided_ids = {
            capability.id for capability in inspection.mod.capabilities.provides
        }
        missing = sorted(EXPERIENCE_CAPABILITIES - provided_ids)
        if missing:
            raise ResolutionError(
                f"{inspection.mod.identity.name} is an incomplete experience provider; missing: "
                f"{', '.join(missing)}"
            )

    if replaced_host_capabilities:
        providers = {
            capability_id: [
                entry
                for entry in entries
                if not (
                    capability_id in replaced_host_capabilities
                    and capability_id in host.capability_providers
                    and entry[0] == host.capability_providers[capability_id].provider
                )
            ]
            for capability_id, entries in providers.items()
        }

    for inspection in ordered:
        mod = inspection.mod
        for required in mod.capabilities.requires:
            matches = providers.get(required.id, [])
            if not any(version_satisfies(version, required.version) for _, version, _ in matches):
                raise ResolutionError(
                    f"{mod.identity.name} requires capability {required.id} "
                    f"{required.version}: {required.reason}"
                )
        for conflict in mod.capabilities.conflicts:
            matches = [provider for provider in providers.get(conflict.id, []) if provider[0] != mod.identity.id]
            if matches:
                names = ", ".join(provider[0] for provider in matches)
                raise ResolutionError(
                    f"{mod.identity.name} conflicts with {conflict.id} from {names}: {conflict.reason}"
                )

    for capability_id, entries in providers.items():
        exclusive = [entry for entry in entries if entry[2] == "exclusive"]
        if len(exclusive) > 1:
            names = ", ".join(entry[0] for entry in exclusive)
            raise ResolutionError(
                f"exclusive capability {capability_id} has multiple providers: {names}"
            )

    effect_owners: dict[str, dict[str, str]] = {
        field: dict(host.effect_owners.get(field, {})) for field in EXCLUSIVE_EFFECT_FIELDS
    }
    boot_arguments: dict[str, tuple[str, str]] = {}
    for argument, owner in host.effect_owners.get("boot_arguments", {}).items():
        key = argument.split("=", 1)[0]
        boot_arguments[key] = (argument, owner)

    for inspection in ordered:
        mod = inspection.mod
        for field in EXCLUSIVE_EFFECT_FIELDS:
            owners = effect_owners[field]
            for effect in getattr(mod.effects, field):
                for owned_effect, previous in owners.items():
                    if previous != mod.identity.id and _effects_overlap(
                        field, owned_effect, effect
                    ):
                        raise ResolutionError(
                            f"{mod.identity.name} claims {field}.{effect}, which overlaps "
                            f"{field}.{owned_effect} owned by {previous}"
                        )
                owners[effect] = mod.identity.id
        for argument in mod.effects.boot_arguments:
            key = argument.split("=", 1)[0]
            previous = boot_arguments.get(key)
            if previous and previous[0] != argument:
                raise ResolutionError(
                    f"{mod.identity.name} sets boot argument {argument}, but {previous[1]} "
                    f"already sets {previous[0]}"
                )
            boot_arguments[key] = (argument, mod.identity.id)

    planned = tuple(PlannedMod(item, assess_mod(item)) for item in ordered)
    overall_impact = max((item.assessment.impact for item in planned), key=IMPACT_ORDER.__getitem__)
    overall_activation = max(
        (item.assessment.activation for item in planned), key=ACTIVATION_ORDER.__getitem__
    )
    composition_input = {
        "target": target_id,
        "host": host_context_record(host),
        "mods": [item.inspection.canonical_sha256 for item in planned],
    }
    canonical = json.dumps(composition_input, sort_keys=True, separators=(",", ":")).encode()
    return Plan(
        target_id=target_id,
        mods=planned,
        dependencies=tuple(edges),
        impact=overall_impact,
        activation=overall_activation,
        composition_sha256=hashlib.sha256(canonical).hexdigest(),
        provider_transitions=tuple(
            sorted(provider_transitions, key=lambda item: item.capability_id)
        ),
        host_sha256=host_context_sha256(host),
    )
