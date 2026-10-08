# SPDX-License-Identifier: MPL-2.0
"""Who may run in the background: the person's decisions over Luma's defaults.

A decision is only ever made by the person -- in Settings, in the dock menu,
in the one-time prompt, or through the portal's own permission tools. The
defaults decide what happens before that, and only for apps the system
itself installed.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import ids

DECISIONS = ("allow", "deny")
DEFAULTS = ("on", "off", "ask")
SOURCES = ("settings", "dock", "prompt", "portal", "app", "cli")
SYSTEM_DEFAULTS = (
    Path("/usr/share/luma-background/defaults.toml"),
    Path("/etc/luma-background/defaults.toml"),
)


@dataclass(frozen=True, slots=True)
class Decision:
    value: str = ""
    source: str = ""
    changed_at: int = 0
    prompted: bool = False


@dataclass(frozen=True, slots=True)
class Effective:
    allowed: bool
    decision: str       # allow | deny | unset
    default: str        # on | off | ask
    essential: bool
    prompted: bool


def load_defaults(paths: tuple[Path, ...] = SYSTEM_DEFAULTS) -> dict[str, str]:
    """Later files override earlier ones, so /etc wins over /usr."""

    defaults: dict[str, str] = {}
    for path in paths:
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        except FileNotFoundError:
            continue
        except (OSError, tomllib.TOMLDecodeError):
            continue
        apps = data.get("apps", {})
        if not isinstance(apps, dict):
            continue
        for app, value in apps.items():
            if ids.is_app_id(app) and value in DEFAULTS:
                defaults[app] = value
    return defaults


class PolicyStore:
    """The person's decisions, one JSON file, written atomically."""

    VERSION = 1

    def __init__(self, path: Path, defaults: dict[str, str] | None = None) -> None:
        self.path = path
        self.defaults = dict(defaults or {})
        self._apps: dict[str, Decision] = {}
        self.load()

    def load(self) -> None:
        self._apps = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, json.JSONDecodeError):
            # A damaged file must not silently turn everything on or off.
            # Keep it for inspection and start from the defaults.
            try:
                self.path.rename(self.path.with_suffix(f".damaged-{int(time.time())}"))
            except OSError:
                pass
            return
        apps = data.get("apps", {}) if isinstance(data, dict) else {}
        for app, entry in apps.items() if isinstance(apps, dict) else ():
            if not ids.is_app_id(app) or not isinstance(entry, dict):
                continue
            value = entry.get("decision", "")
            self._apps[app] = Decision(
                value=value if value in DECISIONS else "",
                source=entry.get("source", "") if entry.get("source") in SOURCES else "",
                changed_at=int(entry.get("changed_at", 0) or 0),
                prompted=bool(entry.get("prompted", False)),
            )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "version": self.VERSION,
            "apps": {
                app: {
                    "decision": decision.value,
                    "source": decision.source,
                    "changed_at": decision.changed_at,
                    "prompted": decision.prompted,
                }
                for app, decision in sorted(self._apps.items())
            },
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".policy-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def decision(self, app: str) -> Decision:
        return self._apps.get(app, Decision())

    def set_decision(self, app: str, allowed: bool, source: str) -> bool:
        """Record a decision. Returns whether anything changed."""

        ids.app_id(app)
        if source not in SOURCES:
            raise ValueError(f"unknown decision source: {source}")
        value = "allow" if allowed else "deny"
        current = self.decision(app)
        if current.value == value:
            return False
        self._apps[app] = Decision(value=value, source=source,
                                   changed_at=int(time.time()), prompted=True)
        self.save()
        return True

    def mark_prompted(self, app: str) -> None:
        current = self.decision(app)
        if current.prompted:
            return
        self._apps[app] = Decision(current.value, current.source, current.changed_at, True)
        self.save()

    def default_for(self, app: str, *, trusted_install: bool) -> str:
        """An app installed by the person can never be on by default."""
        value = self.defaults.get(app, "ask")
        if value == "on" and not trusted_install:
            return "ask"
        return value

    def effective(self, app: str, *, trusted_install: bool) -> Effective:
        decision = self.decision(app)
        default = self.default_for(app, trusted_install=trusted_install)
        if decision.value == "allow":
            allowed = True
        elif decision.value == "deny":
            allowed = False
        else:
            allowed = default == "on"
        return Effective(
            allowed=allowed,
            decision=decision.value or "unset",
            default=default,
            essential=default == "on",
            prompted=decision.prompted,
        )

    def known_apps(self) -> list[str]:
        return sorted(self._apps)
