"""Privileged, catalog-rooted admission for system Mod deployments.

Only a Mod identity and the two identities shown during review cross D-Bus.
The privileged process refreshes TUF, reconstructs the real host tuple,
verifies every publisher, resolves the dependency closure, downloads payloads,
and constructs the fixed system request itself.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .catalog_runtime import (
    SYSTEM_CLIENT_CONFIG,
    CatalogClientConfig,
    TrustedCatalogRuntime,
)
from .errors import CatalogUpdateError, TransactionError
from .manifest import ID_PATTERN
from .privileged import SystemCompositionRequest, build_system_request
from .resolver import HostContext
from .system_backend import ArtifactStore
from .system_host import SYSTEM_HOST_CONFIG, SystemHostProfile

SYSTEM_CATALOG_CACHE = Path("/var/lib/luma/mods/catalog")
SNAPSHOT_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
COMPOSITION_ID = re.compile(r"^[0-9a-f]{64}$")


def _require_root_owned_regular(path: Path, label: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise TransactionError(f"cannot open {label} safely: {error.strerror}") from error
    try:
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o022
    ):
        raise TransactionError(
            f"{label} must be a root-owned, non-writable regular file"
        )


@dataclass(frozen=True)
class AdmittedSystemTransaction:
    snapshot_id: str
    request: SystemCompositionRequest


class SystemCatalogAdmission:
    """Recreate system installation authority entirely inside root."""

    def __init__(
        self,
        runtime: TrustedCatalogRuntime,
        host_supplier: Callable[[], HostContext],
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.runtime = runtime
        self.host_supplier = host_supplier
        self.artifact_store = artifact_store or ArtifactStore()

    @classmethod
    def from_system(
        cls,
        *,
        client_config: Path = SYSTEM_CLIENT_CONFIG,
        host_config: Path = SYSTEM_HOST_CONFIG,
        cache_root: Path = SYSTEM_CATALOG_CACHE,
        artifact_store: ArtifactStore | None = None,
    ) -> "SystemCatalogAdmission":
        _require_root_owned_regular(client_config, "system catalog configuration")
        config = CatalogClientConfig.from_file(client_config)
        _require_root_owned_regular(config.bootstrap_root, "bootstrap TUF root")
        _require_root_owned_regular(config.publisher_policy, "publisher policy")
        runtime = config.open(cache_root)
        profile = SystemHostProfile.from_file(host_config)
        return cls(runtime, profile.context, artifact_store)

    @staticmethod
    def _review_identities(
        identifier: str,
        snapshot_id: str,
        composition_sha256: str,
    ) -> None:
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            raise TransactionError("system Mod ID is invalid")
        if not isinstance(snapshot_id, str) or not SNAPSHOT_ID.fullmatch(snapshot_id):
            raise TransactionError("reviewed catalog snapshot identity is invalid")
        if (
            not isinstance(composition_sha256, str)
            or not COMPOSITION_ID.fullmatch(composition_sha256)
        ):
            raise TransactionError("reviewed system composition identity is invalid")

    def prepare(
        self,
        identifier: str,
        *,
        reviewed_snapshot_id: str,
        reviewed_composition_sha256: str,
        recovery_ready: bool,
    ) -> AdmittedSystemTransaction:
        self._review_identities(
            identifier, reviewed_snapshot_id, reviewed_composition_sha256
        )
        self.runtime.refresh()
        trusted = self.runtime.plan(identifier, self.host_supplier())
        if trusted.snapshot_id != reviewed_snapshot_id:
            raise CatalogUpdateError(
                "the trusted catalog changed after review; reopen this Mod before preparing it"
            )
        if trusted.plan.composition_sha256 != reviewed_composition_sha256:
            raise CatalogUpdateError(
                "the system composition changed after review; reopen this Mod before preparing it"
            )

        trust_levels: dict[str, str] = {}
        for planned in trusted.plan.mods:
            mod_id = planned.inspection.mod.identity.id
            verification = trusted.verifications.get(mod_id)
            if verification is None or not verification.verified:
                raise TransactionError(
                    f"system Mod publisher verification is required for {mod_id}"
                )
            role = trusted.trust_roles.get(mod_id)
            if role != verification.level:
                raise TransactionError(
                    f"system Mod trust role changed during admission for {mod_id}"
                )
            trust_levels[mod_id] = verification.level

        request = build_system_request(
            trusted.plan, trust_levels, recovery_ready=recovery_ready
        )
        if not request.artifacts:
            raise TransactionError(
                "system Mod has no immutable deployment artifacts and cannot be prepared"
            )
        unsupported = sorted({item.backend for item in request.artifacts} - {"rpm-set"})
        if unsupported:
            raise TransactionError(
                "the installed system backend does not support artifact backend(s): "
                + ", ".join(unsupported)
            )

        acquired: dict[str, Path] = {}
        for planned in trusted.plan.mods:
            mod_id = planned.inspection.mod.identity.id
            for path in self.runtime.client.acquire_payloads(
                self.runtime.snapshot, mod_id  # type: ignore[arg-type]
            ):
                # TUF target paths are content-addressed. The client already
                # checked length and digest against the manifest; root repeats
                # those checks while admitting the bytes below.
                acquired[path.name] = path
        for artifact in request.artifacts:
            digest = artifact.digest.removeprefix("sha256:")
            source = acquired.get(digest)
            if source is None:
                raise TransactionError(
                    f"trusted catalog did not acquire declared artifact: {artifact.digest}"
                )
            self.artifact_store.admit(artifact, source)

        return AdmittedSystemTransaction(trusted.snapshot_id, request)
