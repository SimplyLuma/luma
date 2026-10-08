# SPDX-License-Identifier: Apache-2.0
"""The Live Extension payload for the next event in the day.

The publication is bound to Calendar's identity rather than this daemon's: the
event belongs to Calendar, opening it should open Calendar, and there is no
second application here for a person to reason about. The Semantic Broker
resolves that identity from the scope the producer is launched into, which is
why the user unit names it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .events import Upcoming

APPLICATION_ID = "org.projectluma.Calendar"
EXTENSION_ID = "calendar.upnext"

#: Refreshed on every tick. If this daemon stops, the broker drops the
#: publication by itself rather than leaving a stale meeting on the shelf.
#:
#: It must comfortably exceed events.MAX_REFRESH_SECONDS or the publication
#: expires between refreshes and the shelf goes empty; two and a half times the
#: longest sleep survives one missed tick without keeping a dead producer's
#: meeting on screen for long.
EXPIRY_SECONDS = 300

MAX_TITLE_BYTES = 256
MAX_SUBTITLE_BYTES = 512

BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")


def sanitize(value: str, limit: int) -> str:
    """Strip what the broker's text validator rejects, then bound the length."""

    cleaned = "".join(
        character for character in (value or "")
        if character not in BIDI_CONTROLS and (character == " " or character.isprintable())
    ).strip()
    encoded = cleaned.encode("utf-8")[:limit]
    return encoded.decode("utf-8", "ignore").strip()


def humanize(seconds: int) -> str:
    """A duration a person reads at a glance, never more precise than useful."""

    minutes = seconds // 60
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, remainder = divmod(minutes, 60)
    if hours < 24 and remainder:
        return f"{hours} h {remainder} min"
    if hours < 24:
        return f"{hours} h"
    return f"{hours // 24} d"


def standing(state: Upcoming, now: datetime, *, clock24: bool = False) -> str:
    """Where the clock is in relation to the event, in words.

    The shelf already shows when the event starts beside its title, so this
    line says what that time cannot: how soon, or until when.
    """

    fmt = "%H:%M" if clock24 else "%-I:%M %p"
    if state.all_day:
        return "All day"
    if state.in_progress:
        return f"Now · until {state.end.strftime(fmt)}"
    until = state.seconds_until(now)
    if until < 60:
        return "Starting now"
    if until <= 3600:
        return f"Starts in {humanize(until)}"
    return "Later today"


#: The broker's vocabulary, not ours: passive, low, consequential, destructive
#: or security-sensitive. Anything else is refused — and a refused action means
#: the whole publication is refused, so the shelf stays empty with no visible
#: reason. Opening Calendar observes and changes nothing, so it is passive.
def open_action() -> dict[str, Any]:
    return {
        "id": "calendar.open",
        "label": "Open Calendar",
        "risk": "passive",
        "enabled": True,
        "description": "Show this event in Calendar.",
    }


def build(state: Upcoming, *, now: datetime, clock24: bool = False,
          expiry_seconds: int = EXPIRY_SECONDS) -> dict[str, Any]:
    """Return the exact dictionary published to RegisterLiveExtension."""

    return {
        "schema_version": "0.1",
        "id": EXTENSION_ID,
        "app_id": APPLICATION_ID,
        "category": "event",
        "title": sanitize(state.summary, MAX_TITLE_BYTES),
        "subtitle": sanitize(standing(state, now, clock24=clock24), MAX_SUBTITLE_BYTES),
        # An event's name is the person's business. Private means the shelf
        # replaces it with a neutral phrase while the screen is locked, which
        # is the whole reason the class exists.
        "privacy": "private",
        "progress": -1.0,
        "actions": [open_action()],
        "starts_at": state.start.isoformat(),
        "expires_at": (now + timedelta(seconds=expiry_seconds)).isoformat(),
    }
