# SPDX-License-Identifier: Apache-2.0
"""Build the Live Extension payload the Semantic Broker will validate.

Only three things ever reach the panel: the application's own display name,
a clock, and descriptors for the controls that genuinely work.  Nothing
derived from stream metadata that could carry a call title or a participant
name is copied into the payload, and nothing here is ever logged.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

from .qualify import ObservedCall

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to checkers
    from .continuity import ContinuityCall


#: This daemon's own application identity.  The Semantic Broker binds a
#: publication to the identity of the *publisher*, so ``app_id`` is always
#: Luma Calls.  The application actually holding the microphone is named in
#: the title and subtitle, never in ``app_id``.
APPLICATION_ID = "org.projectluma.Calls"
EXTENSION_ID = "calls.active"

#: Refreshed on every update.  If this daemon dies mid-call the broker drops
#: the publication by itself within this window, so the island can never be
#: left asserting a call that has ended.
EXPIRY_SECONDS = 15

MAX_TITLE_BYTES = 256
MAX_SUBTITLE_BYTES = 512
MAX_DESCRIPTION_BYTES = 512

BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")

#: Client names that identify an audio engine rather than an application.
#: These never become a visible title.
GENERIC_CLIENT_NAMES = frozenset({
    "webrtc voiceengine",
    "chromium input",
    "chromium output",
    "cras client",
    "audiostream",
    "alsa plug-in",
    "alsa playback",
    "libcanberra",
    "python3",
    "electron",
})

UNKNOWN_APPLICATION = "An application"


def sanitize(value: str, limit: int) -> str:
    """Strip anything the broker's text validator would reject, then bound it."""

    cleaned = "".join(
        character
        for character in value
        if ord(character) >= 32
        and ord(character) != 127
        and character not in BIDI_CONTROLS
    )
    cleaned = " ".join(cleaned.split())
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= limit:
        return cleaned
    return encoded[:limit].decode("utf-8", "ignore").rstrip()


