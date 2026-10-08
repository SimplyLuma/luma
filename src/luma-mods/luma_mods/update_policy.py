"""Conservative automatic-update envelope for Luma Mods.

Automatic means "no new review" rather than "less verification".  This
module compares the previously reviewed manifest with one candidate from the
same verified catalog snapshot.  Anything that is not provably inside the old
envelope is held for review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import Inspection
from .trust import VerificationResult

TRUST_ORDER = {
    "local-unverified": 0,
    "community": 1,
    "luma-verified": 2,
    "luma-core": 3,
}


@dataclass(frozen=True)
class UpdateDecision:
    automatic: bool
    disposition: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "automatic": self.automatic,
            "disposition": self.disposition,
            "reasons": list(self.reasons),
        }


def _release_version(value: str) -> tuple[int, int, int] | None:
    """Return a comparable stable SemVer core; prereleases require review."""

    if "-" in value or "+" in value:
        return None
    try:
        parts = tuple(int(item) for item in value.split("."))
    except ValueError:
        return None
    return parts if len(parts) == 3 else None


def _payload_envelope(inspection: Inspection) -> tuple[tuple[str, str, str], ...]:
    """Payload bytes may change; backend, architecture and role may not."""

    return tuple(
        sorted(
            (str(item["backend"]), str(item["architecture"]), str(item["role"]))
            for item in inspection.mod.payloads
        )
    )


def evaluate_update(
    previous: Inspection,
    candidate: Inspection,
    previous_verification: VerificationResult,
    candidate_verification: VerificationResult,
) -> UpdateDecision:
    """Classify one candidate without mutating, downloading, or authorizing it."""

    old = previous.mod
    new = candidate.mod
    reasons: list[str] = []

    old_version = _release_version(old.identity.version)
    new_version = _release_version(new.identity.version)
    if old.identity.id != new.identity.id:
        reasons.append("The Mod identity changed.")
    if old_version is None or new_version is None:
        reasons.append("Prerelease or build-qualified versions require review.")
    elif new_version <= old_version:
        reasons.append("The candidate is not a newer stable version.")

    if not previous_verification.verified or not candidate_verification.verified:
        reasons.append("Automatic updates require verified previous and candidate releases.")
    elif previous_verification.publisher_id != candidate_verification.publisher_id:
        reasons.append("The verified publisher identity changed.")
    elif TRUST_ORDER.get(candidate_verification.level, -1) < TRUST_ORDER.get(
        previous_verification.level, -1
    ):
        reasons.append("The candidate has a weaker verification level.")

    comparisons = (
        (old.identity.publisher, new.identity.publisher, "Publisher metadata changed."),
        (old.identity.kind, new.identity.kind, "The Mod category changed."),
        (old.identity.licenses, new.identity.licenses, "License terms changed."),
        (old.compatibility, new.compatibility, "The supported platform or hardware tuple changed."),
        (old.dependencies, new.dependencies, "Required Mods changed."),
        (old.capabilities, new.capabilities, "Capability ownership or requirements changed."),
        (old.effects, new.effects, "System effects or access changed."),
        (old.recovery, new.recovery, "Recovery or data-retention behavior changed."),
        (_payload_envelope(previous), _payload_envelope(candidate), "Payload mechanism changed."),
    )
    for before, after, reason in comparisons:
        if before != after:
            reasons.append(reason)

    if reasons:
        return UpdateDecision(False, "review-required", tuple(dict.fromkeys(reasons)))
    return UpdateDecision(
        True,
        "automatic-compatible",
        (
            "The verified publisher is unchanged and the candidate stays inside "
            "the complete previously reviewed compatibility, dependency, capability, "
            "effect, payload-mechanism, and recovery envelope.",
        ),
    )
