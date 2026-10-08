# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows: type roles for code that sets a font itself (TY1).

Labels take a role with `apply_type(label, "reading")` or
`TypeLabel(text, role="temperature")`. Widgets that are not labels and take a
font of their own (a terminal, a code view, a canvas that draws text) ask for
the role's font here, so the size and family still come from the tokens:

    terminal.set_font(type_font("mono"))               # VTE: IBM Plex Mono 13.5
    layout.set_font_description(type_font("dial"))     # Phone's dial pad readout
    type_metrics("mono")                               # size, weight, line height, tracking in px

TY1's roles for app-only surfaces (v70): `reading` 15/400 at 1.65 (Ari's
answers), `assistant-title` 19/650, `temperature` 96/200, `place` 30/500,
`condition` 17/550 (Weather), `album-title` 46/700 (Tide; 34 on a phone),
`section-day` 16/650 (a day's heading), `dial` 38/300 (Phone's number) and
`mono` 13.5 IBM Plex Mono at 1.55 (Terminal, Monitor).
"""
from __future__ import annotations

import gi

gi.require_version("Pango", "1.0")
from gi.repository import Pango  # noqa: E402

from . import rows_tokens  # noqa: E402

__all__ = ["TYPE_FONT_FAMILY", "type_font", "type_metrics"]

#: The system face; a role with a family of its own (mono) says so in its token.
TYPE_FONT_FAMILY = "Figtree"


def type_metrics(role: str) -> dict:
    """A role's size, weight, line height (px), tracking (px), family and whether it is tabular."""
    scale = rows_tokens.group("type_scale")
    key = role.replace("-", "_")
    spec = scale.get(key)
    if not isinstance(spec, dict):
        roles = ", ".join(k.replace("_", "-") for k, v in scale.items() if isinstance(v, dict))
        raise ValueError(f"unknown LumaUI type role {role!r}; use one of {roles}")
    size = float(spec["size"])
    return {"size": size, "weight": int(spec["weight"]), "line_height": round(size * float(spec["line_height"]), 2),
            "tracking": round(size * float(spec.get("tracking_em", 0)), 3),
            "family": spec.get("family", TYPE_FONT_FAMILY), "tabular": bool(spec.get("tabular", False)),
            "phone_size": spec.get("phone_size")}


def type_font(role: str, *, phone: bool = False) -> Pango.FontDescription:
    """The Pango font for a role (`phone=True` for its phone size, where it has one)."""
    metrics = type_metrics(role)
    size = metrics["phone_size"] if phone and metrics["phone_size"] else metrics["size"]
    font = Pango.FontDescription()
    font.set_family(metrics["family"])
    # Pango's enum has no 550 or 650: take the nearest weight it names, and ask a
    # variable face for the exact one.
    weight = metrics["weight"]
    named = min((w for w in Pango.Weight.__enum_values__.values() if int(w) > 0), key=lambda w: abs(int(w) - weight))
    font.set_weight(named)
    if int(named) != weight:
        font.set_variations(f"wght={weight}")
    font.set_absolute_size(size * Pango.SCALE)
    return font
