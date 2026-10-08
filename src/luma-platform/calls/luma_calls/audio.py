# SPDX-License-Identifier: Apache-2.0
"""Mute and deafen any application in a call, at the audio layer.

No application exposes its call controls to the rest of the desktop, so Luma
controls what it can see: the application's own PipeWire streams.  Muting
every capture stream the application owns silences what it sends; muting
every playback stream it owns silences what it plays.  This is the same
per-application stream mute ``pactl set-source-output-mute`` and the GNOME
volume panel perform, applied to exactly one application's streams and never
to a device.

Three things make that reliable rather than merely possible:

* **Streams come and go.**  WebRTC stacks reopen their capture stream when the
  device or the call's format changes, and open a playback stream per remote
  track.  What the person asked for is an *intent* per application and
  direction, applied to every stream that direction has now and every stream
  it opens while the intent stands.

* **WirePlumber remembers.**  ``state-stream`` stores a stream's mute under
  the application's name and restores it when that application next opens a
  stream.  A mute Luma applied during one call would silently start the next
  call muted.  Every application and direction Luma muted is written to an
  undo ledger on disk; the first stream that application opens after the call
  is unmuted, which also rewrites WirePlumber's memory.

* **Someone else may change it.**  The volume panel, ``wpctl`` or the
  application itself can unmute a stream Luma muted.  The published state is
  always read back from the graph, never assumed, and a stream changed by
  someone else ends Luma's intent for that direction, so Luma never fights the
  person over their own microphone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping
import json
import logging
import os

from .nodes import Node
from .qualify import CAPTURE_AUDIO, PLAYBACK_AUDIO, group_key


LOGGER = logging.getLogger("luma-calls")

CAPTURE = "capture"
PLAYBACK = "playback"
DIRECTIONS = {CAPTURE: CAPTURE_AUDIO, PLAYBACK: PLAYBACK_AUDIO}

#: A call's capture stream reopening, or the call briefly failing to qualify,
#: must not undo a mute the person asked for.  An intent survives this long
#: without its call before it is undone.
GRACE_SECONDS = 10.0

#: How long an application's new streams must stay unmuted before a mute Luma
#: left behind is considered undone.
REPAIR_SETTLE_SECONDS = 1.5

Mute = Callable[[int, bool], bool]


def streams(nodes: Mapping[int, Node], group: str, direction: str) -> list[Node]:
    """Every stream ``group`` owns in ``direction``, in a stable order."""

    media_class = DIRECTIONS[direction]
    return sorted(
        (
            node
            for node in nodes.values()
            if node.media_class == media_class and group_key(node) == group
        ),
        key=lambda node: node.node_id,
    )


def muted(nodes: Mapping[int, Node], group: str, direction: str) -> bool | None:
    """True when every stream in the direction is muted; None with no streams.

    "All" rather than "any": a single live stream means the far side can still
    hear (or be heard), and the island must never claim otherwise.
    """

    found = [node for node in streams(nodes, group, direction) if node.mutable]
    if not found:
        return None
    return all(node.muted for node in found)


def default_ledger_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return Path(base) / "luma" / "calls" / "audio-undo.json"


class UndoLedger:
    """Application groups and directions Luma muted and has not yet restored.

    Only group keys (an executable or application id) and a direction are
    stored: never a call, a title, or a participant.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entries: set[tuple[str, str]] = set()
        if path is not None:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.entries = {
                    (str(group), str(direction))
                    for group, direction in raw.get("muted", [])
                    if direction in DIRECTIONS and isinstance(group, str) and group
                }
            except (OSError, ValueError, TypeError, AttributeError):
                self.entries = set()

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self.entries

    def add(self, key: tuple[str, str]) -> None:
        if key not in self.entries:
            self.entries.add(key)
            self._save()

    def discard(self, key: tuple[str, str]) -> None:
        if key in self.entries:
            self.entries.discard(key)
            self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"muted": sorted([list(entry) for entry in self.entries])}),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as error:
            LOGGER.warning("could not write the audio undo ledger: %s", type(error).__name__)


@dataclass(slots=True)
class Intent:
    muted: bool
    #: Streams (by identity) Luma has applied this intent to.
    handled: set[tuple]
    #: Streams since seen in the intended state.  Only a confirmed stream
    #: that then differs was changed by someone else; a stream whose write
    #: has not reached the graph yet is not.
    confirmed: set[tuple]
    #: Monotonic time the owning call was last seen; None while it is live.
    orphaned_at: float | None = None


