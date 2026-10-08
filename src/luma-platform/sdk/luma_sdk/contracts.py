# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Risk(StrEnum):
    PASSIVE = "passive"
    LOW = "low"
    CONSEQUENTIAL = "consequential"
    DESTRUCTIVE = "destructive"
    SECURITY_SENSITIVE = "security-sensitive"


class Privacy(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class LiveCategory(StrEnum):
    CALL = "call"
    MEDIA = "media"
    TIMER = "timer"
    NAVIGATION = "navigation"
    EVENT = "event"
    TRANSFER = "transfer"
    RECORDING = "recording"
    INSTALLATION = "installation"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    label: str
    risk: Risk = Risk.LOW
    enabled: bool = True
    description: str = ""
    parameter_type: str = ""

    def __post_init__(self) -> None:
        if "." not in self.id or not self.id.isascii():
            raise ValueError("action id must be a stable dotted ASCII identifier")
        if not self.label.strip():
            raise ValueError("action label must not be empty")

    @property
    def requires_confirmation(self) -> bool:
        return self.risk in {Risk.CONSEQUENTIAL, Risk.DESTRUCTIVE, Risk.SECURITY_SENSITIVE}

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "risk": self.risk.value,
            "enabled": self.enabled,
        }
        if self.description:
            value["description"] = self.description
        if self.parameter_type:
            value["parameter_type"] = self.parameter_type
        return value


@dataclass(slots=True)
class SemanticObject:
    id: str
    kind: str
    name: str
    privacy: Privacy = Privacy.PRIVATE
    actions: list[Action] = field(default_factory=list)
    children: list["SemanticObject"] = field(default_factory=list)

    def add_action(self, action: Action) -> None:
        if any(existing.id == action.id for existing in self.actions):
            raise ValueError(f"duplicate semantic action: {action.id}")
        self.actions.append(action)

    def add_child(self, child: "SemanticObject") -> None:
        if child is self or child.id == self.id:
            raise ValueError("a semantic object cannot contain itself")
        self.children.append(child)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1",
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "privacy": self.privacy.value,
            "actions": [action.to_dict() for action in self.actions],
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(slots=True)
class LiveExtension:
    id: str
    app_id: str
    category: LiveCategory
    title: str
    privacy: Privacy = Privacy.PRIVATE
    subtitle: str = ""
    progress: float = -1.0
    expires_at: datetime | None = None
    starts_at: datetime | None = None
    actions: list[Action] = field(default_factory=list)

    def add_action(self, action: Action) -> None:
        if len(self.actions) >= 3:
            raise ValueError("a Live Extension may expose at most three actions")
        if any(existing.id == action.id for existing in self.actions):
            raise ValueError(f"duplicate Live Extension action: {action.id}")
        self.actions.append(action)

    def to_dict(self) -> dict[str, Any]:
        if not -1.0 <= self.progress <= 1.0:
            raise ValueError("progress must be -1 (hidden) or between 0 and 1")
        if self.expires_at is None or self.expires_at.tzinfo is None:
            raise ValueError("a Live Extension requires a timezone-aware expiry")
        if self.starts_at is not None and self.starts_at.tzinfo is None:
            raise ValueError("Live Extension start time must include a timezone")
        value: dict[str, Any] = {
            "schema_version": "0.1",
            "id": self.id,
            "app_id": self.app_id,
            "category": self.category.value,
            "title": self.title,
            "privacy": self.privacy.value,
            "progress": self.progress,
            "actions": [action.to_dict() for action in self.actions],
            "expires_at": self.expires_at.isoformat(),
        }
        if self.subtitle:
            value["subtitle"] = self.subtitle
        if self.starts_at is not None:
            value["starts_at"] = self.starts_at.isoformat()
        return value
