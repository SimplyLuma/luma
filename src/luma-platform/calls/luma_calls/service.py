# SPDX-License-Identifier: Apache-2.0
"""The Luma Calls producer daemon.

Watches PipeWire for a qualifying microphone capture stream, publishes exactly
one Live Extension while that call lasts, keeps its timer current, withdraws
the publication the moment the stream ends, and serves the broker's
``InvokeAction`` for the controls that can genuinely work: mute and deafen
for any application (at its PipeWire streams), and hang-up only for a call
Luma itself carries.

PipeWire is watched with ``pw-dump --monitor``, which pushes a full snapshot
followed by one JSON array per change.  Nothing here polls the graph.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
import logging
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from luma_semantic_broker.client import LiveExtensionPublisher
from luma_semantic_broker.service import BUS_NAME, DEFAULT_XML, PROVIDER_INTERFACE
from luma_semantic_broker.variant import encode

from . import audio, control, extension
from .continuity import ContinuitySource
from .nodes import FrameDecoder, apply_frame
from .qualify import CAPTURE_AUDIO, CallTracker


LOGGER = logging.getLogger("luma-calls")

MONITOR_ARGV = ("pw-dump", "--monitor", "--no-colors")
MONITOR_READ_BYTES = 64 * 1024
MONITOR_RESTART_SECONDS = 2
#: One tick per second is the coarsest cadence that keeps a visible MM:SS
#: timer truthful.  The broker rate-limits a publisher to 120 calls a minute,
#: so a steady 60 updates a minute leaves half the budget for everything else.
TICK_SECONDS = 1
#: Minimum spacing between ordinary bus writes.  Call start and call end
#: bypass it so the island appears and disappears without delay.
MIN_SYNC_INTERVAL = 0.9

ACTION_MUTE = "call.mute"
ACTION_UNMUTE = "call.unmute"
ACTION_DEAFEN = "call.deafen"
ACTION_UNDEAFEN = "call.undeafen"
ACTION_CAMERA = "call.camera"
ACTION_HANGUP = "call.hangup"

PROVIDER_ERROR = "org.projectluma.SemanticProvider1.Error.Failed"
PROVIDER_REFUSED = "org.projectluma.SemanticProvider1.Error.Refused"
PROVIDER_UNKNOWN = "org.projectluma.SemanticProvider1.Error.UnknownAction"


def _desktop_lookup(app_id: str) -> tuple[bool, str]:
    from luma_semantic_broker.desktop import desktop_application

    return desktop_application(app_id)


class CallsProducer:
    """Glue between the PipeWire monitor, the tracker, and the broker."""

    def __init__(
        self,
        application: Gio.Application,
        *,
        clock=lambda: datetime.now(UTC),
        mute=control.set_node_mute,
        xml_path: Path | None = None,
        ledger_path: Path | None = None,
    ) -> None:
        self.clock = clock
        self.mute = mute
        self.audio = audio.AudioControls(
            mute, audio.UndoLedger(ledger_path or audio.default_ledger_path())
        )
        #: False from a monitor (re)start until its first snapshot lands, so
        #: an empty graph is never mistaken for every call having ended.
        self._graph_ready = False
        self.nodes: dict[int, Any] = {}
        self.decoder = FrameDecoder()
        self.tracker = CallTracker()
        self.call = None
        self.continuity: ContinuitySource | None = None
        self.source = ""
        self._payload: dict[str, Any] | None = None
        self._last_sync = 0.0
        self._monitor: Gio.Subprocess | None = None
        self._stopping = False

        self.connection = application.get_dbus_connection()
        if self.connection is None:
            raise RuntimeError("application must be registered before producing calls")
        self.publisher = LiveExtensionPublisher(
            application,
            extension.APPLICATION_ID,
            extension.EXTENSION_ID,
            self._extension,
        )
        selected_xml = xml_path or Path(
            os.environ.get("LUMA_SEMANTIC_BROKER_XML", DEFAULT_XML)
        )
        node = Gio.DBusNodeInfo.new_for_xml(selected_xml.read_text(encoding="utf-8"))
        provider = next(
            item for item in node.interfaces if item.name == PROVIDER_INTERFACE
        )
        self.registration = self.connection.register_object_with_closures2(
            self.publisher.provider_path, provider, self._method_call, None, None
        )
        self._tick_source = GLib.timeout_add_seconds(TICK_SECONDS, self._tick)
        self._start_monitor()
        # Telephony is a bonus source, never a prerequisite: if the paired
        # phone is absent, unpaired or broken, capture-stream detection is
        # entirely unaffected.
        self.continuity = ContinuitySource.attach(GLib.idle_add, self._continuity_changed)

    # ------------------------------------------------------------------
    # PipeWire monitor

    def _start_monitor(self) -> bool:
        if self._stopping:
            return GLib.SOURCE_REMOVE
        # A restarted monitor re-sends a full snapshot, so the previous graph
        # must be dropped rather than merged; a stale node must never keep a
        # call on screen.
        self.nodes = {}
        self.decoder = FrameDecoder()
        self.tracker.forget()
        self._graph_ready = False
        self.audio.forget_graph()
        try:
            self._monitor = Gio.Subprocess.new(
                list(MONITOR_ARGV),
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error:
            LOGGER.warning("pw-dump monitor could not start; retrying")
            self._monitor = None
            GLib.timeout_add_seconds(MONITOR_RESTART_SECONDS, self._start_monitor)
            return GLib.SOURCE_REMOVE
        LOGGER.info("pipewire monitor started")
        self._monitor.wait_async(None, self._monitor_exited)
        self._read(self._monitor.get_stdout_pipe())
        return GLib.SOURCE_REMOVE

    def _monitor_exited(self, process: Gio.Subprocess, result) -> None:
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        if self._stopping or process is not self._monitor:
            return
        LOGGER.info("pipewire monitor exited; restarting")
        self._monitor = None
        self._drop_capture_state("pipewire monitor restart")
        GLib.timeout_add_seconds(MONITOR_RESTART_SECONDS, self._start_monitor)

    def _read(self, stream: Gio.InputStream) -> None:
        stream.read_bytes_async(
            MONITOR_READ_BYTES, GLib.PRIORITY_DEFAULT, None, self._read_finished
        )

    def _read_finished(self, stream: Gio.InputStream, result) -> None:
        try:
            data = stream.read_bytes_finish(result)
        except GLib.Error:
            return
        if data is None or data.get_size() == 0:
            return
        try:
            text = data.get_data().decode("utf-8", "replace")
            changed = False
            for frame in self.decoder.feed(text):
                changed = apply_frame(self.nodes, frame) or changed
                if not self._graph_ready:
                    self._graph_ready = True
                    changed = True
        except ValueError:
            LOGGER.warning("discarded an unparsable pipewire monitor frame")
            self.decoder = FrameDecoder()
            changed = False
        if changed:
            self._evaluate()
        if not self._stopping:
            self._read(stream)

    # ------------------------------------------------------------------
    # Call state

    def _continuity_changed(self) -> None:
        self._evaluate()

    def _select(self):
        """Arbitrate the two sources into the one publication this daemon owns.

        There is a single extension id and a single publication slot, so two
        live extensions for one call are structurally impossible.  Continuity
        wins whenever it reports a call: it knows the call is real, whereas
        the capture-stream path only infers it — and a Continuity call whose
        audio is bridged here also shows up as a capture stream, which is
        exactly the double-count this ordering removes.
        """

        now = self.clock()
        if self.continuity is not None:
            bridged = self.continuity.refresh()
            if bridged is not None:
                return "continuity", bridged, now
        return "pipewire", self.tracker.observe(self.nodes, now), now

    @staticmethod
    def _identity(source: str, call) -> tuple:
        if call is None:
            return ()
        if source == "continuity":
            return ("continuity", call.call_id)
        return ("pipewire", call.audio_node_id)

    def _evaluate(self) -> None:
        previous, previous_source = self.call, self.source
        source, call, _now = self._select()
        self.call, self.source = call, source if call is not None else ""
        if self._graph_ready:
            active = call.group if source == "pipewire" and call is not None else None
            self.audio.reconcile(self.nodes, active, monotonic())
        before = self._identity(previous_source, previous)
        after = self._identity(self.source, self.call)
        if before == after:
            # A mute or deafen that landed is shown at once, not on the next
            # rate-limited tick: the island is the only place it is visible.
            controls = lambda value: (
                getattr(value, "muted", None), getattr(value, "deafened", None)
            )
            self._sync(force=controls(previous) != controls(call))
            return
        if not before:
            if source == "continuity":
                LOGGER.info("publishing a continuity call")
            else:
                LOGGER.info(
                    "qualified call on capture node %d (%d capture streams in graph)",
                    call.audio_node_id,
                    sum(
                        1
                        for node in self.nodes.values()
                        if node.media_class == CAPTURE_AUDIO
                    ),
                )
        elif not after:
            LOGGER.info("the published call ended; withdrawing")
        else:
            LOGGER.info("the published call moved between sources or streams")
        self._sync(force=True)

    def _drop_capture_state(self, reason: str) -> None:
        """Forget every inferred capture stream; telephony state is untouched."""

        if self.source == "pipewire" and self.call is not None:
            LOGGER.info("withdrawing the inferred call: %s", reason)
        self.tracker.forget()
        self.nodes = {}
        self._evaluate()

    def _extension(self) -> dict[str, Any] | None:
        """The callable ``LiveExtensionPublisher`` asks for on every sync."""

        if self.call is None:
            self._payload = None
            return None
        if self.source == "continuity":
            self._payload = extension.build_continuity(self.call, now=self.clock())
        else:
            self._payload = extension.build(
                self.call, now=self.clock(), desktop_lookup=_desktop_lookup
            )
        return self._payload

    def _sync(self, *, force: bool = False) -> None:
        now = monotonic()
        if not force and now - self._last_sync < MIN_SYNC_INTERVAL:
            return
        if self.call is None and not self.publisher.publication_id:
            return
        self._last_sync = now
        self.publisher.sync()

    def _tick(self) -> bool:
        if self._stopping:
            return GLib.SOURCE_REMOVE
        self._evaluate()
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------
    # Broker-invoked actions

    def _broker_owner(self) -> str:
        try:
            result = self.connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                GLib.Variant("(s)", (BUS_NAME,)),
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                2_000,
                None,
            )
            return result.unpack()[0]
        except GLib.Error:
            return ""

    def _method_call(
        self,
        _connection,
        sender,
        _object_path,
        _interface_name,
        method_name,
        parameters,
        invocation,
    ) -> None:
        if sender != self._broker_owner() or method_name != "InvokeAction":
            invocation.return_dbus_error(
                PROVIDER_REFUSED,
                "call actions may only be invoked by the authenticated broker",
            )
            return
        object_id = parameters.get_child_value(0).get_string()
        action_id = parameters.get_child_value(1).get_string()
        if object_id != extension.EXTENSION_ID:
            invocation.return_dbus_error(PROVIDER_ERROR, "unknown live extension object")
            return
        if self.call is None:
            invocation.return_dbus_error(PROVIDER_ERROR, "no call is in progress")
            return
        try:
            result = self._invoke(action_id)
        except LookupError:
            invocation.return_dbus_error(PROVIDER_UNKNOWN, "unknown call action")
            return
        except RuntimeError as error:
            invocation.return_dbus_error(PROVIDER_ERROR, str(error))
            return
        invocation.return_value(
            GLib.Variant.new_tuple(GLib.Variant.new_variant(encode(result)))
        )
        GLib.idle_add(self._refresh_after_action)

    def _invoke(self, action_id: str) -> dict[str, Any]:
        if self.source == "continuity":
            return self._invoke_continuity(action_id)
        return self._invoke_capture(action_id)

    def _invoke_capture(self, action_id: str) -> dict[str, Any]:
        call = self.call
        if action_id in (ACTION_MUTE, ACTION_UNMUTE):
            wanted = action_id == ACTION_MUTE
            if not self.audio.set(self.nodes, call.group, audio.CAPTURE, wanted):
                raise RuntimeError("PipeWire refused the microphone mute")
            return {"muted": wanted}
        if action_id in (ACTION_DEAFEN, ACTION_UNDEAFEN):
            wanted = action_id == ACTION_DEAFEN
            if not self.audio.deafen(self.nodes, call.group, wanted):
                raise RuntimeError("PipeWire refused to silence the call")
            return {"deafened": wanted}
        if action_id == ACTION_HANGUP:
            # No third-party application lets another program end its call:
            # Chromium's hang-up Media Session action has no Linux bridge, and
            # Discord's voice RPC is closed to applications Discord has not
            # approved.  Killing the process or synthesizing input would not
            # be ending the call, so this is never offered or faked.
            raise RuntimeError(
                "ending another application's call is not possible from Luma"
            )
        raise LookupError(action_id)

    def _invoke_continuity(self, action_id: str) -> dict[str, Any]:
        call = self.call
        source = self.continuity
        if source is None:
            raise RuntimeError("the phone connection is no longer available")
        if action_id in (ACTION_MUTE, ACTION_UNMUTE):
            if not call.mute_available:
                raise RuntimeError("the call audio is not on this computer")
            wanted = action_id == ACTION_MUTE
            if not source.set_muted(wanted):
                raise RuntimeError("the phone refused the microphone mute")
            return {"muted": wanted}
        if action_id == ACTION_HANGUP:
            if not source.hangup(call.call_id):
                raise RuntimeError("the phone refused the end-call request")
            return {"ended": True}
        raise LookupError(action_id)

    def _refresh_after_action(self) -> bool:
        self._evaluate()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------

    def close(self) -> None:
        self._stopping = True
        if self._tick_source:
            GLib.source_remove(self._tick_source)
            self._tick_source = 0
        if self._monitor is not None:
            try:
                self._monitor.force_exit()
            except GLib.Error:
                pass
            self._monitor = None
        if self.continuity is not None:
            self.continuity.close()
            self.continuity = None
        self.call = None
        self.source = ""
        self.publisher.close()
        self.connection.unregister_object(self.registration)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="luma-calls: %(levelname)s %(message)s"
    )
    application = Gio.Application(
        application_id=extension.APPLICATION_ID,
        flags=Gio.ApplicationFlags.IS_SERVICE,
    )
    try:
        application.register(None)
    except GLib.Error:
        LOGGER.error("could not register on the session bus")
        return 1
    if application.get_is_remote():
        LOGGER.info("another call producer already owns the session; exiting")
        return 0
    try:
        producer = CallsProducer(application)
    except (GLib.Error, OSError, RuntimeError, ValueError):
        LOGGER.error("could not start the call producer")
        return 1
    application.hold()
    try:
        return application.run(None)
    finally:
        producer.close()


if __name__ == "__main__":
    raise SystemExit(main())
