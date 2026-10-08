# SPDX-License-Identifier: Apache-2.0
"""A bounded, read-only model of the PipeWire nodes this producer cares about.

``pw-dump`` emits one JSON array for the initial snapshot and, under ``-m``,
one further array per change.  A removal is ``{"id": N, "info": null}`` with no
``type`` key.  A change re-emits the object, but only the sections named by
``info.change-mask`` are guaranteed to be populated, so props and params are
merged onto the last known record rather than replacing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator
import json


NODE_TYPE = "PipeWire:Interface:Node"


@dataclass(frozen=True, slots=True)
class Node:
    """One PipeWire node, reduced to the fields this producer reads."""

    node_id: int
    media_class: str
    state: str
    props: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def text(self, key: str) -> str:
        value = self.props.get(key)
        return value.strip() if isinstance(value, str) else ""

    @property
    def binary(self) -> str:
        """The owning executable, which groups an application's streams.

        An application may open its capture and playback streams from
        different processes (Discord uses one helper for WebRTC capture and
        another for Chromium playback), so the binary rather than the PID is
        the honest application grouping key.
        """

        return self.text("application.process.binary")

    @property
    def identity(self) -> tuple[int, object]:
        """A stream's identity over time.

        PipeWire reuses a node id as soon as the node is gone, so a new stream
        can arrive under the id of the one it replaced; ``object.serial`` is
        never reused within a session.
        """

        return (self.node_id, self.props.get("object.serial"))

    @property
    def application_id(self) -> str:
        return self.text("application.id")

    @property
    def application_name(self) -> str:
        return self.text("application.name")

    @property
    def role(self) -> str:
        return self.text("media.role")

    @property
    def corked(self) -> bool:
        return self.props.get("pulse.corked") is True

    @property
    def _props_param(self) -> dict[str, Any]:
        entries = self.params.get("Props")
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            return entries[0]
        return {}

    @property
    def muted(self) -> bool:
        return self._props_param.get("mute") is True

    @property
    def mutable(self) -> bool:
        """Whether PipeWire actually exposes a mute control on this node."""

        return isinstance(self._props_param.get("mute"), bool)


def _node_from_object(previous: Node | None, value: dict[str, Any]) -> Node | None:
    info = value.get("info")
    if not isinstance(info, dict):
        return None
    identifier = value.get("id")
    if not isinstance(identifier, int) or isinstance(identifier, bool):
        return None
    props = dict(previous.props) if previous is not None else {}
    params = dict(previous.params) if previous is not None else {}
    incoming_props = info.get("props")
    if isinstance(incoming_props, dict):
        props.update(incoming_props)
    incoming_params = info.get("params")
    if isinstance(incoming_params, dict):
        params.update(incoming_params)
    media_class = props.get("media.class")
    state = info.get("state")
    if previous is not None and not isinstance(state, str):
        state = previous.state
    return Node(
        node_id=identifier,
        media_class=media_class if isinstance(media_class, str) else "",
        state=state if isinstance(state, str) else "",
        props=props,
        params=params,
    )


def apply_frame(nodes: dict[int, Node], frame: Any) -> bool:
    """Fold one ``pw-dump`` array into ``nodes``; report whether it changed."""

    if not isinstance(frame, list):
        return False
    changed = False
    for value in frame:
        if not isinstance(value, dict):
            continue
        identifier = value.get("id")
        if not isinstance(identifier, int) or isinstance(identifier, bool):
            continue
        if value.get("info", False) is None:
            changed = nodes.pop(identifier, None) is not None or changed
            continue
        kind = value.get("type")
        if kind is not None and kind != NODE_TYPE:
            # A non-node object can never become a node, so forget any stale
            # record sharing this global id.
            changed = nodes.pop(identifier, None) is not None or changed
            continue
        if kind is None and identifier not in nodes:
            continue
        node = _node_from_object(nodes.get(identifier), value)
        if node is None:
            continue
        if nodes.get(identifier) != node:
            nodes[identifier] = node
            changed = True
    return changed


def graph_from_dump(dump: Any) -> dict[int, Node]:
    """Build a node map from a complete ``pw-dump`` snapshot."""

    nodes: dict[int, Node] = {}
    apply_frame(nodes, dump)
    return nodes


class FrameDecoder:
    """Split the concatenated JSON arrays of ``pw-dump -m`` as they arrive."""

    MAX_BUFFER_BYTES = 8 * 1024 * 1024

    def __init__(self) -> None:
        self._buffer = ""
        self._decoder = json.JSONDecoder()

    def feed(self, text: str) -> Iterator[Any]:
        self._buffer += text
        if len(self._buffer) > self.MAX_BUFFER_BYTES:
            # A truncated or hostile stream must not grow without bound.
            self._buffer = ""
            raise ValueError("PipeWire monitor frame exceeds the size limit")
        while True:
            start = 0
            while start < len(self._buffer) and self._buffer[start].isspace():
                start += 1
            if start >= len(self._buffer):
                self._buffer = ""
                return
            try:
                value, end = self._decoder.raw_decode(self._buffer, start)
            except ValueError:
                self._buffer = self._buffer[start:]
                return
            self._buffer = self._buffer[end:]
            yield value
