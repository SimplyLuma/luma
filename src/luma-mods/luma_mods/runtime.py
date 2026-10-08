"""Shared unprivileged runtime wiring for the CLI and graphical Mods surfaces."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import LumaModsError
from .lifecycle import Authorization, PreferenceLifecycle
from .model import Plan
from .paths import UserPaths
from .profile import FilePreferenceBackend, PreferenceProfile
from .registry import SUPPORTED_PREFERENCE_DOMAINS
from .resolver import Catalog, HostContext
from .state import StateStore
from .trust import TrustPolicy, VerificationResult, verify_inspection

SYSTEM_CATALOG_ROOT = Path('/app/share/luma/mods/catalog') if Path('/.flatpak-info').is_file() else Path('/usr/share/luma/mods/catalog')


@dataclass(frozen=True)
class UserRuntime:
    paths: UserPaths
    store: StateStore
    lifecycle: PreferenceLifecycle

    @classmethod
    def current(cls) -> "UserRuntime":
        if Path('/.flatpak-info').is_file():
            from .profile_client import Client
            return Client()
        paths = UserPaths.current()
        store = StateStore(paths.state_root)
        return cls(
            paths=paths,
            store=store,
            lifecycle=PreferenceLifecycle(
                store,
                FilePreferenceBackend(
                    paths.preference_file,
                    set(SUPPORTED_PREFERENCE_DOMAINS),
                ),
            ),
        )

    def host_context(self, presentation: str | None = None) -> HostContext:
        state = self.store.read()
        selected = presentation or os.environ.get("LUMA_PRESENTATION_MODE", "desktop")
        if selected == "fullscreen-mobile":
            selected = "handheld"
        if selected not in {"desktop", "tablet", "handheld"}:
            selected = "desktop"
        base = HostContext.minimal(presentation=selected)
        return HostContext(
            luma_base=os.environ.get("LUMA_BASE_VERSION", "0.1.0"),
            architecture=base.architecture,
            presentation=selected,
            kernel_release=base.kernel_release,
            install_mode="running-system",
            hardware=(),
            capabilities={"org.luma.apps.core": "1.0.0"},
            capability_providers={},
            installed_mods={
                identifier: record["version"]
                for identifier, record in state["installed"].items()
            },
            effect_owners=state["effect_owners"],
        )


def catalog_root(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get("LUMA_MOD_CATALOG")
    return Path(override) if override else SYSTEM_CATALOG_ROOT


def load_catalog(path: Path | None = None) -> tuple[Path, Catalog]:
    root = catalog_root(path)
    manifests = root / "manifests" if (root / "manifests").is_dir() else root
    return root, Catalog.from_directory(manifests)


def profile_for(root: Path, mod_id: str) -> PreferenceProfile:
    path = root / "profiles" / f"{mod_id}.profile.json"
    if not path.is_file():
        raise LumaModsError(f"this Mod has no supported local profile: {mod_id}")
    return PreferenceProfile.from_file(path)


def verify_plan(
    plan: Plan, policy: TrustPolicy | None
) -> dict[str, VerificationResult]:
    return {
        planned.inspection.mod.identity.id: verify_inspection(
            planned.inspection, policy
        )
        for planned in plan.mods
    }


def authorization_for_plan(
    plan: Plan,
    policy: TrustPolicy | None,
    *,
    confirmed_unverified: bool,
) -> Authorization:
    levels: dict[str, str] = {}
    unverified: set[str] = set()
    for planned in plan.mods:
        verification = verify_inspection(planned.inspection, policy)
        identifier = planned.inspection.mod.identity.id
        if verification.verified:
            levels[identifier] = verification.level
        else:
            unverified.add(identifier)
    if unverified and not confirmed_unverified:
        raise LumaModsError(
            "Local / Unverified confirmation required for: "
            + ", ".join(sorted(unverified))
        )
    return Authorization(
        trust_levels=levels,
        confirmed_unverified=frozenset(unverified),
    )
