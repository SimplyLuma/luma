# SPDX-License-Identifier: Apache-2.0
"""Bounded validation for data crossing the Semantic Broker bus boundary."""

from __future__ import annotations

import json
import re
from typing import Any

from .model import Scope


APP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){2,}$")
STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
ACTION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+$")
OBJECT_PATH = re.compile(r"^/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+$")
PRIVACY = frozenset({"public", "private", "sensitive"})
RISK = frozenset({"passive", "low", "consequential", "destructive", "security-sensitive"})
MAX_SURFACE_BYTES = 256 * 1024
MAX_DEPTH = 16
MAX_NODES = 512
MAX_ACTIONS_PER_NODE = 32
MAX_TEXT = 4096
MAX_LIVE_TITLE_BYTES = 256
MAX_LIVE_SUBTITLE_BYTES = 512
CATEGORIES = frozenset({
    "call", "media", "timer", "navigation", "event", "transfer",
    "recording", "installation", "generic",
})
PARAMETER_TYPES = frozenset({"b", "d", "s", "x", "a{sv}"})
BIDI_CONTROLS = frozenset(
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)


class ValidationError(ValueError):
    pass


def application_id(value: Any) -> str:
    if not isinstance(value, str) or not APP_ID.fullmatch(value):
        raise ValidationError("invalid application identifier")
    return value


def stable_id(value: Any, field: str = "identifier") -> str:
    if not isinstance(value, str) or not STABLE_ID.fullmatch(value):
        raise ValidationError(f"invalid {field}")
    return value


def object_path(value: Any) -> str:
    if not isinstance(value, str) or not OBJECT_PATH.fullmatch(value):
        raise ValidationError("invalid provider object path")
    return value


def scopes(values: Any) -> frozenset[Scope]:
    if not isinstance(values, (list, tuple)) or len(values) > len(Scope):
        raise ValidationError("invalid semantic scope list")
    if any(not isinstance(item, str) for item in values):
        raise ValidationError("semantic scopes must be strings")
    try:
        normalized = frozenset(Scope(item) for item in values)
    except ValueError as error:
        raise ValidationError("unknown semantic scope") from error
    if len(normalized) != len(values):
        raise ValidationError("duplicate semantic scope")
    return normalized


def _text(value: Any, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_TEXT:
        raise ValidationError(f"invalid semantic {field}")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or character in BIDI_CONTROLS
        for character in value
    ):
        raise ValidationError(f"semantic {field} contains unsafe control characters")
    if not empty and not value.strip():
        raise ValidationError(f"semantic {field} must not be empty")
    return value


