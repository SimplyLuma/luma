# SPDX-License-Identifier: Apache-2.0
"""Small immutable records shared by the Semantic Broker layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Scope(StrEnum):
    OBSERVE_PUBLIC = "observe-public"
    OBSERVE_PRIVATE = "observe-private"
    INVOKE_LOW_RISK = "invoke-low-risk"
    INVOKE_CONSEQUENTIAL = "invoke-consequential"
    INVOKE_DESTRUCTIVE = "invoke-destructive"
    INVOKE_SECURITY_SENSITIVE = "invoke-security-sensitive"


class IdentityStrength(StrEnum):
    SANDBOXED = "sandboxed"
    MANAGED_NATIVE = "managed-native"
    TRANSIENT_NATIVE = "transient-native"


@dataclass(frozen=True, slots=True)
class Identity:
    key: str
    app_id: str
    label: str
    sender: str
    uid: int
    pid: int
    strength: IdentityStrength

    @property
    def persistent(self) -> bool:
        return self.strength is IdentityStrength.SANDBOXED


@dataclass(frozen=True, slots=True)
class Grant:
    client_key: str
    application_id: str
    scopes: frozenset[Scope]
    expires_at: datetime | None = None
    persistent: bool = False

    def active(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return self.expires_at is None or current < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_key": self.client_key,
            "application_id": self.application_id,
            "scopes": sorted(scope.value for scope in self.scopes),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "persistent": self.persistent,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Grant":
        expected = {
            "client_key",
            "application_id",
            "scopes",
            "expires_at",
            "persistent",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("invalid persisted semantic grant")
        raw_expiration = value["expires_at"]
        expiration = None
        if raw_expiration is not None:
            if not isinstance(raw_expiration, str):
                raise ValueError("invalid semantic grant expiration")
            expiration = datetime.fromisoformat(raw_expiration)
            if expiration.tzinfo is None:
                raise ValueError("semantic grant expiration must include a timezone")
        raw_scopes = value["scopes"]
        if not isinstance(raw_scopes, list) or any(
            not isinstance(item, str) for item in raw_scopes
        ):
            raise ValueError("invalid persisted semantic scopes")
        if not raw_scopes or len(raw_scopes) != len(set(raw_scopes)):
            raise ValueError("invalid persisted semantic scopes")
        if not isinstance(value["persistent"], bool) or not value["persistent"]:
            raise ValueError("persisted semantic grant must be marked persistent")
        client_key = value["client_key"]
        application_id = value["application_id"]
        if not isinstance(client_key, str) or not client_key.startswith(
            ("flatpak:", "native:")
        ):
            raise ValueError("invalid persisted semantic client identity")
        if not isinstance(application_id, str):
            raise ValueError("invalid persisted semantic application identity")
        if expiration is None:
            raise ValueError("persisted semantic grant must expire")
        return cls(
            client_key=client_key,
            application_id=application_id,
            scopes=frozenset(Scope(item) for item in raw_scopes),
            expires_at=expiration,
            persistent=True,
        )


@dataclass(frozen=True, slots=True)
class Surface:
    publication_id: str
    owner_sender: str
    application_id: str
    surface_id: str
    provider_path: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LivePublication:
    publication_id: str
    owner_sender: str
    application_id: str
    extension_id: str
    provider_path: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    timestamp: datetime
    client_key: str
    operation: str
    target: str
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.astimezone(UTC).isoformat(),
            "client_key": self.client_key,
            "operation": self.operation,
            "target": self.target,
            "allowed": self.allowed,
            "reason": self.reason,
        }