class AudioControls:
    """Per-application mute and deafen, reconciled against the live graph."""

    def __init__(self, mute: Mute, ledger: UndoLedger, *, grace: float = GRACE_SECONDS) -> None:
        self.mute = mute
        self.ledger = ledger
        self.grace = grace
        self.intents: dict[tuple[str, str], Intent] = {}
        #: The microphone intent in force when the call was deafened, so
        #: undeafening restores it rather than always unmuting.
        self.mic_before_deafen: dict[str, bool] = {}
        self._repair_since: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------
    # Requests

    def set(self, nodes: Mapping[int, Node], group: str, direction: str, wanted: bool) -> bool:
        """Apply a mute to every stream in the direction.  False if none took."""

        found = [node for node in streams(nodes, group, direction) if node.mutable]
        if not found:
            return False
        ok = True
        for node in found:
            if node.muted != wanted and not self.mute(node.node_id, wanted):
                ok = False
        key = (group, direction)
        if wanted:
            self.intents[key] = Intent(
                True,
                {node.identity for node in found},
                {node.identity for node in found if node.muted},
            )
            self.ledger.add(key)
        else:
            self.intents.pop(key, None)
            self.ledger.discard(key)
        LOGGER.info(
            "%s %s on %d %s stream(s)",
            "applied" if ok else "partly applied",
            "mute" if wanted else "unmute",
            len(found),
            direction,
        )
        return ok

    def deafen(self, nodes: Mapping[int, Node], group: str, wanted: bool) -> bool:
        """Discord's meaning: deafened hears nothing and says nothing."""

        if wanted:
            self.mic_before_deafen[group] = bool(muted(nodes, group, CAPTURE))
            ok = self.set(nodes, group, PLAYBACK, True)
            if muted(nodes, group, CAPTURE) is False:
                self.set(nodes, group, CAPTURE, True)
            return ok
        ok = self.set(nodes, group, PLAYBACK, False)
        if not self.mic_before_deafen.pop(group, False) and muted(nodes, group, CAPTURE):
            self.set(nodes, group, CAPTURE, False)
        return ok

    # ------------------------------------------------------------------
    # Reconciliation, on every graph change and every tick

    def reconcile(self, nodes: Mapping[int, Node], active_group: str | None, now: float) -> None:
        for (group, direction), intent in list(self.intents.items()):
            key = (group, direction)
            present = [node for node in streams(nodes, group, direction) if node.mutable]
            # Someone else changed a stream Luma had set: stop managing it.
            if any(node.identity in intent.confirmed and node.muted != intent.muted for node in present):
                LOGGER.info("a %s stream was changed outside Luma; releasing it", direction)
                del self.intents[key]
                self.ledger.discard(key)
                self.mic_before_deafen.pop(group, None)
                continue
            if group == active_group:
                intent.orphaned_at = None
            elif intent.orphaned_at is None:
                intent.orphaned_at = now
            if intent.orphaned_at is not None and now - intent.orphaned_at >= self.grace:
                self._undo(key, present)
                continue
            # A stream opened since the request carries the request too.
            for node in present:
                if node.muted == intent.muted:
                    intent.confirmed.add(node.identity)
                if node.identity in intent.handled:
                    continue
                if node.muted != intent.muted:
                    self.mute(node.node_id, intent.muted)
                    LOGGER.info("carried the %s mute to a new stream", direction)
                intent.handled.add(node.identity)
        self._repair_restored(nodes, now)

    def _undo(self, key: tuple[str, str], present: Iterable[Node]) -> None:
        group, direction = key
        del self.intents[key]
        self.mic_before_deafen.pop(group, None)
        survivors = [node for node in present if node.muted]
        for node in survivors:
            self.mute(node.node_id, False)
        if survivors:
            # Unmuting a live stream also rewrites WirePlumber's memory.
            self.ledger.discard(key)
        LOGGER.info(
            "the call ended; restored %d %s stream(s)%s",
            len(survivors),
            direction,
            "" if survivors else ", undo deferred to the next stream",
        )

    def _repair_restored(self, nodes: Mapping[int, Node], now: float) -> None:
        """Unmute the first streams an application opens after a Luma mute.

        WirePlumber applies the remembered mute as the stream is created, and
        that can land after Luma first sees the stream.  The entry is only
        retired once the application's streams have stayed unmuted for
        :data:`REPAIR_SETTLE_SECONDS`, and a restored mute arriving late is
        undone when it appears.
        """

        for key in list(self.ledger.entries):
            if key in self.intents:
                self._repair_since.pop(key, None)
                continue
            group, direction = key
            present = [node for node in streams(nodes, group, direction) if node.mutable]
            if not present:
                self._repair_since.pop(key, None)
                continue
            restored = [node for node in present if node.muted]
            for node in restored:
                self.mute(node.node_id, False)
            if restored:
                LOGGER.info("undid a remembered %s mute on %d new stream(s)", direction, len(restored))
                self._repair_since.pop(key, None)
                continue
            since = self._repair_since.setdefault(key, now)
            if now - since >= REPAIR_SETTLE_SECONDS:
                self._repair_since.pop(key, None)
                self.ledger.discard(key)

    def forget_graph(self) -> None:
        """The graph is being rebuilt; no stream id seen so far is meaningful."""

        for intent in self.intents.values():
            intent.handled.clear()
            intent.confirmed.clear()
