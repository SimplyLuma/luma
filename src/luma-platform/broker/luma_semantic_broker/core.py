# SPDX-License-Identifier: Apache-2.0
"""Stateful broker core kept independent from D-Bus and GTK adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable
import os
import uuid

from .model import Grant, Identity, LivePublication, Scope, Surface
from .policy import BrokerPolicy, Decision, RateLimiter
from .store import AuditStore, GrantStore
from .validation import (
    ValidationError,
    application_id,
    object_path,
    scopes as validate_scopes,
    semantic_surface,
    live_extension,
    stable_id,
)


MAX_GRANT_LIFETIME = timedelta(days=30)
MAX_SURFACES = 128
MAX_SURFACES_PER_APPLICATION = 16
MAX_LIVE_EXTENSIONS = 16
MAX_LIVE_EXTENSIONS_PER_APPLICATION = 4
MAX_LIVE_EXTENSION_LIFETIME = timedelta(days=7)


class BrokerError(RuntimeError):
    pass


class BrokerCore:
    def __init__(
        self,
        *,
        grant_store: GrantStore,
        audit_store: AuditStore,
        locked: Callable[[], bool],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        limiter: RateLimiter | None = None,
    ) -> None:
        self.clock = clock
        self.policy = BrokerPolicy(clock=clock)
        self.grant_store = grant_store
        self.audit_store = audit_store
        self.locked = locked
        self.limiter = limiter or RateLimiter()
        self.surfaces: dict[str, Surface] = {}
        self._surface_keys: dict[tuple[str, str], str] = {}
        self.live_extensions: dict[str, LivePublication] = {}
        self._live_extension_keys: dict[tuple[str, str], str] = {}
        now = self.clock()
        for grant in self.grant_store.load():
            if grant.expires_at is None or grant.expires_at > now + MAX_GRANT_LIFETIME:
                raise BrokerError("persisted semantic grant has an invalid lifetime")
            if grant.active(now):
                self.policy.grant(grant)

    def _rate(self, identity: Identity) -> None:
        if not self.limiter.allow(identity.key):
            raise BrokerError("semantic broker rate limit exceeded")

    def _flush_audit(self) -> None:
        events, self.policy.audit = self.policy.audit, []
        for event in events:
            self.audit_store.append(event)

    def _save_grants(self) -> None:
        persistent = (
            grant
            for grant in self.policy.grants.values()
            if grant.active(self.clock()) and grant.persistent
        )
        self.grant_store.save(sorted(persistent, key=lambda item: (item.client_key, item.application_id)))

    def register_surface(
        self,
        provider: Identity,
        application: str,
        surface_id: str,
        payload: dict,
        provider_path: str,
    ) -> Surface:
        self._rate(provider)
        app_id = application_id(application)
        if provider.app_id != app_id:
            raise BrokerError("provider is not authorized for the application identifier")
        stable_id(surface_id, "surface identifier")
        object_path(provider_path)
        normalized = semantic_surface(payload)
        if normalized["id"] != surface_id:
            raise ValidationError("surface identifier does not match semantic root")
        key = (app_id, surface_id)
        previous = self._surface_keys.get(key)
        if previous is not None:
            existing = self.surfaces[previous]
            if existing.owner_sender != provider.sender:
                raise BrokerError("semantic surface is already owned by another connection")
            surface = Surface(
                existing.publication_id,
                provider.sender,
                app_id,
                surface_id,
                provider_path,
                normalized,
            )
        else:
            if len(self.surfaces) >= MAX_SURFACES:
                raise BrokerError("semantic broker publication limit reached")
            application_surfaces = sum(
                item.application_id == app_id for item in self.surfaces.values()
            )
            if application_surfaces >= MAX_SURFACES_PER_APPLICATION:
                raise BrokerError("semantic application publication limit reached")
            surface = Surface(
                uuid.uuid4().hex,
                provider.sender,
                app_id,
                surface_id,
                provider_path,
                normalized,
            )
        self.surfaces[surface.publication_id] = surface
        self._surface_keys[key] = surface.publication_id
        return surface

    def update_surface(self, provider: Identity, publication_id: str, payload: dict) -> Surface:
        self._rate(provider)
        publication = stable_id(publication_id, "publication identifier")
        surface = self.surfaces.get(publication)
        if surface is None:
            raise BrokerError("unknown semantic publication")
        if surface.owner_sender != provider.sender:
            raise BrokerError("semantic publication belongs to another connection")
        normalized = semantic_surface(payload)
        if normalized["id"] != surface.surface_id:
            raise ValidationError("updated semantic root changed its identifier")
        updated = Surface(
            surface.publication_id,
            surface.owner_sender,
            surface.application_id,
            surface.surface_id,
            surface.provider_path,
            normalized,
        )
        self.surfaces[publication] = updated
        return updated

    def unregister_surface(self, provider: Identity, publication_id: str) -> None:
        self._rate(provider)
        publication = stable_id(publication_id, "publication identifier")
        surface = self.surfaces.get(publication)
        if surface is None:
            return
        if surface.owner_sender != provider.sender:
            raise BrokerError("semantic publication belongs to another connection")
        self.surfaces.pop(publication)
        self._surface_keys.pop((surface.application_id, surface.surface_id), None)

    def disconnect(self, sender: str) -> None:
        publications = [
            item.publication_id
            for item in self.surfaces.values()
            if item.owner_sender == sender
        ]
        for publication in publications:
            surface = self.surfaces.pop(publication)
            self._surface_keys.pop((surface.application_id, surface.surface_id), None)
        live_publications = [
            item.publication_id
            for item in self.live_extensions.values()
            if item.owner_sender == sender
        ]
        for publication in live_publications:
            extension = self.live_extensions.pop(publication)
            self._live_extension_keys.pop(
                (extension.application_id, extension.extension_id), None
            )
        self.limiter.forget(f"transient:{os.getuid()}:{sender}")

    def _normalize_live_extension(self, payload: dict, app_id: str, extension_id: str) -> dict:
        normalized = live_extension(payload)
        if normalized["app_id"] != app_id or normalized["id"] != extension_id:
            raise ValidationError("Live Extension identity does not match its registration")
        expires = datetime.fromisoformat(normalized["expires_at"].replace("Z", "+00:00"))
        now = self.clock()
        if expires <= now or expires > now + MAX_LIVE_EXTENSION_LIFETIME:
            raise ValidationError("Live Extension expiry is outside the supported range")
        return normalized

    def register_live_extension(
        self, provider: Identity, application: str, extension_id: str,
        payload: dict, provider_path: str,
    ) -> LivePublication:
        self._rate(provider)
        app_id = application_id(application)
        if provider.app_id != app_id:
            raise BrokerError("provider is not authorized for the application identifier")
        stable_id(extension_id, "Live Extension identifier")
        object_path(provider_path)
        normalized = self._normalize_live_extension(payload, app_id, extension_id)
        key = (app_id, extension_id)
        previous = self._live_extension_keys.get(key)
        if previous is not None:
            existing = self.live_extensions[previous]
            if existing.owner_sender != provider.sender:
                raise BrokerError("Live Extension is already owned by another connection")
            extension = LivePublication(
                existing.publication_id, provider.sender, app_id, extension_id,
                provider_path, normalized,
            )
        else:
            if len(self.live_extensions) >= MAX_LIVE_EXTENSIONS:
                raise BrokerError("Live Extension publication limit reached")
            per_app = sum(item.application_id == app_id for item in self.live_extensions.values())
            if per_app >= MAX_LIVE_EXTENSIONS_PER_APPLICATION:
                raise BrokerError("application Live Extension limit reached")
            extension = LivePublication(
                uuid.uuid4().hex, provider.sender, app_id, extension_id,
                provider_path, normalized,
            )
        self.live_extensions[extension.publication_id] = extension
        self._live_extension_keys[key] = extension.publication_id
        return extension

    def update_live_extension(self, provider: Identity, publication_id: str, payload: dict) -> LivePublication:
        self._rate(provider)
        publication = stable_id(publication_id, "publication identifier")
        extension = self.live_extensions.get(publication)
        if extension is None:
            raise BrokerError("unknown Live Extension publication")
        if extension.owner_sender != provider.sender:
            raise BrokerError("Live Extension belongs to another connection")
        normalized = self._normalize_live_extension(
            payload, extension.application_id, extension.extension_id
        )
        updated = LivePublication(
            extension.publication_id, extension.owner_sender,
            extension.application_id, extension.extension_id,
            extension.provider_path, normalized,
        )
        self.live_extensions[publication] = updated
        return updated

    def unregister_live_extension(self, provider: Identity, publication_id: str) -> None:
        self._rate(provider)
        publication = stable_id(publication_id, "publication identifier")
        extension = self.live_extensions.get(publication)
        if extension is None:
            return
        if extension.owner_sender != provider.sender:
            raise BrokerError("Live Extension belongs to another connection")
        self.live_extensions.pop(publication)
        self._live_extension_keys.pop((extension.application_id, extension.extension_id), None)

    def list_live_extensions(self) -> tuple[dict, ...]:
        now = self.clock()
        locked = self.locked()
        result = []
        expired = []
        for publication, extension in self.live_extensions.items():
            expires = datetime.fromisoformat(
                extension.payload["expires_at"].replace("Z", "+00:00")
            )
            if expires <= now:
                expired.append(publication)
                continue
            payload = dict(extension.payload)
            if locked and payload["privacy"] != "public":
                payload["title"] = "Ongoing activity"
                payload.pop("subtitle", None)
                payload["actions"] = []
            result.append({
                "publication_id": publication,
                "application_id": extension.application_id,
                "extension": payload,
            })
        for publication in expired:
            extension = self.live_extensions.pop(publication)
            self._live_extension_keys.pop((extension.application_id, extension.extension_id), None)
        return tuple(result)

    def authorize_live_invocation(
        self,
        shell: Identity,
        publication_id: str,
        object_id: str,
        action_id: str,
        *,
        confirmed: bool,
    ) -> tuple[Decision, LivePublication, dict]:
        """Authorize an action the system Shell renders for a Live Extension.

        Live Extensions live in their own namespace: this never consults
        ``self.surfaces`` and :meth:`authorize_invocation` never consults
        ``self.live_extensions``, so neither kind of publication identifier can
        satisfy a lookup for the other.  The action must be one the
        publication itself declared, and a publisher that shipped it
        ``enabled: false`` is refused here, in the broker, whatever the caller
        asks for.
        """

        self._rate(shell)
        publication = stable_id(publication_id, "publication identifier")
        stable_id(object_id, "semantic object identifier")
        stable_id(action_id, "action identifier")
        extension = self.live_extensions.get(publication)
        if extension is None:
            raise BrokerError("unknown Live Extension publication")
        payload = extension.payload
        if payload["id"] != object_id:
            raise BrokerError("unknown semantic object")
        action = self._find_action(payload, action_id)
        if action is None:
            raise BrokerError("unknown semantic action")
        decision = self.policy.authorize_live_action(
            shell.key,
            action,
            locked=self.locked(),
            confirmed=confirmed,
        )
        self._flush_audit()
        return decision, extension, action

    def grant_access(
        self,
        client: Identity,
        target_application: str,
        requested_scopes,
        *,
        lifetime_seconds: int,
        persistent: bool,
    ) -> Grant:
        self._rate(client)
        target = application_id(target_application)
        granted_scopes = validate_scopes(requested_scopes)
        if not granted_scopes:
            raise ValidationError("at least one semantic scope is required")
        if isinstance(lifetime_seconds, bool) or not isinstance(lifetime_seconds, int):
            raise ValidationError("grant lifetime must be an integer")
        if lifetime_seconds < 60 or lifetime_seconds > int(MAX_GRANT_LIFETIME.total_seconds()):
            raise ValidationError("grant lifetime is outside the supported range")
        if persistent and not client.persistent:
            raise BrokerError("native clients may receive session access only")
        grant = Grant(
            client.key,
            target,
            granted_scopes,
            self.clock() + timedelta(seconds=lifetime_seconds),
            persistent,
        )
        self.policy.grant(grant)
        if persistent:
            self._save_grants()
        return grant

    def revoke_access(self, client: Identity, target_application: str) -> None:
        self._rate(client)
        target = application_id(target_application)
        self.policy.revoke(client.key, target)
        self._save_grants()
        self.audit_decision(client, "access-revoke", target, True, "revoked")

    def audit_decision(
        self,
        client: Identity,
        operation: str,
        target: str,
        allowed: bool,
        reason: str,
    ) -> None:
        self.policy.record(
            client.key,
            operation,
            target,
            allowed=allowed,
            reason=reason,
        )
        self._flush_audit()

    def list_surfaces(self, client: Identity) -> tuple[dict, ...]:
        self._rate(client)
        visible = []
        for surface in self.surfaces.values():
            decision, payload = self.policy.observe(
                client.key,
                surface.application_id,
                surface.payload,
                locked=self.locked(),
            )
            if decision.allowed and payload is not None:
                visible.append(
                    {
                        "publication_id": surface.publication_id,
                        "application_id": surface.application_id,
                        "surface_id": surface.surface_id,
                        "surface": payload,
                    }
                )
        self._flush_audit()
        return tuple(visible)

    def get_surface(self, client: Identity, publication_id: str) -> dict:
        self._rate(client)
        publication = stable_id(publication_id, "publication identifier")
        surface = self.surfaces.get(publication)
        if surface is None:
            raise BrokerError("unknown semantic publication")
        decision, payload = self.policy.observe(
            client.key,
            surface.application_id,
            surface.payload,
            locked=self.locked(),
        )
        self._flush_audit()
        if not decision.allowed or payload is None:
            raise BrokerError(decision.reason)
        return {
            "publication_id": surface.publication_id,
            "application_id": surface.application_id,
            "surface_id": surface.surface_id,
            "surface": payload,
        }

    @staticmethod
    def _find_object(node: dict, object_id: str) -> dict | None:
        if node["id"] == object_id:
            return node
        for child in node.get("children", ()):
            found = BrokerCore._find_object(child, object_id)
            if found is not None:
                return found
        return None

    @staticmethod
    def _find_action(node: dict, action_id: str) -> dict | None:
        for action in node["actions"]:
            if action["id"] == action_id:
                return action
        return None

    def authorize_invocation(
        self,
        client: Identity,
        publication_id: str,
        object_id: str,
        action_id: str,
        *,
        confirmed: bool,
    ) -> tuple[Decision, Surface, dict]:
        self._rate(client)
        publication = stable_id(publication_id, "publication identifier")
        stable_id(object_id, "semantic object identifier")
        stable_id(action_id, "action identifier")
        surface = self.surfaces.get(publication)
        if surface is None:
            raise BrokerError("unknown semantic publication")
        target_object = self._find_object(surface.payload, object_id)
        if target_object is None:
            raise BrokerError("unknown semantic object")
        action = self._find_action(target_object, action_id)
        if action is None:
            raise BrokerError("unknown semantic action")
        decision = self.policy.authorize_action(
            client.key,
            surface.application_id,
            action,
            object_privacy=target_object["privacy"],
            locked=self.locked(),
            confirmed=confirmed,
        )
        self._flush_audit()
        return decision, surface, action
