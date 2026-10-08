"""Production-shaped trusted catalog wiring for graphical and CLI clients.

The release configuration is immutable system input.  Per-user state contains
only python-tuf's rollback-protected metadata/target cache.  A review never
becomes durable install authority: the exact dependency closure and preference
profile are refreshed and admitted again immediately before the transaction.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .catalog_update import CatalogSnapshot, TrustedCatalogPlan, TufCatalogClient
from .errors import CatalogUpdateError
from .profile import PreferenceProfile
from .resolver import Catalog, HostContext
from .trust import TrustPolicy

CONFIG_SCHEMA = "org.luma.mod-catalog-client/v0.1"
SYSTEM_CLIENT_CONFIG = Path("/usr/share/luma/mods/trust/catalog-client.json")
MAX_CONFIG_BYTES = 64 * 1024


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise CatalogUpdateError(
                f"catalog client configuration contains duplicate field: {key}"
            )
        result[key] = value
    return result


def _reject_numeric_constant(value: str) -> None:
    raise CatalogUpdateError(
        f"catalog client configuration contains unsupported numeric constant: {value}"
    )


def _read_config(path: Path) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CatalogUpdateError(
            f"cannot open catalog client configuration safely: {error.strerror}"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_CONFIG_BYTES:
            raise CatalogUpdateError(
                "catalog client configuration must be a bounded regular file"
            )
        payload = os.read(descriptor, MAX_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_CONFIG_BYTES:
        raise CatalogUpdateError("catalog client configuration exceeds its size limit")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_numeric_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogUpdateError(
            f"catalog client configuration is invalid UTF-8 JSON: {error}"
        ) from error
    if not isinstance(value, dict):
        raise CatalogUpdateError("catalog client configuration must be an object")
    return value


def _absolute_file(value: object, where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CatalogUpdateError(f"{where} must be an absolute file path")
    path = Path(value)
    if not path.is_absolute():
        raise CatalogUpdateError(f"{where} must be an absolute file path")
    return path


def _https_repository(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise CatalogUpdateError(f"{where} must be an HTTPS repository URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise CatalogUpdateError(f"{where} must be a valid HTTPS repository URL") from error
    if (
        parsed.scheme != "https"
        or not hostname
        or port is not None and not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/")
    ):
        raise CatalogUpdateError(
            f"{where} must be an HTTPS base URL without credentials, query, or fragment"
        )
    return value


@dataclass(frozen=True)
class CatalogClientConfig:
    bootstrap_root: Path
    publisher_policy: Path
    metadata_url: str
    targets_url: str

    @classmethod
    def from_file(cls, path: Path) -> "CatalogClientConfig":
        value = _read_config(path)
        expected = {
            "schema", "bootstrap_root", "publisher_policy",
            "metadata_url", "targets_url",
        }
        if set(value) != expected or value.get("schema") != CONFIG_SCHEMA:
            raise CatalogUpdateError("catalog client configuration has an invalid shape")
        return cls(
            bootstrap_root=_absolute_file(value["bootstrap_root"], "bootstrap_root"),
            publisher_policy=_absolute_file(value["publisher_policy"], "publisher_policy"),
            metadata_url=_https_repository(value["metadata_url"], "metadata_url"),
            targets_url=_https_repository(value["targets_url"], "targets_url"),
        )

    def open(self, cache_root: Path) -> "TrustedCatalogRuntime":
        policy = TrustPolicy.from_file(self.publisher_policy)
        client = TufCatalogClient(
            bootstrap_root=self.bootstrap_root,
            cache_root=cache_root,
            metadata_base_url=self.metadata_url,
            target_base_url=self.targets_url,
        )
        return TrustedCatalogRuntime(client, policy)


@dataclass(frozen=True)
class TrustedPreferenceTransaction:
    trusted: TrustedCatalogPlan
    profile: PreferenceProfile


class TrustedCatalogRuntime:
    """One process's current trusted catalog view and transaction authority."""

    def __init__(self, client: TufCatalogClient, policy: TrustPolicy | None):
        self.client = client
        self.policy = policy
        self.snapshot: CatalogSnapshot | None = None
        self.catalog: Catalog | None = None

    def refresh(self) -> Catalog:
        snapshot = self.client.refresh()
        inspections = [
            self.client.inspect_manifest_entry(snapshot, identifier)
            for identifier in sorted(snapshot.entries)
        ]
        self.snapshot = snapshot
        self.catalog = Catalog(inspections)
        return self.catalog

    def plan(self, identifier: str, host: HostContext) -> TrustedCatalogPlan:
        snapshot = self.snapshot
        if snapshot is None:
            self.refresh()
            snapshot = self.snapshot
        assert snapshot is not None
        return self.client.resolve_plan(snapshot, identifier, host, self.policy)

    def profile(self, identifier: str) -> PreferenceProfile:
        snapshot = self.snapshot
        if snapshot is None:
            raise CatalogUpdateError("trusted catalog must be refreshed before profile lookup")
        return self.client.acquire_preference_profile(snapshot, identifier)

    def prepare_transaction(
        self,
        identifier: str,
        host: HostContext,
        *,
        reviewed_composition_sha256: str | None = None,
        reviewed_profile_sha256: str | None = None,
    ) -> TrustedPreferenceTransaction:
        """Refresh and recreate authority immediately before a preference write."""

        self.refresh()
        trusted = self.plan(identifier, host)
        profile = self.profile(identifier)
        if (
            reviewed_composition_sha256 is not None
            and trusted.plan.composition_sha256 != reviewed_composition_sha256
        ):
            raise CatalogUpdateError(
                "the trusted catalog changed after review; reopen this Mod to review it again"
            )
        if (
            reviewed_profile_sha256 is not None
            and profile.source_sha256 != reviewed_profile_sha256
        ):
            raise CatalogUpdateError(
                "the trusted preference profile changed after review; reopen this Mod"
            )
        return TrustedPreferenceTransaction(trusted, profile)


def user_catalog_cache() -> Path:
    raw = os.environ.get("XDG_CACHE_HOME")
    root = Path(raw) if raw else Path.home() / ".cache"
    if not root.is_absolute():
        raise CatalogUpdateError("XDG_CACHE_HOME must be an absolute path")
    return root / "luma" / "mods" / "catalog"
