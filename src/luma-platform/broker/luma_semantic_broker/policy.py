# SPDX-License-Identifier: Apache-2.0
"""Fail-closed authorization and redaction policy for the Semantic Broker."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Callable

from .model import AuditEvent, Grant, Scope


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: str
    confirmation_required: bool = False


class RateLimiter:
    def __init__(self, limit: int = 120, window_seconds: float = 60.0) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("invalid rate-limit configuration")
        self.limit = limit
        self.window_seconds = window_seconds
        self._calls: dict[str, deque[float]] = {}

    def allow(self, identity_key: str, now: float | None = None) -> bool:
        current = monotonic() if now is None else now
        calls = self._calls.setdefault(identity_key, deque())
        cutoff = current - self.window_seconds
        while calls and calls[0] <= cutoff:
            calls.popleft()
        if len(calls) >= self.limit:
            return False
        calls.append(current)
        return True

    def forget(self, identity_key: str) -> None:
        self._calls.pop(identity_key, None)


@dataclass(slots=True)
class BrokerPolicy:
    grants: dict[tuple[str, str], Grant] = field(default_factory=dict)
    audit: list[AuditEvent] = field(default_factory=list)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def grant(self, grant: Grant) -> None:
        self.grants[(grant.client_key, grant.application_id)] = grant

    def revoke(self, client_key: str, application_id: str) -> None:
        self.grants.pop((client_key, application_id), None)

    def _record(
        self,
        client_key: str,
        operation: str,
        target: str,
        decision: Decision,
    ) -> Decision:
        self.audit.append(
            AuditEvent(
                self.clock(),
                client_key,
                operation,
                target,
                decision.allowed,
                decision.reason,
            )
        )
        return decision

    def record(
        self,
        client_key: str,
        operation: str,
        target: str,
        *,
        allowed: bool,
        reason: str,
    ) -> Decision:
        return self._record(
            client_key,
            operation,
            target,
            Decision(allowed, reason),
        )

    def _grant(self, client_key: str, application_id: str) -> Grant | None:
        grant = self.grants.get((client_key, application_id))
        return grant if grant is not None and grant.active(self.clock()) else None

    def observe(
        self,
        client_key: str,
        application_id: str,
        root: dict,
        *,
        locked: bool,
    ) -> tuple[Decision, dict | None]:
        if locked:
            return self._record(
                client_key, "observe", root["id"], Decision(False, "session is locked")
            ), None
        if root.get("privacy") == "secret":
            return self._record(
                client_key,
                "observe",
                root.get("id", "secret"),
                Decision(False, "secret objects are never exported"),
            ), None
        grant = self._grant(client_key, application_id)
        if grant is None:
            return self._record(
                client_key, "observe", root["id"], Decision(False, "no active grant")
            ), None
        required = (
            Scope.OBSERVE_PUBLIC
            if root["privacy"] == "public"
            else Scope.OBSERVE_PRIVATE
        )
        if required not in grant.scopes:
            return self._record(
                client_key,
                "observe",
                root["id"],
                Decision(False, f"missing {required.value}"),
            ), None
        visible = self._visible_object(root, grant)
        return self._record(
            client_key, "observe", root["id"], Decision(True, "granted")
        ), visible

    @staticmethod
    def _visible_object(node: dict, grant: Grant) -> dict:
        children = []
        for child in node["children"]:
            if child["privacy"] == "secret":
                continue
            if (
                child["privacy"] != "public"
                and Scope.OBSERVE_PRIVATE not in grant.scopes
            ):
                continue
            children.append(BrokerPolicy._visible_object(child, grant))
        return {**node, "children": children}

    def authorize_live_action(
        self,
        client_key: str,
        action: dict,
        *,
        locked: bool,
        confirmed: bool,
    ) -> Decision:
        """Authorize the system Shell invoking a control it renders itself.

        Live Extensions are already readable only by the authenticated system
        Shell, and the controls it draws in the live island belong to the
        publication it is drawing. No observation grant mediates that, so the
        checks that still carry meaning are the ones kept here: the session
        must be unlocked, the publisher must have marked the action enabled,
        and a risky action still needs an explicit confirmation.
        """

        action_id = str(action.get("id", "unknown"))
        if locked:
            return self._record(
                client_key, "invoke-live", action_id, Decision(False, "session is locked")
            )
        if not action.get("enabled", False):
            return self._record(
                client_key,
                "invoke-live",
                action_id,
                Decision(False, "the publisher disabled this action"),
            )
        risk = action.get("risk")
        if risk not in {
            "passive",
            "low",
            "consequential",
            "destructive",
            "security-sensitive",
        }:
            return self._record(
                client_key,
                "invoke-live",
                action_id,
                Decision(False, "missing valid risk scope"),
            )
        if risk in {"consequential", "destructive", "security-sensitive"} and not confirmed:
            return self._record(
                client_key,
                "invoke-live",
                action_id,
                Decision(False, f"{risk} confirmation required", True),
            )
        return self._record(
            client_key, "invoke-live", action_id, Decision(True, "granted")
        )

    def authorize_action(
        self,
        client_key: str,
        application_id: str,
        action: dict,
        *,
        object_privacy: str = "public",
        locked: bool,
        confirmed: bool,
    ) -> Decision:
        action_id = str(action.get("id", "unknown"))
        if locked:
            return self._record(
                client_key, "invoke", action_id, Decision(False, "session is locked")
            )
        grant = self._grant(client_key, application_id)
        if grant is None or not action.get("enabled", False):
            return self._record(
                client_key,
                "invoke",
                action_id,
                Decision(False, "inactive grant or action"),
            )
        observe_scope = (
            Scope.OBSERVE_PUBLIC
            if object_privacy == "public"
            else Scope.OBSERVE_PRIVATE
        )
        if observe_scope not in grant.scopes:
            return self._record(
                client_key,
                "invoke",
                action_id,
                Decision(False, f"missing {observe_scope.value}"),
            )
        risk = action.get("risk")
        required = {
            "passive": Scope.INVOKE_LOW_RISK,
            "low": Scope.INVOKE_LOW_RISK,
            "consequential": Scope.INVOKE_CONSEQUENTIAL,
            "destructive": Scope.INVOKE_DESTRUCTIVE,
            "security-sensitive": Scope.INVOKE_SECURITY_SENSITIVE,
        }.get(risk)
        if required is None or required not in grant.scopes:
            return self._record(
                client_key,
                "invoke",
                action_id,
                Decision(False, f"missing {required.value if required else 'valid risk scope'}"),
            )
        needs_confirmation = risk in {
            "consequential",
            "destructive",
            "security-sensitive",
        }
        if needs_confirmation and not confirmed:
            return self._record(
                client_key,
                "invoke",
                action_id,
                Decision(False, f"{risk} confirmation required", True),
            )
        return self._record(
            client_key, "invoke", action_id, Decision(True, "granted")
        )
