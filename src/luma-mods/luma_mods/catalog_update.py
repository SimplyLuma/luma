"""TUF-backed catalog refresh and verified target acquisition.

Cryptographic metadata verification, rollback protection, expiry, delegation,
thresholds, and target length/hash checks are delegated to python-tuf's
``ngclient.Updater``. Luma adds a strict catalog index and manifest contract.
"""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .errors import CatalogUpdateError, LumaModsError
from .manifest import ID_PATTERN, VERSION_PATTERN, inspect_manifest
from .model import Inspection, Plan
from .profile import MAX_PROFILE_BYTES, PreferenceProfile
from .resolver import Catalog, HostContext, resolve_mod
from .trust import TrustPolicy, VerificationResult, verify_inspection

CATALOG_SCHEMA = "org.luma.mod-catalog/v0.1"
MAX_ROOT_BYTES = 1024 * 1024
MAX_INDEX_BYTES = 4 * 1024 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 20_000
TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$")
CONTENT_TARGET_PATTERN = re.compile(
    r"^(evidence|payloads)/(luma-core|luma-verified|community)/sha256/([0-9a-f]{64})$"
)
UpdaterFactory = Callable[..., Any]


def _safe_target(value: Any, where: str) -> str:
    if not isinstance(value, str) or not TARGET_PATTERN.fullmatch(value):
        raise CatalogUpdateError(f"{where} is not a valid catalog target path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or "\\" in value:
        raise CatalogUpdateError(f"{where} must be a normalized relative target path")
    return value


def _read_regular(path: Path, limit: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CatalogUpdateError(f"cannot open {label} safely: {error.strerror}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise CatalogUpdateError(f"{label} must be a bounded regular file")
        payload = os.read(descriptor, limit + 1)
        if len(payload) > limit:
            raise CatalogUpdateError(f"{label} exceeds its size limit")
        return payload
    finally:
        os.close(descriptor)


def _regular_identity(path: Path, limit: int, label: str) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CatalogUpdateError(f"cannot open {label} safely: {error.strerror}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise CatalogUpdateError(f"{label} must be a bounded regular file")
        digest = hashlib.sha256()
        length = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            length += len(chunk)
            if length > limit:
                raise CatalogUpdateError(f"{label} exceeds its size limit")
            digest.update(chunk)
        return length, digest.hexdigest()
    finally:
        os.close(descriptor)


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CatalogUpdateError(f"catalog cache {path} must be a real directory")
    os.chmod(path, 0o700)


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    version: str
    manifest: str
    signature_bundles: tuple[str, ...]
    evidence: tuple[str, ...]
    payloads: tuple[str, ...]
    trust_role: str


@dataclass(frozen=True)
class CatalogSnapshot:
    snapshot_id: str
    entries: Mapping[str, CatalogEntry]
    index_path: Path
    _tuf_verified: bool = False
    _client_token: object | None = None


@dataclass(frozen=True)
class TrustedCatalogPlan:
    """A plan resolved from one live, TUF-verified catalog refresh.

    This object carries in-process authority only. It deliberately has no
    ``from_dict`` path: serialized plans remain review evidence and must be
    admitted again against current TUF metadata before installation.
    """

    snapshot_id: str
    plan: Plan
    verifications: Mapping[str, VerificationResult]
    trust_roles: Mapping[str, str]
    admitted_manifests: Mapping[str, str]
    _tuf_verified: bool = False

    def authorization(self, *, confirmed_unverified: bool = False):
        from .lifecycle import Authorization

        if not self._tuf_verified:
            raise CatalogUpdateError(
                "serialized or reconstructed catalog plans cannot authorize installation"
            )
        levels: dict[str, str] = {}
        unverified: set[str] = set()
        for planned in self.plan.mods:
            identifier = planned.inspection.mod.identity.id
            verification = self.verifications.get(identifier)
            if verification is None:
                raise CatalogUpdateError(f"catalog plan lacks verification for {identifier}")
            if verification.verified:
                levels[identifier] = verification.level
            else:
                unverified.add(identifier)
        if unverified and not confirmed_unverified:
            raise CatalogUpdateError(
                "Local / Unverified confirmation required for: "
                + ", ".join(sorted(unverified))
            )
        return Authorization(
            trust_levels=levels,
            confirmed_unverified=frozenset(unverified),
            catalog_snapshot_id=self.snapshot_id,
            admitted_manifests=dict(self.admitted_manifests),
            catalog_verified=True,
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogUpdateError(f"catalog index contains duplicate field: {key}")
        result[key] = value
    return result


def _reject_numeric_constant(value: str) -> None:
    raise CatalogUpdateError(
        f"catalog index contains unsupported numeric constant: {value}"
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _entry_targets(entry: CatalogEntry) -> tuple[str, ...]:
    return (
        entry.manifest,
        *entry.signature_bundles,
        *entry.evidence,
        *entry.payloads,
    )


def _validate_role_paths(entry: CatalogEntry) -> None:
    mod_prefix = f"mods/{entry.trust_role}/{entry.id}/{entry.version}/"
    if entry.manifest != f"{mod_prefix}manifest.mod.json":
        raise CatalogUpdateError(
            f"catalog manifest path is outside the exact {entry.trust_role} Mod namespace"
        )
    if any(not target.startswith(mod_prefix) for target in entry.signature_bundles):
        raise CatalogUpdateError(
            f"catalog signature target is outside the {entry.trust_role} Mod namespace"
        )
    if any(
        not (match := CONTENT_TARGET_PATTERN.fullmatch(target))
        or match.group(1) != "evidence"
        or match.group(2) != entry.trust_role
        for target in entry.evidence
    ):
        raise CatalogUpdateError(
            f"catalog evidence target is outside the {entry.trust_role} namespace"
        )
    if any(
        not (match := CONTENT_TARGET_PATTERN.fullmatch(target))
        or match.group(1) != "payloads"
        or match.group(2) != entry.trust_role
        for target in entry.payloads
    ):
        raise CatalogUpdateError(
            f"catalog payload target is outside the {entry.trust_role} namespace"
        )


def _parse_index(path: Path) -> CatalogSnapshot:
    payload = _read_regular(path, MAX_INDEX_BYTES, "catalog index")
    try:
        root = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_numeric_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogUpdateError(f"catalog index is invalid UTF-8 JSON: {error}") from error
    if not isinstance(root, dict) or set(root) != {"schema", "snapshot_id", "entries"}:
        raise CatalogUpdateError("catalog index has an invalid document shape")
    if root["schema"] != CATALOG_SCHEMA:
        raise CatalogUpdateError(f"unsupported catalog schema: {root['schema']!r}")
    snapshot_id = root["snapshot_id"]
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_id):
        raise CatalogUpdateError("catalog snapshot_id must be a SHA-256 identity")
    raw_entries = root["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) > MAX_ENTRIES:
        raise CatalogUpdateError(f"catalog entries must be a list of at most {MAX_ENTRIES}")
    entries: dict[str, CatalogEntry] = {}
    claimed_targets: set[str] = set()
    for index, raw in enumerate(raw_entries):
        where = f"entries[{index}]"
        fields = {"id", "version", "manifest", "signature_bundles", "evidence", "payloads", "trust_role"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise CatalogUpdateError(f"{where} has an invalid record shape")
        identifier = raw["id"]
        version = raw["version"]
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            raise CatalogUpdateError(f"{where}.id is invalid")
        if identifier in entries:
            raise CatalogUpdateError(f"catalog contains duplicate Mod {identifier}")
        if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
            raise CatalogUpdateError(f"{where}.version is invalid")
        lists: dict[str, tuple[str, ...]] = {}
        for name in ("signature_bundles", "evidence", "payloads"):
            value = raw[name]
            if not isinstance(value, list) or len(value) > 128:
                raise CatalogUpdateError(f"{where}.{name} must be a bounded list")
            targets = tuple(_safe_target(item, f"{where}.{name}[]") for item in value)
            if len(targets) != len(set(targets)):
                raise CatalogUpdateError(f"{where}.{name} contains duplicate targets")
            lists[name] = targets
        trust_role = raw["trust_role"]
        if trust_role not in {"luma-core", "luma-verified", "community"}:
            raise CatalogUpdateError(f"{where}.trust_role is invalid")
        entry = CatalogEntry(
            id=identifier,
            version=version,
            manifest=_safe_target(raw["manifest"], f"{where}.manifest"),
            signature_bundles=lists["signature_bundles"],
            evidence=lists["evidence"],
            payloads=lists["payloads"],
            trust_role=trust_role,
        )
        _validate_role_paths(entry)
        for target in _entry_targets(entry):
            if target in claimed_targets:
                raise CatalogUpdateError(
                    f"catalog target is claimed by more than one entry: {target}"
                )
            claimed_targets.add(target)
        entries[identifier] = entry
    calculated_snapshot = "sha256:" + hashlib.sha256(_canonical(raw_entries)).hexdigest()
    if snapshot_id != calculated_snapshot:
        raise CatalogUpdateError(
            "catalog snapshot_id does not match the content-addressed entry set"
        )
    return CatalogSnapshot(snapshot_id, entries, path)


class TufCatalogClient:
    """Small policy wrapper around python-tuf's supported updater API."""

    def __init__(
        self,
        *,
        bootstrap_root: Path,
        cache_root: Path,
        metadata_base_url: str,
        target_base_url: str,
        updater_factory: UpdaterFactory | None = None,
    ):
        if not metadata_base_url.startswith("https://") or not target_base_url.startswith("https://"):
            raise CatalogUpdateError("production catalog URLs must use HTTPS")
        self.bootstrap = _read_regular(bootstrap_root, MAX_ROOT_BYTES, "bootstrap TUF root")
        self.metadata_dir = cache_root / "metadata"
        self.targets_dir = cache_root / "targets"
        _ensure_private_directory(cache_root)
        _ensure_private_directory(self.metadata_dir)
        _ensure_private_directory(self.targets_dir)
        if updater_factory is None:
            try:
                version = importlib.metadata.version("tuf")
                major = int(version.split(".", 1)[0])
                if major < 7:
                    raise CatalogUpdateError(
                        f"python-tuf {version} is below Luma's security floor (7.x)"
                    )
                from tuf.ngclient import Updater
            except importlib.metadata.PackageNotFoundError as error:
                raise CatalogUpdateError(
                    "python-tuf 7.x is required for trusted catalog refresh"
                ) from error
            except (ImportError, ValueError) as error:
                raise CatalogUpdateError(f"cannot load python-tuf safely: {error}") from error
            updater_factory = Updater
        try:
            self.updater = updater_factory(
                metadata_dir=str(self.metadata_dir),
                metadata_base_url=metadata_base_url,
                target_dir=str(self.targets_dir),
                target_base_url=target_base_url,
                bootstrap=self.bootstrap,
            )
        except Exception as error:
            raise CatalogUpdateError(f"cannot initialize trusted catalog: {error}") from error
        self._snapshot_token = object()

    def _require_live_snapshot(self, snapshot: CatalogSnapshot) -> None:
        if not snapshot._tuf_verified or snapshot._client_token is not self._snapshot_token:
            raise CatalogUpdateError(
                "catalog operation requires a snapshot returned by this client's live TUF refresh"
            )

    def _download(self, target_name: str, *, limit: int) -> Path:
        target_name = _safe_target(target_name, "target name")
        try:
            info = self.updater.get_targetinfo(target_name)
            if info is None:
                raise CatalogUpdateError(f"trusted catalog does not contain {target_name}")
            length = getattr(info, "length", None)
            if not isinstance(length, int) or length < 0 or length > limit:
                raise CatalogUpdateError(f"trusted target {target_name} exceeds its size policy")
            downloaded = Path(self.updater.download_target(info))
        except CatalogUpdateError:
            raise
        except Exception as error:
            raise CatalogUpdateError(f"cannot acquire trusted target {target_name}: {error}") from error
        resolved_root = self.targets_dir.resolve()
        try:
            resolved = downloaded.resolve(strict=True)
        except OSError as error:
            raise CatalogUpdateError(f"downloaded target {target_name} is unavailable: {error}") from error
        if resolved_root not in resolved.parents:
            raise CatalogUpdateError("python-tuf returned a target outside the private cache")
        _read_regular(resolved, limit, f"trusted target {target_name}")
        return resolved

    def refresh(self, index_target: str = "catalog/index.json") -> CatalogSnapshot:
        try:
            self.updater.refresh()
        except Exception as error:
            raise CatalogUpdateError(f"trusted catalog metadata refresh failed: {error}") from error
        parsed = _parse_index(self._download(index_target, limit=MAX_INDEX_BYTES))
        return CatalogSnapshot(
            parsed.snapshot_id,
            parsed.entries,
            parsed.index_path,
            _tuf_verified=True,
            _client_token=self._snapshot_token,
        )

    def inspect_manifest_entry(
        self, snapshot: CatalogSnapshot, identifier: str
    ) -> Inspection:
        """Acquire and validate the manifest without fetching larger proof files."""

        self._require_live_snapshot(snapshot)
        try:
            entry = snapshot.entries[identifier]
        except KeyError as error:
            raise CatalogUpdateError(f"catalog snapshot has no Mod {identifier}") from error
        inspection = inspect_manifest(self._download(entry.manifest, limit=1024 * 1024))
        if inspection.mod.identity.id != entry.id or inspection.mod.identity.version != entry.version:
            raise CatalogUpdateError(
                f"catalog entry identity does not match manifest for {identifier}"
            )
        manifest_bundles = {signature.bundle for signature in inspection.mod.signatures}
        if manifest_bundles != {PurePosixPath(item).name for item in entry.signature_bundles}:
            raise CatalogUpdateError(
                f"catalog signature targets do not match manifest for {identifier}"
            )
        declared_payloads = {
            str(payload["digest"]).removeprefix("sha256:")
            for payload in inspection.mod.payloads
        }
        catalog_payloads = {
            PurePosixPath(item).name for item in entry.payloads
        }
        if declared_payloads != catalog_payloads:
            raise CatalogUpdateError(
                f"catalog payload targets do not match manifest for {identifier}"
            )
        return inspection

    def inspect_entry(self, snapshot: CatalogSnapshot, identifier: str) -> Inspection:
        """Validate a manifest and acquire every publisher/evidence proof target."""

        inspection = self.inspect_manifest_entry(snapshot, identifier)
        entry = snapshot.entries[identifier]
        for target in entry.signature_bundles:
            self._download(target, limit=MAX_SIGNATURE_BYTES)
        for target in entry.evidence:
            self._download(target, limit=MAX_EVIDENCE_BYTES)
        return inspection

    def acquire_preference_profile(
        self, snapshot: CatalogSnapshot, identifier: str
    ) -> PreferenceProfile:
        """Acquire the exact reviewed preference profile through TUF evidence."""

        inspection = self.inspect_manifest_entry(snapshot, identifier)
        if not inspection.mod.effects.settings:
            raise CatalogUpdateError(f"catalog Mod {identifier} has no preference effects")
        source_digest = inspection.mod.evidence.get("source_digest")
        if not isinstance(source_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", source_digest
        ):
            raise CatalogUpdateError(
                f"catalog Mod {identifier} has no content-addressed profile evidence"
            )
        digest = source_digest.removeprefix("sha256:")
        entry = snapshot.entries[identifier]
        matches = [target for target in entry.evidence if PurePosixPath(target).name == digest]
        if len(matches) != 1:
            raise CatalogUpdateError(
                f"catalog Mod {identifier} must admit exactly one matching preference profile"
            )
        path = self._download(matches[0], limit=MAX_PROFILE_BYTES)
        try:
            profile = PreferenceProfile.from_file(path)
        except LumaModsError as error:
            raise CatalogUpdateError(
                f"catalog preference profile for {identifier} is invalid: {error}"
            ) from error
        if profile.mod_id != identifier or profile.version != inspection.mod.identity.version:
            raise CatalogUpdateError(
                f"catalog preference profile identity differs from {identifier}"
            )
        if profile.source_sha256 != digest:
            raise CatalogUpdateError(
                f"catalog preference profile digest differs from {identifier} manifest evidence"
            )
        return profile

    def resolve_plan(
        self,
        snapshot: CatalogSnapshot,
        identifier: str,
        host: HostContext,
        policy: TrustPolicy | None,
        *,
        sigstore_executable: str = "sigstore",
        runner: Callable[..., Any] = subprocess.run,
    ) -> TrustedCatalogPlan:
        """Resolve and verify one dependency closure from the live TUF snapshot."""

        self._require_live_snapshot(snapshot)
        pending = [identifier]
        inspections: dict[str, Inspection] = {}
        while pending:
            current = pending.pop()
            if current in inspections:
                continue
            inspection = self.inspect_entry(snapshot, current)
            inspections[current] = inspection
            pending.extend(
                dependency.id
                for dependency in inspection.mod.dependencies
                if dependency.id not in inspections
            )

        plan = resolve_mod(identifier, Catalog(inspections.values()), host)
        verifications: dict[str, VerificationResult] = {}
        roles: dict[str, str] = {}
        admitted: dict[str, str] = {}
        for planned in plan.mods:
            inspection = planned.inspection
            mod_id = inspection.mod.identity.id
            entry = snapshot.entries[mod_id]
            verification = verify_inspection(
                inspection,
                policy,
                sigstore_executable=sigstore_executable,
                runner=runner,
            )
            if verification.verified and verification.level != entry.trust_role:
                raise CatalogUpdateError(
                    f"publisher policy and TUF delegation disagree for {mod_id}"
                )
            if not verification.verified and entry.trust_role != "community":
                raise CatalogUpdateError(
                    f"protected catalog role {entry.trust_role} requires verified publisher proof"
                )
            verifications[mod_id] = verification
            roles[mod_id] = entry.trust_role
            admitted[mod_id] = inspection.source_sha256
        return TrustedCatalogPlan(
            snapshot_id=snapshot.snapshot_id,
            plan=plan,
            verifications=verifications,
            trust_roles=roles,
            admitted_manifests=admitted,
            _tuf_verified=True,
        )

    def acquire_payloads(self, snapshot: CatalogSnapshot, identifier: str) -> tuple[Path, ...]:
        """Download TUF-verified payloads only after a separately reviewed plan."""

        self._require_live_snapshot(snapshot)
        try:
            entry = snapshot.entries[identifier]
        except KeyError as error:
            raise CatalogUpdateError(f"catalog snapshot has no Mod {identifier}") from error
        inspection = self.inspect_entry(snapshot, identifier)
        declarations = {
            str(payload["digest"]).removeprefix("sha256:"): int(payload["size"])
            for payload in inspection.mod.payloads
        }
        acquired: list[Path] = []
        for target in entry.payloads:
            path = self._download(target, limit=8 * 1024 * 1024 * 1024)
            expected_digest = PurePosixPath(target).name
            expected_size = declarations[expected_digest]
            length, digest = _regular_identity(
                path, expected_size, f"catalog payload for {identifier}"
            )
            if length != expected_size or digest != expected_digest:
                raise CatalogUpdateError(
                    f"catalog payload identity differs from manifest for {identifier}"
                )
            acquired.append(path)
        return tuple(acquired)

    def acquire_target(self, target_name: str, *, maximum_bytes: int) -> Path:
        """Acquire one TUF-verified target for release acceptance tooling."""

        if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes < 0:
            raise CatalogUpdateError("trusted target size policy is invalid")
        return self._download(target_name, limit=maximum_bytes)