def format_duration(seconds: float) -> str:
    """Render an elapsed call as MM:SS, widening to H:MM:SS after an hour."""

    total = int(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, second = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{second:02d}"
    return f"{minutes:02d}:{second:02d}"


def application_label(
    call: ObservedCall,
    *,
    desktop_lookup: Callable[[str], tuple[bool, str]] | None = None,
) -> str:
    """Name the application holding the microphone, preferring trusted data."""

    if desktop_lookup is not None and call.application_id:
        try:
            found, name = desktop_lookup(call.application_id)
        except Exception:  # noqa: BLE001 - a bad desktop file must not break a call
            found, name = False, ""
        if found and name.strip():
            return sanitize(name, MAX_TITLE_BYTES)
    candidate = call.application_name
    if candidate and candidate.strip().lower() not in GENERIC_CLIENT_NAMES:
        return sanitize(candidate, MAX_TITLE_BYTES)
    if call.binary:
        return sanitize(call.binary, MAX_TITLE_BYTES)
    if call.application_id:
        return sanitize(call.application_id.rsplit(".", 1)[-1], MAX_TITLE_BYTES)
    return UNKNOWN_APPLICATION


def mute_action(*, muted: bool, application: str = "") -> dict[str, Any]:
    """The microphone control, named for what a press will do.

    The identifier states the intent rather than a toggle, so a press that
    races a change made elsewhere can never invert it.
    """

    who = application or "the call"
    if muted:
        return {
            "id": "call.unmute",
            "label": "Unmute microphone",
            "risk": "low",
            "enabled": True,
            "description": sanitize(
                f"Luma has muted {who}'s microphone. {who} may still show you "
                "as unmuted.",
                MAX_DESCRIPTION_BYTES,
            ),
        }
    return {
        "id": "call.mute",
        "label": "Mute microphone",
        "risk": "low",
        "enabled": True,
        "description": sanitize(
            "Mutes this call's microphone streams. Your microphone stays "
            "available to everything else.",
            MAX_DESCRIPTION_BYTES,
        ),
    }


def deafen_action(*, deafened: bool, application: str = "") -> dict[str, Any]:
    """Deafen, as in Discord: hear nothing and send nothing."""

    who = application or "the call"
    if deafened:
        return {
            "id": "call.undeafen",
            "label": "Undeafen",
            "risk": "low",
            "enabled": True,
            "description": sanitize(
                f"Luma has silenced {who}'s sound and microphone.",
                MAX_DESCRIPTION_BYTES,
            ),
        }
    return {
        "id": "call.deafen",
        "label": "Deafen",
        "risk": "low",
        "enabled": True,
        "description": sanitize(
            "Silences this call's sound and microphone.", MAX_DESCRIPTION_BYTES
        ),
    }


PHONE_LABEL = "Phone"

SETUP_SUBTITLES = {
    "incoming": "Incoming call",
    "dialling": "Calling",
    "ringing-outgoing": "Ringing",
    "connecting": "Connecting",
}


def continuity_mute_action(call: "ContinuityCall") -> dict[str, Any]:
    """Mute is real only while this desktop is carrying the call audio."""

    if not call.mute_available:
        return {
            "id": "call.mute",
            "label": "Mute microphone",
            "risk": "low",
            "enabled": False,
            "description": sanitize(
                "The call audio is on your phone, so this computer cannot "
                "mute the microphone for it.",
                MAX_DESCRIPTION_BYTES,
            ),
        }
    return {
        "id": "call.unmute" if call.muted else "call.mute",
        "label": "Unmute microphone" if call.muted else "Mute microphone",
        "risk": "low",
        "enabled": True,
        "description": sanitize(
            "Mutes the microphone this computer is sending to the call.",
            MAX_DESCRIPTION_BYTES,
        ),
    }


def continuity_hangup_action() -> dict[str, Any]:
    """The one end-call control Luma can honestly offer.

    A Continuity call is Luma's own telephony bridge, so the provider exposes
    a real ``hangup``.  This is the only call that offers the action; no
    third-party application exposes one.
    """

    return {
        "id": "call.hangup",
        "label": "End call",
        "risk": "low",
        "enabled": True,
        "description": sanitize(
            "Ends this call on your phone.", MAX_DESCRIPTION_BYTES
        ),
    }


def build_continuity(
    call: "ContinuityCall",
    *,
    now: datetime,
    expiry_seconds: int = EXPIRY_SECONDS,
) -> dict[str, Any]:
    """Payload for a call the paired phone is authoritative about."""

    address = sanitize(call.address, MAX_TITLE_BYTES) or "Unknown caller"
    if call.timed:
        detail = format_duration(call.duration_seconds(now))
    else:
        detail = SETUP_SUBTITLES.get(call.phase, "In progress")
    return {
        "schema_version": "0.1",
        "id": EXTENSION_ID,
        "app_id": APPLICATION_ID,
        "category": "call",
        "title": address,
        "subtitle": sanitize(f"{PHONE_LABEL} · {detail}", MAX_SUBTITLE_BYTES),
        "privacy": "private",
        "progress": -1.0,
        "actions": [
            continuity_mute_action(call),
            continuity_hangup_action(),
        ],
        "starts_at": call.started().isoformat(),
        "expires_at": (now + timedelta(seconds=expiry_seconds)).isoformat(),
    }


def build(
    call: ObservedCall,
    *,
    now: datetime,
    desktop_lookup: Callable[[str], tuple[bool, str]] | None = None,
    expiry_seconds: int = EXPIRY_SECONDS,
) -> dict[str, Any]:
    """Return the exact dictionary published to ``RegisterLiveExtension``."""

    application = application_label(call, desktop_lookup=desktop_lookup)
    elapsed = format_duration(call.duration_seconds(now))
    return {
        "schema_version": "0.1",
        "id": EXTENSION_ID,
        "app_id": APPLICATION_ID,
        "category": "call",
        "title": sanitize(application, MAX_TITLE_BYTES),
        "subtitle": sanitize(f"{application} · {elapsed}", MAX_SUBTITLE_BYTES),
        "privacy": "private",
        "progress": -1.0,
        # Only controls that work.  Muting and deafening act on the
        # application's own audio streams, which every application has; no
        # third-party application lets another program end its call, so there
        # is no end-call control here at all rather than a dead one.
        "actions": [
            mute_action(muted=call.muted, application=application),
            *([deafen_action(deafened=call.deafened, application=application)]
              if call.can_deafen else []),
        ],
        "starts_at": call.started_at.isoformat(),
        "expires_at": (now + timedelta(seconds=expiry_seconds)).isoformat(),
    }
