# SPDX-License-Identifier: Apache-2.0
"""Optional second source: telephony bridged from the paired phone.

Luma Continuity already owns real call state — a call identifier, the remote
address, the lifecycle phase, and the only end-call control that genuinely
works.  When it reports a call, it is authoritative and the microphone
inference in :mod:`luma_calls.qualify` is not consulted at all, so the two
sources can never both publish.

This module stays deliberately small.  The reducer is pure and duck-typed, so
it is testable without ``luma_continuity``, ``prairie_apps``, GTK or a paired
phone; the adapter below is the only part that touches the installed package,
and every failure there simply removes this source and leaves the PipeWire
path untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Iterable
import logging


LOGGER = logging.getLogger("luma-calls")

#: Phases in which a call is genuinely happening and belongs on screen.
#: ``preparing`` is local optimism before the phone has confirmed anything,
#: and ``ending``/``ended``/``failed`` are withdrawal, not activity.
ACTIVE_PHASES = frozenset({
    "dialling",
    "ringing-outgoing",
    "incoming",
    "connecting",
    "active",
    "held",
    "waiting",
    "multi-call",
})

#: Phases in which the call clock is meaningful rather than a setup state.
TIMED_PHASES = frozenset({"active", "held", "waiting", "multi-call"})

#: ``CallProvider.audio_state()`` reports where the call audio actually is.
#: Only ``connected`` means this desktop is carrying it and can mute it;
#: ``phone`` means the handset owns the audio and nothing here can change it.
AUDIO_CONNECTED = "connected"


@dataclass(frozen=True, slots=True)
class ContinuityCall:
    """One bridged telephony call, reduced to what the island needs."""

    call_id: str
    address: str
    direction: str
    phase: str
    started_at: int
    answered_at: int
    muted: bool
    mute_available: bool
    audio_status: str

    @property
    def timed(self) -> bool:
        return self.phase in TIMED_PHASES

    def duration_seconds(self, now: datetime) -> float:
        anchor = self.answered_at or self.started_at
        if not anchor:
            return 0.0
        return max(0.0, now.timestamp() - anchor)

    def started(self) -> datetime:
        anchor = self.answered_at or self.started_at
        return datetime.fromtimestamp(anchor or 0, UTC)


def _phase_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def select(
    calls: Iterable[Any],
    *,
    audio: dict[str, Any] | None = None,
    mute_supported: bool = False,
) -> ContinuityCall | None:
    """Pick the one call to show, or ``None`` when the phone reports none.

    ``calls`` is any iterable of records carrying ``call_id``, ``address``,
    ``direction``, ``phase``, ``started_at`` and ``answered_at`` — the shape
    ``luma_continuity.call_provider.CallProvider.calls()`` returns.
    """

    state = audio or {}
    status = str(state.get("status") or "")
    muted = state.get("muted") is True
    active = [
        record
        for record in calls
        if _phase_value(getattr(record, "phase", "")) in ACTIVE_PHASES
    ]
    if not active:
        return None
    # An answered call outranks a ringing one, then the earliest start wins;
    # a second leg arriving mid-call never steals the island.
    chosen = min(
        active,
        key=lambda record: (
            0 if _phase_value(record.phase) in TIMED_PHASES else 1,
            int(getattr(record, "answered_at", 0) or getattr(record, "started_at", 0) or 0),
        ),
    )
    return ContinuityCall(
        call_id=str(getattr(chosen, "call_id", "")),
        address=str(getattr(chosen, "address", "")),
        direction=str(getattr(chosen, "direction", "")),
        phase=_phase_value(chosen.phase),
        started_at=int(getattr(chosen, "started_at", 0) or 0),
        answered_at=int(getattr(chosen, "answered_at", 0) or 0),
        muted=muted,
        mute_available=bool(mute_supported) and status == AUDIO_CONNECTED,
        audio_status=status,
    )


class ContinuitySource:
    """Thin, failure-tolerant wrapper around the installed call provider."""

    def __init__(
        self,
        provider: Any,
        *,
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        self.provider = provider
        self.on_change = on_change
        self.call: ContinuityCall | None = None

    @classmethod
    def attach(cls, dispatch, on_change: Callable[[], None]) -> "ContinuitySource | None":
        """Start the installed provider, or return ``None`` if unavailable."""

        try:
            import importlib.util

            if importlib.util.find_spec("luma_continuity") is None:
                return None
            from luma_continuity.call_provider import selected_provider

            provider = selected_provider(dispatch=dispatch)
        except Exception:  # noqa: BLE001 - an absent or unpaired phone is normal
            LOGGER.info("continuity call source unavailable; using capture streams only")
            return None
        source = cls(provider, on_change=on_change)
        try:
            provider.start(source.listener)
        except Exception:  # noqa: BLE001
            LOGGER.info("continuity call source could not start")
            try:
                provider.close()
            except Exception:  # noqa: BLE001
                pass
            return None
        LOGGER.info("continuity call source attached")
        return source

    @property
    def mute_supported(self) -> bool:
        return callable(getattr(self.provider, "mute_audio", None))

    def _audio_state(self) -> dict[str, Any]:
        reader = getattr(self.provider, "audio_state", None)
        if not callable(reader):
            return {}
        try:
            value = reader()
        except Exception:  # noqa: BLE001
            return {}
        return value if isinstance(value, dict) else {}

    def refresh(self) -> ContinuityCall | None:
        try:
            records = self.provider.calls()
        except Exception:  # noqa: BLE001 - a revoked phone simply has no calls
            records = ()
        self.call = select(
            records,
            audio=self._audio_state(),
            mute_supported=self.mute_supported,
        )
        return self.call

    def listener(self, _snapshot: Any, _ok: bool) -> None:
        """Called on the main context whenever the phone's call state moves."""

        previous = self.call
        current = self.refresh()
        if (previous is None) != (current is None) or (
            previous is not None
            and current is not None
            and (previous.call_id, previous.phase, previous.muted)
            != (current.call_id, current.phase, current.muted)
        ):
            # Identifiers and phases only: never the address.
            LOGGER.info(
                "continuity call state changed (active=%s)", current is not None
            )
        self.on_change()

    def set_muted(self, muted: bool) -> bool:
        mute = getattr(self.provider, "mute_audio", None)
        if not callable(mute):
            return False
        try:
            mute(bool(muted))
        except Exception:  # noqa: BLE001
            LOGGER.warning("continuity refused the call audio mute")
            return False
        return True

    def hangup(self, call_id: str) -> bool:
        try:
            self.provider.hangup(call_id)
        except Exception:  # noqa: BLE001
            LOGGER.warning("continuity refused the end-call request")
            return False
        return True

    def close(self) -> None:
        try:
            self.provider.close()
        except Exception:  # noqa: BLE001
            pass
        self.call = None
