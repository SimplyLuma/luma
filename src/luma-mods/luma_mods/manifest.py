"""Strict, bounded, non-mutating Luma Mod manifest inspection."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ManifestValidationError
from .model import (
    Capability,
    CapabilitySet,
    Compatibility,
    Dependency,
    Effects,
    Identity,
    Inspection,
    Mod,
    Publisher,
    Signature,
)

SCHEMA = "org.luma.mod/v0.1"
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
KERNEL_RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")

MOD_KINDS = {
    "application",
    "appearance",
    "behavior",
    "capability",
    "experience",
    "hardware",
    "developer",
    "compatibility",
    "core-system",
}
OWNERSHIP = {"exclusive", "selectable", "shared"}
SECURE_BOOT_EFFECTS = {"none", "signing-required", "enrollment-required", "chain-change"}
SIGNATURE_ALGORITHMS = {"sigstore-bundle-v0.3"}
INSTALL_MODES = {"running-system", "image-compose"}
BUNDLE_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}\.sigstore(?:\.json)?$"
)


@dataclass(frozen=True)
class ManifestLimits:
    max_bytes: int = 1024 * 1024
    max_depth: int = 32
    max_nodes: int = 20_000
    max_list_items: int = 2_048
    max_string_bytes: int = 16_384


def _fail(message: str) -> None:
    raise ManifestValidationError(message)


def _expect_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{where} must be an object")
    return value


def _expect_list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{where} must be a list")
    return value


def _only_keys(value: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        _fail(f"{where} contains unknown field(s): {', '.join(unknown)}")


def _required(value: Mapping[str, Any], keys: set[str], where: str) -> None:
    missing = sorted(keys - set(value))
    if missing:
        _fail(f"{where} is missing required field(s): {', '.join(missing)}")


def _string(value: Any, where: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        _fail(f"{where} must be text")
    if nonempty and not value.strip():
        _fail(f"{where} must not be empty")
    return value


def _strings(value: Any, where: str) -> tuple[str, ...]:
    result = tuple(_string(item, f"{where}[]") for item in _expect_list(value, where))
    if len(set(result)) != len(result):
        _fail(f"{where} must not contain duplicates")
    return result


def _identifier(value: Any, where: str) -> str:
    identifier = _string(value, where)
    if not ID_PATTERN.fullmatch(identifier):
        _fail(f"{where} is not a valid reverse-domain identifier")
    return identifier


def _version(value: Any, where: str) -> str:
    version = _string(value, where)
    if not VERSION_PATTERN.fullmatch(version):
        _fail(f"{where} must be a semantic version")
    return version


def _digest(value: Any, where: str) -> str:
    digest = _string(value, where)
    if not DIGEST_PATTERN.fullmatch(digest):
        _fail(f"{where} must be a lowercase sha256 digest")
    return digest


def _bounded_tree(root: Any, limits: ManifestLimits) -> None:
    stack: list[tuple[Any, int]] = [(root, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            _fail("manifest contains too many values")
        if depth > limits.max_depth:
            _fail("manifest nesting is too deep")
        if isinstance(value, str) and len(value.encode("utf-8")) > limits.max_string_bytes:
            _fail("manifest contains an oversized text value")
        if isinstance(value, list):
            if len(value) > limits.max_list_items:
                _fail("manifest contains an oversized list")
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, dict):
            if len(value) > limits.max_list_items:
                _fail("manifest contains an oversized object")
            stack.extend((key, depth + 1) for key in value)
            stack.extend((item, depth + 1) for item in value.values())


def _duplicate_rejecting_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"manifest contains duplicate field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail(f"manifest contains unsupported numeric constant: {value}")


def _read_regular_file(path: Path, limits: ManifestLimits) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ManifestValidationError(f"cannot open Mod manifest safely: {error.strerror}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("Mod manifest must be a regular file")
        if metadata.st_size > limits.max_bytes:
            _fail(f"Mod manifest is larger than the {limits.max_bytes}-byte limit")
        chunks: list[bytes] = []
        remaining = limits.max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limits.max_bytes:
            _fail(f"Mod manifest is larger than the {limits.max_bytes}-byte limit")
        return payload
    finally:
        os.close(descriptor)


def _capability(value: Any, where: str, *, default_ownership: str) -> Capability:
    item = _expect_mapping(value, where)
    allowed = {"id", "version", "ownership", "reason"}
    _only_keys(item, allowed, where)
    _required(item, {"id", "version", "reason"}, where)
    ownership = _string(item.get("ownership", default_ownership), f"{where}.ownership")
    if ownership not in OWNERSHIP:
        _fail(f"{where}.ownership must be one of: {', '.join(sorted(OWNERSHIP))}")
    return Capability(
        id=_identifier(item["id"], f"{where}.id"),
        version=_string(item["version"], f"{where}.version"),
        ownership=ownership,
        reason=_string(item["reason"], f"{where}.reason"),
    )


def _capabilities(value: Any) -> CapabilitySet:
    item = _expect_mapping(value, "capabilities")
    allowed = {"provides", "requires", "conflicts", "replaces"}
    _only_keys(item, allowed, "capabilities")
    _required(item, allowed, "capabilities")

    def entries(name: str, ownership: str) -> tuple[Capability, ...]:
        result = tuple(
            _capability(entry, f"capabilities.{name}[]", default_ownership=ownership)
            for entry in _expect_list(item[name], f"capabilities.{name}")
        )
        ids = [entry.id for entry in result]
        if len(set(ids)) != len(ids):
            _fail(f"capabilities.{name} contains duplicate capability IDs")
        return result

    return CapabilitySet(
        provides=entries("provides", "shared"),
        requires=entries("requires", "shared"),
        conflicts=entries("conflicts", "shared"),
        replaces=entries("replaces", "shared"),
    )


def _dependencies(value: Any) -> tuple[Dependency, ...]:
    result: list[Dependency] = []
    for index, raw in enumerate(_expect_list(value, "dependencies")):
        where = f"dependencies[{index}]"
        item = _expect_mapping(raw, where)
        _only_keys(item, {"id", "version", "reason"}, where)
        _required(item, {"id", "version", "reason"}, where)
        result.append(
            Dependency(
                id=_identifier(item["id"], f"{where}.id"),
                version=_string(item["version"], f"{where}.version"),
                reason=_string(item["reason"], f"{where}.reason"),
            )
        )
    ids = [entry.id for entry in result]
    if len(set(ids)) != len(ids):
        _fail("dependencies contains duplicate Mod IDs")
    return tuple(result)


def _effects(value: Any) -> Effects:
    item = _expect_mapping(value, "effects")
    list_fields = {
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
    }
    allowed = list_fields | {"initramfs", "secure_boot"}
    _only_keys(item, allowed, "effects")
    _required(item, allowed, "effects")
    values = {name: _strings(item[name], f"effects.{name}") for name in list_fields}
    for path in values["files"]:
        if not path.startswith("/") or path == "/" or posixpath.normpath(path) != path:
            _fail("effects.files entries must be normalized absolute paths below /")
    for field in ("settings", "configuration_domains"):
        for domain in values[field]:
            if not ID_PATTERN.fullmatch(domain):
                _fail(f"effects.{field} entries must be reverse-domain identifiers")
    for device in values["devices"]:
        if any(character in device for character in "*?["):
            _fail("effects.devices does not permit wildcard ownership")
    if not isinstance(item["initramfs"], bool):
        _fail("effects.initramfs must be true or false")
    secure_boot = _string(item["secure_boot"], "effects.secure_boot")
    if secure_boot not in SECURE_BOOT_EFFECTS:
        _fail(
            "effects.secure_boot must be one of: "
            + ", ".join(sorted(SECURE_BOOT_EFFECTS))
        )
    return Effects(**values, initramfs=item["initramfs"], secure_boot=secure_boot)


def _payloads(value: Any) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    for index, raw in enumerate(_expect_list(value, "payloads")):
        where = f"payloads[{index}]"
        item = _expect_mapping(raw, where)
        allowed = {"backend", "digest", "size", "architecture", "role", "source"}
        _only_keys(item, allowed, where)
        _required(item, allowed, where)
        _string(item["backend"], f"{where}.backend")
        _digest(item["digest"], f"{where}.digest")
        if not isinstance(item["size"], int) or isinstance(item["size"], bool) or item["size"] < 0:
            _fail(f"{where}.size must be a non-negative integer")
        for field in ("architecture", "role", "source"):
            _string(item[field], f"{where}.{field}")
        result.append(dict(item))
    return tuple(result)


def _signed_mapping(value: Any, where: str, allowed: set[str]) -> Mapping[str, Any]:
    item = _expect_mapping(value, where)
    _only_keys(item, allowed, where)
    for key, raw in item.items():
        if key.endswith("_digest"):
            _digest(raw, f"{where}.{key}")
        elif isinstance(raw, str):
            _string(raw, f"{where}.{key}")
        elif isinstance(raw, bool):
            continue
        else:
            _fail(f"{where}.{key} has an unsupported value type")
    return dict(item)


def _parse_manifest(data: Any) -> Mod:
    root = _expect_mapping(data, "manifest")
    top = {
        "schema",
        "identity",
        "compatibility",
        "dependencies",
        "capabilities",
        "effects",
        "payloads",
        "recovery",
        "evidence",
        "signatures",
    }
    _only_keys(root, top, "manifest")
    _required(root, top, "manifest")
    if root["schema"] != SCHEMA:
        _fail(f"unsupported schema; expected {SCHEMA}")

    identity_raw = _expect_mapping(root["identity"], "identity")
    identity_keys = {"id", "version", "kind", "name", "summary", "license", "publisher"}
    _only_keys(identity_raw, identity_keys, "identity")
    _required(identity_raw, identity_keys, "identity")
    publisher_raw = _expect_mapping(identity_raw["publisher"], "identity.publisher")
    _only_keys(publisher_raw, {"id", "name"}, "identity.publisher")
    _required(publisher_raw, {"id", "name"}, "identity.publisher")
    kind = _string(identity_raw["kind"], "identity.kind")
    if kind not in MOD_KINDS:
        _fail(f"identity.kind must be one of: {', '.join(sorted(MOD_KINDS))}")
    licenses = _strings(identity_raw["license"], "identity.license")
    if not licenses:
        _fail("identity.license must contain at least one SPDX expression")
    identity = Identity(
        id=_identifier(identity_raw["id"], "identity.id"),
        version=_version(identity_raw["version"], "identity.version"),
        kind=kind,
        name=_string(identity_raw["name"], "identity.name"),
        summary=_string(identity_raw["summary"], "identity.summary"),
        licenses=licenses,
        publisher=Publisher(
            id=_identifier(publisher_raw["id"], "identity.publisher.id"),
            name=_string(publisher_raw["name"], "identity.publisher.name"),
        ),
    )

    compatibility_raw = _expect_mapping(root["compatibility"], "compatibility")
    compatibility_keys = {
        "luma_base",
        "architectures",
        "presentations",
        "hardware",
        "kernel_releases",
        "install_modes",
    }
    _only_keys(compatibility_raw, compatibility_keys, "compatibility")
    _required(
        compatibility_raw,
        {"luma_base", "architectures", "presentations", "hardware"},
        "compatibility",
    )
    kernel_releases = _strings(
        compatibility_raw.get("kernel_releases", []),
        "compatibility.kernel_releases",
    )
    for release in kernel_releases:
        if not KERNEL_RELEASE_PATTERN.fullmatch(release):
            _fail(
                "compatibility.kernel_releases entries must be exact kernel release identities"
            )
    install_modes = _strings(
        compatibility_raw.get("install_modes", ["running-system"]),
        "compatibility.install_modes",
    )
    if not install_modes or set(install_modes) - INSTALL_MODES:
        _fail(
            "compatibility.install_modes must contain running-system and/or image-compose"
        )
    compatibility = Compatibility(
        luma_base=_string(compatibility_raw["luma_base"], "compatibility.luma_base"),
        architectures=_strings(compatibility_raw["architectures"], "compatibility.architectures"),
        presentations=_strings(compatibility_raw["presentations"], "compatibility.presentations"),
        hardware=_strings(compatibility_raw["hardware"], "compatibility.hardware"),
        kernel_releases=kernel_releases,
        install_modes=install_modes,
    )
    if not compatibility.architectures:
        _fail("compatibility.architectures must not be empty")
    if not compatibility.presentations:
        _fail("compatibility.presentations must not be empty")

    effects = _effects(root["effects"])
    kernel_facing = bool(
        effects.kernel_modules
        or effects.boot_arguments
        or effects.initramfs
        or effects.secure_boot != "none"
    )
    if kernel_facing and not compatibility.kernel_releases:
        _fail(
            "kernel, initramfs, boot, and Secure Boot effects require exact "
            "compatibility.kernel_releases"
        )
    if kernel_facing and not compatibility.hardware:
        _fail(
            "kernel, initramfs, boot, and Secure Boot effects require positive "
            "compatibility.hardware identities"
        )

    recovery = _signed_mapping(
        root["recovery"],
        "recovery",
        {"previous_composition_retained", "health_gate", "user_data"},
    )
    _required(recovery, {"previous_composition_retained", "health_gate", "user_data"}, "recovery")
    if not isinstance(recovery["previous_composition_retained"], bool):
        _fail("recovery.previous_composition_retained must be true or false")
    if recovery["user_data"] not in {"retain", "prompt"}:
        _fail("recovery.user_data must be retain or prompt; deletion is a separate transaction")

    evidence = _signed_mapping(
        root["evidence"],
        "evidence",
        {"source", "source_digest", "sbom_digest", "provenance_digest", "test_result_digest"},
    )
    _required(evidence, {"source", "source_digest"}, "evidence")

    signatures: list[Signature] = []
    for index, raw in enumerate(_expect_list(root["signatures"], "signatures")):
        where = f"signatures[{index}]"
        item = _expect_mapping(raw, where)
        allowed = {"signer", "algorithm", "manifest_digest", "bundle", "bundle_digest"}
        _only_keys(item, allowed, where)
        _required(item, allowed, where)
        signer = _identifier(item["signer"], f"{where}.signer")
        algorithm = _string(item["algorithm"], f"{where}.algorithm")
        if algorithm not in SIGNATURE_ALGORITHMS:
            _fail(
                f"{where}.algorithm must be one of: "
                + ", ".join(sorted(SIGNATURE_ALGORITHMS))
            )
        manifest_digest = _digest(
            item["manifest_digest"], f"{where}.manifest_digest"
        )
        bundle = _string(item["bundle"], f"{where}.bundle")
        if not BUNDLE_NAME_PATTERN.fullmatch(bundle):
            _fail(f"{where}.bundle must be a local Sigstore sidecar filename")
        bundle_digest = _digest(item["bundle_digest"], f"{where}.bundle_digest")
        signatures.append(
            Signature(
                signer=signer,
                algorithm=algorithm,
                manifest_digest=manifest_digest,
                bundle=bundle,
                bundle_digest=bundle_digest,
            )
        )

    return Mod(
        schema=SCHEMA,
        identity=identity,
        compatibility=compatibility,
        dependencies=_dependencies(root["dependencies"]),
        capabilities=_capabilities(root["capabilities"]),
        effects=effects,
        payloads=_payloads(root["payloads"]),
        recovery=recovery,
        evidence=evidence,
        signatures=tuple(signatures),
    )


def inspect_manifest(path: str | os.PathLike[str], limits: ManifestLimits | None = None) -> Inspection:
    """Read and validate one manifest without executing or staging any content."""

    active_limits = limits or ManifestLimits()
    source = Path(path)
    payload = _read_regular_file(source, active_limits)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestValidationError("Mod manifest must be UTF-8 JSON") from error
    try:
        data = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_object,
            parse_constant=_reject_constant,
        )
    except ManifestValidationError:
        raise
    except (json.JSONDecodeError, RecursionError) as error:
        raise ManifestValidationError(f"Mod manifest is not valid bounded JSON: {error}") from error
    _bounded_tree(data, active_limits)
    mod = _parse_manifest(data)
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signing_data = dict(data)
    signing_data["signatures"] = []
    signing = json.dumps(
        signing_data,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Inspection(
        path=source,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        canonical_sha256=hashlib.sha256(canonical).hexdigest(),
        canonical_json=canonical,
        signing_sha256=hashlib.sha256(signing).hexdigest(),
        signing_json=signing,
        mod=mod,
    )
