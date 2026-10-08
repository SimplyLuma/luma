"""Luma preference domains with real, versioned runtime consumers.

Do not add a domain merely because a Mod wants it. A domain enters this set
only when the shared presentation implementation consumes it and has
apply/remove/recovery tests. Values are design tokens, never CSS or commands.
"""

from __future__ import annotations

from typing import Any


DOCK_APPEARANCE_DOMAIN = "org.luma.shell.dock.appearance"
DOCK_SURFACES = frozenset({"prairie-default", "green-glow"})

SUPPORTED_PREFERENCE_DOMAINS: frozenset[str] = frozenset({
    DOCK_APPEARANCE_DOMAIN,
})


def validate_preference_value(domain: str, value: Any) -> None:
    """Validate the complete value for a registered preference domain.

    The runtime API intentionally exposes a tiny semantic vocabulary. A Mod
    cannot smuggle stylesheet text, paths, selectors, commands, or arbitrary
    presentation properties through the preference store.
    """

    if domain != DOCK_APPEARANCE_DOMAIN:
        raise ValueError(f"unsupported Luma preference domain: {domain}")
    if not isinstance(value, dict) or set(value) != {"surface"}:
        raise ValueError("dock appearance must contain surface only")
    if value["surface"] not in DOCK_SURFACES:
        raise ValueError(
            "dock appearance surface must be one of: "
            + ", ".join(sorted(DOCK_SURFACES))
        )