def _action(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("semantic action must be a dictionary")
    allowed = {"id", "label", "risk", "enabled", "description", "parameter_type"}
    if not {"id", "label", "risk", "enabled"}.issubset(value) or set(value) - allowed:
        raise ValidationError("semantic action has invalid fields")
    identifier = value["id"]
    if not isinstance(identifier, str) or not ACTION_ID.fullmatch(identifier):
        raise ValidationError("invalid semantic action identifier")
    risk = value["risk"]
    if risk not in RISK:
        raise ValidationError("invalid semantic action risk")
    if not isinstance(value["enabled"], bool):
        raise ValidationError("semantic action enabled state must be boolean")
    result = {
        "id": identifier,
        "label": _text(value["label"], "action label"),
        "risk": risk,
        "enabled": value["enabled"],
    }
    if "description" in value:
        result["description"] = _text(value["description"], "action description", empty=True)
    if "parameter_type" in value:
        parameter_type = _text(value["parameter_type"], "parameter type")
        if parameter_type not in PARAMETER_TYPES:
            raise ValidationError("unsupported semantic action parameter type")
        result["parameter_type"] = parameter_type
    return result


def semantic_surface(value: Any) -> dict[str, Any]:
    """Return a normalized bounded tree; secret nodes never cross this boundary."""

    if not isinstance(value, dict):
        raise ValidationError("semantic surface must be a dictionary")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValidationError("semantic surface contains unsupported values") from error
    if len(encoded) > MAX_SURFACE_BYTES:
        raise ValidationError("semantic surface exceeds the size limit")

    count = 0

    def normalize(node: Any, depth: int) -> dict[str, Any]:
        nonlocal count
        if depth > MAX_DEPTH:
            raise ValidationError("semantic surface exceeds the depth limit")
        count += 1
        if count > MAX_NODES:
            raise ValidationError("semantic surface exceeds the object limit")
        if not isinstance(node, dict):
            raise ValidationError("semantic object must be a dictionary")
        expected = {"schema_version", "id", "kind", "name", "privacy", "actions", "children"}
        if set(node) != expected or node["schema_version"] != "0.1":
            raise ValidationError("semantic object does not match contract 0.1")
        privacy = node["privacy"]
        if privacy == "secret":
            raise ValidationError("secret semantic objects must not be serialized")
        if privacy not in PRIVACY:
            raise ValidationError("invalid semantic privacy class")
        raw_actions = node["actions"]
        raw_children = node["children"]
        if not isinstance(raw_actions, list) or len(raw_actions) > MAX_ACTIONS_PER_NODE:
            raise ValidationError("semantic object has too many actions")
        if not isinstance(raw_children, list):
            raise ValidationError("semantic children must be a list")
        normalized_actions = [_action(item) for item in raw_actions]
        action_ids = [item["id"] for item in normalized_actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValidationError("semantic object has duplicate actions")
        return {
            "schema_version": "0.1",
            "id": stable_id(node["id"], "semantic object identifier"),
            "kind": stable_id(node["kind"], "semantic object kind"),
            "name": _text(node["name"], "object name"),
            "privacy": privacy,
            "actions": normalized_actions,
            "children": [normalize(child, depth + 1) for child in raw_children],
        }

    return normalize(value, 0)


def live_extension(value: Any) -> dict[str, Any]:
    """Validate the compact, system-rendered Live Extension wire contract."""

    if not isinstance(value, dict):
        raise ValidationError("Live Extension must be a dictionary")
    required = {
        "schema_version", "id", "app_id", "category", "title", "privacy",
        "progress", "actions", "expires_at",
    }
    if not required.issubset(value) or set(value) - (required | {"subtitle", "starts_at"}):
        raise ValidationError("Live Extension has invalid fields")
    if value["schema_version"] != "0.1":
        raise ValidationError("Live Extension does not match contract 0.1")
    category = value["category"]
    if category not in CATEGORIES:
        raise ValidationError("invalid Live Extension category")
    privacy = value["privacy"]
    if privacy not in PRIVACY:
        raise ValidationError("invalid Live Extension privacy class")
    progress = value["progress"]
    if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not -1 <= progress <= 1:
        raise ValidationError("invalid Live Extension progress")
    raw_actions = value["actions"]
    if not isinstance(raw_actions, list) or len(raw_actions) > 3:
        raise ValidationError("a Live Extension may expose at most three actions")
    normalized_actions = [_action(item) for item in raw_actions]
    if len({item["id"] for item in normalized_actions}) != len(normalized_actions):
        raise ValidationError("Live Extension has duplicate actions")
    title = _text(value["title"], "Live Extension title")
    if len(title.encode("utf-8")) > MAX_LIVE_TITLE_BYTES:
        raise ValidationError("Live Extension title exceeds the size limit")
    result = {
        "schema_version": "0.1",
        "id": stable_id(value["id"], "Live Extension identifier"),
        "app_id": application_id(value["app_id"]),
        "category": category,
        "title": title,
        "privacy": privacy,
        "progress": float(progress),
        "actions": normalized_actions,
        "expires_at": _text(value["expires_at"], "Live Extension expiry"),
    }
    if "subtitle" in value:
        subtitle = _text(value["subtitle"], "Live Extension subtitle", empty=True)
        if len(subtitle.encode("utf-8")) > MAX_LIVE_SUBTITLE_BYTES:
            raise ValidationError("Live Extension subtitle exceeds the size limit")
        result["subtitle"] = subtitle
    if "starts_at" in value:
        result["starts_at"] = _text(value["starts_at"], "Live Extension start", empty=True)
    try:
        from datetime import datetime
        for field in ("expires_at", "starts_at"):
            if field not in result:
                continue
            parsed = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
    except ValueError as error:
        raise ValidationError("Live Extension timestamps must be ISO 8601 with timezone") from error
    return result
