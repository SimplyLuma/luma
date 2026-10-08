# SPDX-License-Identifier: Apache-2.0
"""Decide, conservatively, whether the microphone capture we see is a call.

The rule is deliberately narrow.  Publishing a Live Extension tells the user
that a call is happening; being wrong about that is worse than showing
nothing, so every uncertain case resolves to "no call".  There is no fallback
path that publishes on a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .nodes import Node


CAPTURE_AUDIO = "Stream/Input/Audio"
PLAYBACK_AUDIO = "Stream/Output/Audio"
CAPTURE_VIDEO = "Stream/Input/Video"

#: A stream must be continuously qualifying for this long before it is a call.
#: A microphone permission probe, a device-enumeration open, or a level meter
#: lives well under a second; a call does not.
DEBOUNCE_SECONDS = 2.0

#: A call is two-way audio.  Requiring a concurrent playback stream from the
#: same application separates a call from dictation, a voice memo, a recorder,
#: or a "test your microphone" panel, none of which play the far side back.
REQUIRE_DUPLEX = True

#: Streams owned by the session itself.  These are Luma's own shell, greeter
#: and audio plumbing, never a user call.
SYSTEM_BINARIES = frozenset({
    "gnome-shell",
    "gnome-session-binary",
    "gnome-session",
    "gdm",
    "gdm-session-worker",
    "gsd-media-keys",
    "gsd-power",
    "gsd-sound",
    "wireplumber",
    "pipewire",
    "pipewire-pulse",
    "pipewire-media-session",
    "xdg-desktop-portal",
    "xdg-desktop-portal-gnome",
    "xdg-desktop-portal-gtk",
    "pw-cat",
    "pw-cli",
    "pw-dump",
    "pw-record",
    "pw-top",
    "wpctl",
    "luma-calls-producer",
    "luma-semantic-broker",
    "speech-dispatcher",
    "spd-say",
})

SYSTEM_APPLICATION_IDS = frozenset({
    "org.gnome.VolumeControl",
    "org.gnome.Shell",
    "org.freedesktop.libcanberra",
    "org.projectluma.Calls",
})

#: PipeWire roles an application sets when it is playing or recording a system
#: sound rather than conversing.
IGNORED_ROLES = frozenset({
    "Notification",
    "Event",
    "Test",
    "A11y",
    "Accessibility",
    "Camera",
})

#: The node states in which a stream is actually moving audio.  A probe that
#: opens a device and closes it again is never driven, so it never reaches
#: ``running``.
ACTIVE_STATES = frozenset({"running"})

#: Playback evidence is weaker on purpose: an application whose output is
#: paused or idle is still holding a two-way audio path open.
PLAYBACK_STATES = frozenset({"running", "idle", "suspended"})


@dataclass(frozen=True, slots=True)
class ObservedCall:
    """The single call this producer is currently willing to assert."""

    audio_node_id: int
    video_node_id: int | None
    binary: str
    application_id: str
    application_name: str
    started_at: datetime
    #: Every capture stream the application owns is muted.
    muted: bool
    camera_mutable: bool
    camera_muted: bool
    #: The application grouping key the audio controls act on.
    group: str = ""
    #: Every playback stream the application owns is muted.
    deafened: bool = False
    #: The application has a playback stream Luma can mute.
    can_deafen: bool = False

    def duration_seconds(self, now: datetime) -> float:
        return max(0.0, (now - self.started_at).total_seconds())


def _attributable(node: Node) -> bool:
    """Reject any stream whose owning application cannot be identified."""

    return bool(node.binary or node.application_id)


def _system_owned(node: Node) -> bool:
    return (
        node.binary in SYSTEM_BINARIES
        or node.application_id in SYSTEM_APPLICATION_IDS
    )


def group_key(node: Node) -> str:
    """The application a stream belongs to: its executable, else its app id."""

    return node.binary or node.application_id


def _all_muted(nodes: Mapping[int, Node], group: str, media_class: str) -> bool:
    found = [
        node
        for node in nodes.values()
        if node.media_class == media_class and group_key(node) == group and node.mutable
    ]
    return bool(found) and all(node.muted for node in found)


def is_capture_candidate(node: Node) -> bool:
    """Structural qualification for one node, before any debounce."""

    # An exact match rejects WirePlumber's plumbing classes such as
    # ``Stream/Input/Audio/Internal`` (verified on hardware: the Bluetooth
    # internal capture loopback carries no application identity at all).
    if node.media_class != CAPTURE_AUDIO:
        return False
    if node.state not in ACTIVE_STATES:
        return False
    if node.corked:
        return False
    if node.role in IGNORED_ROLES:
        return False
    if not _attributable(node):
        return False
    if _system_owned(node):
        return False
    return True


def has_playback(nodes: Mapping[int, Node], group: str) -> bool:
    for node in nodes.values():
        if node.media_class != PLAYBACK_AUDIO:
            continue
        if node.state not in PLAYBACK_STATES:
            continue
        if group_key(node) != group:
            continue
        return True
    return False


def video_stream(nodes: Mapping[int, Node], group: str) -> Node | None:
    for node in sorted(nodes.values(), key=lambda item: item.node_id):
        if node.media_class != CAPTURE_VIDEO:
            continue
        if node.state not in ACTIVE_STATES:
            continue
        if group_key(node) != group:
            continue
        return node
    return None


def capture_candidates(nodes: Mapping[int, Node]) -> list[Node]:
    """Every capture stream that structurally looks like a call leg."""

    candidates = [node for node in nodes.values() if is_capture_candidate(node)]
    if REQUIRE_DUPLEX:
        candidates = [
            node for node in candidates if has_playback(nodes, group_key(node))
        ]
    return sorted(candidates, key=lambda node: node.node_id)


class CallTracker:
    """Hold a candidate stream for :data:`DEBOUNCE_SECONDS` before asserting it.

    The clock is injected so the debounce and the visible timer are both
    testable without sleeping.
    """

    def __init__(self, *, debounce_seconds: float = DEBOUNCE_SECONDS) -> None:
        self.debounce_seconds = debounce_seconds
        self._first_seen: dict[int, datetime] = {}
        self._published: int | None = None

    @property
    def pending(self) -> int:
        """How many candidates are still inside the debounce window."""

        return sum(1 for _ in self._first_seen)

    def observe(self, nodes: Mapping[int, Node], now: datetime) -> ObservedCall | None:
        candidates = capture_candidates(nodes)
        live = {node.node_id: node for node in candidates}
        for node_id in [key for key in self._first_seen if key not in live]:
            # A stream that stops qualifying loses its credit entirely; it must
            # earn the full debounce again if it comes back.
            del self._first_seen[node_id]
        for node_id in live:
            self._first_seen.setdefault(node_id, now)

        qualified = [
            node
            for node in candidates
            if (now - self._first_seen[node.node_id]).total_seconds()
            >= self.debounce_seconds
        ]
        if not qualified:
            self._published = None
            return None
        # The oldest qualifying stream is the call; a second stream appearing
        # mid-call (a screen-share helper, say) never displaces it.
        chosen = min(qualified, key=lambda node: (self._first_seen[node.node_id], node.node_id))
        self._published = chosen.node_id
        group = group_key(chosen)
        video = video_stream(nodes, group)
        return ObservedCall(
            audio_node_id=chosen.node_id,
            video_node_id=None if video is None else video.node_id,
            binary=chosen.binary,
            application_id=chosen.application_id,
            application_name=chosen.application_name,
            started_at=self._first_seen[chosen.node_id],
            muted=_all_muted(nodes, group, CAPTURE_AUDIO),
            camera_mutable=video is not None and video.mutable,
            camera_muted=video is not None and video.muted,
            group=group,
            deafened=_all_muted(nodes, group, PLAYBACK_AUDIO),
            can_deafen=any(
                node.mutable
                for node in nodes.values()
                if node.media_class == PLAYBACK_AUDIO and group_key(node) == group
            ),
        )

    def forget(self, node_ids: Iterable[int] = ()) -> None:
        for node_id in node_ids:
            self._first_seen.pop(node_id, None)
        if not node_ids:
            self._first_seen.clear()
        self._published = None
