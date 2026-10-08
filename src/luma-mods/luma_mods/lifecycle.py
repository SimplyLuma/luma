"""Transactional lifecycle for the first bounded, user-scope Mod backend."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .errors import StateError, TransactionError
from .model import Plan
from .profile import FilePreferenceBackend, PreferenceProfile
from .state import MAX_AUDIT_ENTRIES, StateStore, state_digest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _composition(installed: Mapping[str, Any]) -> str:
    subjects = [
        {
            "id": identifier,
            "version": record["version"],
            "manifest_sha256": record["manifest_sha256"],
            "enabled": record["enabled"],
        }
        for identifier, record in sorted(installed.items())
    ]
    return hashlib.sha256(
        json.dumps(subjects, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class Authorization:
    """Review result supplied by a future policy/UI layer.

    Verified Mods name their verified trust level. Local Mods must be listed in
    ``confirmed_unverified`` after a distinct warning; absence always refuses.
    """

    trust_levels: Mapping[str, str]
    confirmed_unverified: frozenset[str] = frozenset()
    catalog_snapshot_id: str | None = None
    admitted_manifests: Mapping[str, str] = field(default_factory=dict)
    catalog_verified: bool = False

    def level_for(self, identifier: str, manifest_source_sha256: str | None = None) -> str:
        if identifier in self.trust_levels:
            level = self.trust_levels[identifier]
            if level not in {"luma-core", "luma-verified", "community"}:
                raise TransactionError(f"invalid verified trust level for {identifier}")
            if not self.catalog_verified or self.catalog_snapshot_id is None:
                raise TransactionError(
                    f"{identifier} has publisher proof but no live TUF catalog admission"
                )
            if manifest_source_sha256 is None:
                raise TransactionError(f"{identifier} is missing its manifest source identity")
            if self.admitted_manifests.get(identifier) != manifest_source_sha256:
                raise TransactionError(
                    f"{identifier} does not match the manifest admitted by the signed catalog"
                )
            return level
        if identifier in self.confirmed_unverified:
            return "local-unverified"
        raise TransactionError(f"{identifier} has not been authorized for installation")


class PreferenceLifecycle:
    """Install/remove only declarative settings through a registered backend."""

    def __init__(self, state: StateStore, backend: FilePreferenceBackend):
        self.state = state
        self.backend = backend

    @staticmethod
    def _assert_profile_plan(plan: Plan, profile: PreferenceProfile) -> None:
        target = next(
            (item.inspection for item in plan.mods if item.inspection.mod.identity.id == plan.target_id),
            None,
        )
        if target is None:
            raise TransactionError("plan does not contain its target Mod")
        if profile.mod_id != target.mod.identity.id or profile.version != target.mod.identity.version:
            raise TransactionError("profile identity/version does not match the planned target")
        all_settings: set[str] = set()
        for planned in plan.mods:
            mod = planned.inspection.mod
            effects = mod.effects
            if mod.payloads:
                raise TransactionError("preference lifecycle does not accept executable payloads")
            populated = set(effects.populated())
            if populated - {"settings"}:
                raise TransactionError(
                    f"{mod.identity.id} requires effects outside the preference backend"
                )
            all_settings.update(effects.settings)
        if set(profile.values) != set(target.mod.effects.settings):
            raise TransactionError("profile values must exactly match the target's declared settings")
        if set(profile.values) != all_settings:
            raise TransactionError("dependency settings require their own independently reviewed profile")

    def install(
        self,
        plan: Plan,
        profile: PreferenceProfile,
        authorization: Authorization,
        *,
        expected_generation: int,
    ) -> dict[str, Any]:
        self._assert_profile_plan(plan, profile)
        domains = set(profile.values)
        self.backend.ensure_supported(domains)
        transaction_id = str(uuid.uuid4())
        timestamp = _now()

        with self.state.locked() as locked:
            current = locked.value
            if current["pending_transaction"] is not None:
                raise StateError("an interrupted transaction must be recovered first")
            if current["generation"] != expected_generation:
                raise StateError(
                    f"stale Mod state: expected generation {expected_generation}, found {current['generation']}"
                )
            if plan.target_id in current["installed"]:
                raise TransactionError(f"{plan.target_id} is already installed")
            for planned in plan.mods:
                identifier = planned.inspection.mod.identity.id
                if identifier in current["installed"]:
                    raise TransactionError(f"plan unexpectedly includes installed Mod {identifier}")
                authorization.level_for(identifier, planned.inspection.source_sha256)
            target_inspection = next(
                planned.inspection
                for planned in plan.mods
                if planned.inspection.mod.identity.id == plan.target_id
            )
            target_level = authorization.level_for(
                plan.target_id, target_inspection.source_sha256
            )
            if target_level != "local-unverified":
                expected_profile = target_inspection.mod.evidence.get("source_digest")
                if expected_profile != f"sha256:{profile.source_sha256}":
                    raise TransactionError(
                        "verified preference profile bytes do not match the reviewed manifest"
                    )
            snapshot = self.backend.snapshot(domains)
            desired = copy.deepcopy(current)
            for planned in plan.mods:
                inspection = planned.inspection
                mod = inspection.mod
                claims = {
                    field: list(value)
                    for field, value in mod.effects.populated().items()
                    if isinstance(value, tuple)
                }
                desired["installed"][mod.identity.id] = {
                    "version": mod.identity.version,
                    "manifest_sha256": inspection.canonical_sha256,
                    "dependencies": [item.id for item in mod.dependencies],
                    "enabled": True,
                    "trust_level": authorization.level_for(
                        mod.identity.id, inspection.source_sha256
                    ),
                    "installed_at": timestamp,
                    "effect_claims": claims,
                    "restoration": snapshot if mod.identity.id == plan.target_id else {},
                    "activation": copy.deepcopy(profile.values)
                    if mod.identity.id == plan.target_id else {},
                }
                for field, resources in claims.items():
                    owners = desired["effect_owners"].setdefault(field, {})
                    for resource in resources:
                        if resource in owners:
                            raise TransactionError(f"effect {field}:{resource} is already owned")
                        owners[resource] = mod.identity.id
                for dependency in mod.dependencies:
                    refs = desired["dependency_references"].setdefault(dependency.id, [])
                    if mod.identity.id not in refs:
                        refs.append(mod.identity.id)
                        refs.sort()
            desired["composition_sha256"] = _composition(desired["installed"])
            before = copy.deepcopy(current)
            pending = {
                "id": transaction_id,
                "operation": "install",
                "target_id": plan.target_id,
                "phase": "prepared",
                "created_at": timestamp,
                "expected_generation": expected_generation,
                "before_sha256": state_digest(before),
                "desired_sha256": state_digest(desired),
                "before_state": before,
                "backend": "luma-preferences-v1",
                "backend_recovery": snapshot,
            }
            journaled = copy.deepcopy(current)
            journaled["pending_transaction"] = pending
            locked.write(journaled)

        self._set_phase(transaction_id, "backend-applying")
        try:
            self.backend.apply(profile.values)
        except Exception as error:
            self.recover()
            if isinstance(error, TransactionError):
                raise
            raise TransactionError(f"preference backend failed: {error}") from error
        self._set_phase(transaction_id, "backend-applied")
        return self._commit(transaction_id, desired, timestamp)

    def update(
        self,
        plan: Plan,
        profile: PreferenceProfile,
        authorization: Authorization,
        *,
        expected_generation: int,
    ) -> dict[str, Any]:
        """Update a preference Mod without silently broadening its effects.

        The first backend only accepts the exact same dependencies and setting
        claims. A broader update must return to full install review and a later
        backend appropriate to the new effects.
        """

        self._assert_profile_plan(plan, profile)
        transaction_id = str(uuid.uuid4())
        timestamp = _now()
        with self.state.locked() as locked:
            current = locked.value
            if current["pending_transaction"] is not None:
                raise StateError("an interrupted transaction must be recovered first")
            if current["generation"] != expected_generation:
                raise StateError("stale Mod state")
            existing = current["installed"].get(plan.target_id)
            if existing is None:
                raise TransactionError(f"{plan.target_id} is not installed")
            target = next(
                item.inspection for item in plan.mods
                if item.inspection.mod.identity.id == plan.target_id
            )
            new_dependencies = sorted(item.id for item in target.mod.dependencies)
            new_claims = {
                "settings": list(target.mod.effects.settings)
            } if target.mod.effects.settings else {}
            if new_dependencies != sorted(existing["dependencies"]):
                raise TransactionError("update changes dependencies and requires fresh review")
            if new_claims != existing["effect_claims"]:
                raise TransactionError("update broadens or changes effects and requires fresh review")
            trust_level = authorization.level_for(
                plan.target_id, target.source_sha256
            )
            if trust_level != "local-unverified":
                expected_profile = target.mod.evidence.get("source_digest")
                if expected_profile != f"sha256:{profile.source_sha256}":
                    raise TransactionError(
                        "verified preference profile bytes do not match the reviewed manifest"
                    )
            backend_recovery = self.backend.snapshot(set(profile.values))
            desired = copy.deepcopy(current)
            record = desired["installed"][plan.target_id]
            record["version"] = target.mod.identity.version
            record["manifest_sha256"] = target.canonical_sha256
            record["trust_level"] = trust_level
            record["activation"] = copy.deepcopy(profile.values)
            record["enabled"] = True
            desired["composition_sha256"] = _composition(desired["installed"])
            before = copy.deepcopy(current)
            journaled = copy.deepcopy(current)
            journaled["pending_transaction"] = {
                "id": transaction_id, "operation": "update", "target_id": plan.target_id,
                "phase": "prepared", "created_at": timestamp,
                "expected_generation": expected_generation,
                "before_sha256": state_digest(before), "desired_sha256": state_digest(desired),
                "before_state": before, "backend_recovery": backend_recovery,
                "backend": "luma-preferences-v1",
            }
            locked.write(journaled)
        self._set_phase(transaction_id, "backend-applying")
        try:
            self.backend.apply(profile.values)
        except Exception as error:
            self.recover()
            raise TransactionError(f"preference update failed: {error}") from error
        self._set_phase(transaction_id, "backend-applied")
        return self._commit(transaction_id, desired, timestamp)

    def set_enabled(
        self, identifier: str, enabled: bool, *, expected_generation: int
    ) -> dict[str, Any]:
        operation = "enable" if enabled else "disable"
        transaction_id = str(uuid.uuid4())
        timestamp = _now()
        with self.state.locked() as locked:
            current = locked.value
            if current["pending_transaction"] is not None:
                raise StateError("an interrupted transaction must be recovered first")
            if current["generation"] != expected_generation:
                raise StateError("stale Mod state")
            record = current["installed"].get(identifier)
            if record is None:
                raise TransactionError(f"{identifier} is not installed")
            if record["enabled"] is enabled:
                raise TransactionError(f"{identifier} is already {operation}d")
            domains = set(record["effect_claims"].get("settings", []))
            if domains != set(record["activation"]):
                raise TransactionError("installed activation record does not match owned settings")
            backend_recovery = self.backend.snapshot(domains)
            desired = copy.deepcopy(current)
            desired["installed"][identifier]["enabled"] = enabled
            desired["composition_sha256"] = _composition(desired["installed"])
            before = copy.deepcopy(current)
            journaled = copy.deepcopy(current)
            journaled["pending_transaction"] = {
                "id": transaction_id, "operation": operation, "target_id": identifier,
                "phase": "prepared", "created_at": timestamp,
                "expected_generation": expected_generation,
                "before_sha256": state_digest(before), "desired_sha256": state_digest(desired),
                "before_state": before, "backend_recovery": backend_recovery,
                "backend": "luma-preferences-v1",
            }
            locked.write(journaled)
        self._set_phase(transaction_id, "backend-applying")
        try:
            if enabled:
                self.backend.apply(record["activation"])
            else:
                self.backend.restore(record["restoration"])
        except Exception as error:
            self.recover()
            raise TransactionError(f"preference {operation} failed: {error}") from error
        self._set_phase(transaction_id, "backend-applied")
        return self._commit(transaction_id, desired, timestamp)

    def remove(self, identifier: str, *, expected_generation: int) -> dict[str, Any]:
        transaction_id = str(uuid.uuid4())
        timestamp = _now()
        with self.state.locked() as locked:
            current = locked.value
            if current["pending_transaction"] is not None:
                raise StateError("an interrupted transaction must be recovered first")
            if current["generation"] != expected_generation:
                raise StateError("stale Mod state")
            record = current["installed"].get(identifier)
            if record is None:
                raise TransactionError(f"{identifier} is not installed")
            dependents = current["dependency_references"].get(identifier, [])
            if dependents:
                raise TransactionError(
                    f"cannot remove {identifier}; required by {', '.join(dependents)}"
                )
            restoration = record["restoration"]
            if record["effect_claims"].get("settings") and not restoration:
                raise TransactionError("installed preference Mod has no restoration record")
            desired = copy.deepcopy(current)
            desired["installed"].pop(identifier)
            for field, resources in record["effect_claims"].items():
                owners = desired["effect_owners"].get(field, {})
                for resource in resources:
                    if owners.get(resource) != identifier:
                        raise TransactionError(f"effect ownership drift for {field}:{resource}")
                    owners.pop(resource)
                if not owners:
                    desired["effect_owners"].pop(field, None)
            for dependency in record["dependencies"]:
                refs = desired["dependency_references"].get(dependency, [])
                if identifier in refs:
                    refs.remove(identifier)
                if not refs:
                    desired["dependency_references"].pop(dependency, None)
            desired["composition_sha256"] = _composition(desired["installed"])
            before = copy.deepcopy(current)
            pending = {
                "id": transaction_id,
                "operation": "remove",
                "target_id": identifier,
                "phase": "prepared",
                "created_at": timestamp,
                "expected_generation": expected_generation,
                "before_sha256": state_digest(before),
                "desired_sha256": state_digest(desired),
                "before_state": before,
                "backend": "luma-preferences-v1",
                "backend_recovery": self.backend.snapshot(set(restoration)),
            }
            journaled = copy.deepcopy(current)
            journaled["pending_transaction"] = pending
            locked.write(journaled)

        self._set_phase(transaction_id, "backend-applying")
        try:
            self.backend.restore(restoration)
        except Exception as error:
            self.recover()
            if isinstance(error, TransactionError):
                raise
            raise TransactionError(f"preference restoration failed: {error}") from error
        self._set_phase(transaction_id, "backend-applied")
        return self._commit(transaction_id, desired, timestamp)

    def _set_phase(self, transaction_id: str, phase: str) -> None:
        with self.state.locked() as locked:
            pending = locked.value["pending_transaction"]
            if pending is None or pending["id"] != transaction_id:
                raise TransactionError("transaction journal identity changed unexpectedly")
            updated = copy.deepcopy(locked.value)
            updated["pending_transaction"]["phase"] = phase
            locked.write(updated)

    def _commit(self, transaction_id: str, desired: dict[str, Any], timestamp: str) -> dict[str, Any]:
        with self.state.locked() as locked:
            pending = locked.value["pending_transaction"]
            if pending is None or pending["id"] != transaction_id or pending["phase"] != "backend-applied":
                raise TransactionError("transaction is not ready to commit")
            if state_digest(desired) != pending["desired_sha256"]:
                raise TransactionError("desired state changed after review")
            committed = copy.deepcopy(desired)
            committed["generation"] = pending["expected_generation"] + 1
            committed["pending_transaction"] = None
            audit = committed["audit"]
            audit.append({
                "transaction_id": transaction_id,
                "operation": pending["operation"],
                "target_id": pending["target_id"],
                "result": "committed",
                "at": timestamp,
                "from_generation": pending["expected_generation"],
                "to_generation": committed["generation"],
            })
            committed["audit"] = audit[-MAX_AUDIT_ENTRIES:]
            return locked.write(committed)

    def recover(self) -> dict[str, Any]:
        """Restore the pre-transaction backend snapshot and state.

        Recovery is idempotent: if there is no pending transaction, the current
        state is returned unchanged.
        """

        with self.state.locked() as locked:
            pending = copy.deepcopy(locked.value["pending_transaction"])
            if pending is None:
                return copy.deepcopy(locked.value)
        self.backend.restore(pending["backend_recovery"])
        with self.state.locked() as locked:
            current_pending = locked.value["pending_transaction"]
            if current_pending is None:
                return copy.deepcopy(locked.value)
            if current_pending["id"] != pending["id"]:
                raise TransactionError("transaction journal changed during recovery")
            restored = copy.deepcopy(pending["before_state"])
            restored["generation"] = pending["expected_generation"] + 1
            restored["pending_transaction"] = None
            restored["audit"].append({
                "transaction_id": pending["id"],
                "operation": pending["operation"],
                "target_id": pending["target_id"],
                "result": "recovered",
                "at": _now(),
                "from_generation": pending["expected_generation"],
                "to_generation": restored["generation"],
            })
            restored["audit"] = restored["audit"][-MAX_AUDIT_ENTRIES:]
            return locked.write(restored)
