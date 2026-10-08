# SPDX-License-Identifier: Apache-2.0
"""One playback controller shared by Tide UI, actions, MPRIS, and shell state."""
from __future__ import annotations

import enum
import random
import re
import threading
from dataclasses import dataclass, replace
from typing import Callable, Protocol

from .model import LibraryStore, QueueEntry, RepeatMode, Track, TrackCopy

_URL_QUERY = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s?#]*)[?#]\S*", re.IGNORECASE)


class PlaybackUnavailable(Exception):
    """A copy that cannot be opened right now; the message is safe to show."""


def redact_uris(message: str) -> str:
    """Drop query strings from URIs in a message: a stream URI may carry
    credentials that must never reach a notification, MPRIS, or a log."""
    return _URL_QUERY.sub(r"\1", message)


class PlaybackState(str, enum.Enum):
    STOPPED = "stopped"
    PAUSED = "paused"
    PLAYING = "playing"
    LOADING = "loading"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    state: PlaybackState = PlaybackState.STOPPED
    track: Track | None = None
    copy: TrackCopy | None = None
    queue_position: int = 0
    queue_length: int = 0
    position_ns: int = 0
    duration_ns: int = 0
    volume: float = 1.0
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF
    error: str | None = None

    @property
    def can_seek(self) -> bool:
        return self.track is not None and self.duration_ns > 0

    @property
    def can_previous(self) -> bool:
        return self.track is not None and (self.position_ns > 3_000_000_000 or self.queue_position > 0)

    @property
    def can_next(self) -> bool:
        return self.track is not None and (
            self.queue_position + 1 < self.queue_length or self.repeat is RepeatMode.ALL
        )


class PlaybackEngine(Protocol):
    # Events delivered to the handler set via set_event_handler:
    #   ("state", <PlaybackState value>)  ("position", <ns>)  ("duration", <ns>)
    #   ("error", <message>)  ("eos", None)
    #   ("advanced", None) -- the engine switched, gaplessly and on its own,
    #     to the URI most recently returned by the next-track provider (see
    #     set_next_track_provider). This is the reliable "the track actually
    #     changed" signal for a gapless transition: it fires once the new
    #     track's data is actually flowing, not when the switch was merely
    #     queued. A plain "eos" with no prior gapless hand-off is the
    #     ordinary (non-gapless) end-of-track fallback.
    def set_event_handler(self, handler: Callable[[str, object], None]) -> None: ...
    def set_next_track_provider(self, provider: Callable[[], str | None]) -> None: ...
    def load(self, uri: str, *, position_ns: int = 0, play: bool = False) -> None: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def seek(self, position_ns: int) -> None: ...
    def set_volume(self, volume: float) -> None: ...
    def position(self) -> int: ...
    def close(self) -> None: ...


class PlaybackController:
    """Owns durable queue semantics and delegates only audio I/O to an engine."""

    def __init__(
        self,
        store: LibraryStore,
        engine: PlaybackEngine,
        resolver: Callable[[TrackCopy], str] | None = None,
        *,
        resolve_uri: Callable[[TrackCopy], str] | None = None,
    ) -> None:
        self.store = store
        self.engine = engine
        # Turns a stored copy into the URI the engine opens. Local copies play
        # their stored URI directly; a remote source authenticates here, at the
        # moment of playback, so no stored URI ever carries a credential.
        self._resolver = resolver or resolve_uri or (lambda copy: copy.uri)
        self._resolve = self._resolver
        session = store.session_state()
        self._snapshot = PlaybackSnapshot(
            queue_position=session.current_position,
            position_ns=session.position_ns,
            volume=session.volume,
            shuffle=session.shuffle,
            repeat=session.repeat,
        )
        self._listeners: list[Callable[[PlaybackSnapshot], None]] = []
        self._lock = threading.RLock()
        self._shuffle_order: list[int] = []
        self._failed_positions: set[int] = set()
        # Tracks handed to the engine for a gapless transition but not yet
        # confirmed as playing, oldest first. Appended by _compute_next_uri
        # (called from the engine's about-to-finish, possibly off the main
        # thread) and consumed in order by _commit_advance as the engine
        # reports each new stream actually starting.
        #
        # This is a queue rather than a single slot because the two events
        # are driven by different threads: the engine can reach the end of
        # the handed-over track and ask for the one after it before the main
        # loop has processed the STREAM_START for the first hand-off. Asking
        # "what follows the last track handed over" -- not "what follows the
        # track currently published" -- is what keeps that from handing the
        # same track over twice.
        self._handed_over: list[tuple[int, QueueEntry, TrackCopy]] = []
        self.engine.set_event_handler(self._on_engine_event)
        self.engine.set_next_track_provider(self._provide_next_uri)
        self.engine.set_volume(session.volume)
        self._restore_queue()

    @property
    def snapshot(self) -> PlaybackSnapshot:
        with self._lock:
            return self._snapshot

    def subscribe(self, listener: Callable[[PlaybackSnapshot], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self.snapshot)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _publish(self, **changes: object) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, **changes)
            snapshot = self._snapshot
        for listener in tuple(self._listeners):
            listener(snapshot)

    def _queue(self) -> list[QueueEntry]:
        return self.store.queue()

    def _restore_queue(self) -> None:
        queue = self._queue()
        if not queue:
            self._publish(track=None, copy=None, queue_position=0, queue_length=0, duration_ns=0)
            return
        position = min(self.store.session_state().current_position, len(queue) - 1)
        entry = queue[position]
        copy = self.store.select_copy(entry.track.id, entry.selected_copy_id)
        self._publish(
            track=entry.track,
            copy=copy,
            queue_position=position,
            queue_length=len(queue),
            duration_ns=entry.track.duration_ns,
        )

    def reload_queue(self) -> None:
        """Re-read a queue that lost items outside the controller, such as when a
        source was removed; the current song keeps playing if it survived."""
        snapshot = self.snapshot
        entries = self._queue()
        ids = [entry.track.id for entry in entries]
        if snapshot.track is None or snapshot.track.id not in ids:
            self.stop()
            self.store.replace_queue(ids, selected_copy_ids=[entry.selected_copy_id for entry in entries])
            self._failed_positions.clear()
            self._restore_queue()
            return
        position = ids.index(snapshot.track.id)
        self.store.replace_queue(
            ids, current_position=position,
            selected_copy_ids=[entry.selected_copy_id for entry in entries],
        )
        self.store.update_session(position_ns=snapshot.position_ns, playback_state=snapshot.state.value)
        self._failed_positions.clear()
        self._publish(queue_position=position, queue_length=len(ids))

    def refresh_metadata(self) -> None:
        """Refresh displayed metadata without reloading or moving the audio stream."""
        snapshot = self.snapshot
        if snapshot.track is None:
            return
        track = self.store.track(snapshot.track.id)
        copy = next(
            (item for item in self.store.copies_for_track(track.id)
             if snapshot.copy is not None and item.id == snapshot.copy.id),
            snapshot.copy,
        )
        self._publish(track=track, copy=copy, duration_ns=track.duration_ns)

    def replace_queue(self, track_ids: list[str], *, start: int = 0, play: bool = True) -> None:
        self._clear_pending_advance()
        self.stop()
        self.store.replace_queue(track_ids, current_position=start)
        self._failed_positions.clear()
        self._restore_queue()
        if play and self.snapshot.track is not None:
            self.play()

    def add_last(self, track_ids: list[str]) -> None:
        self.store.append_queue(track_ids)
        queue = self._queue()
        self._publish(queue_length=len(queue))
        if self.snapshot.track is None:
            self._restore_queue()

    def play_next(self, track_id: str) -> None:
        entries = self._queue()
        insertion = min(self.snapshot.queue_position + 1, len(entries))
        ids = [entry.track.id for entry in entries]
        copies = [entry.selected_copy_id for entry in entries]
        ids.insert(insertion, track_id)
        copies.insert(insertion, None)
        self.store.replace_queue(
            ids,
            current_position=self.snapshot.queue_position,
            selected_copy_ids=copies,
        )
        self._publish(queue_length=len(ids))

    def play(self) -> bool:
        snapshot = self.snapshot
        if snapshot.track is None:
            return False
        if snapshot.copy is None:
            self._publish(
                state=PlaybackState.ERROR,
                error="No reachable copy is available for this track.",
            )
            return False
        if snapshot.state is PlaybackState.PAUSED:
            self.engine.play()
            return True
        try:
            uri = self._resolve(snapshot.copy)
        except PlaybackUnavailable as error:
            self._recover_from_failure(str(error))
            return False
        self._clear_pending_advance()
        self._publish(state=PlaybackState.LOADING, error=None)
        self.engine.load(uri, position_ns=snapshot.position_ns, play=True)
        return True

    def pause(self) -> None:
        if self.snapshot.state in (PlaybackState.PLAYING, PlaybackState.LOADING):
            self.engine.pause()

    def toggle(self) -> None:
        if self.snapshot.state is PlaybackState.PLAYING:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._clear_pending_advance()
        self.engine.stop()
        self.store.update_session(position_ns=0, playback_state=PlaybackState.STOPPED.value)
        self._publish(state=PlaybackState.STOPPED, position_ns=0, error=None)

    def seek(self, position_ns: int) -> None:
        snapshot = self.snapshot
        if not snapshot.can_seek:
            return
        bounded = max(0, min(position_ns, snapshot.duration_ns))
        self.engine.seek(bounded)
        self.store.update_session(position_ns=bounded)
        self._publish(position_ns=bounded)

    def seek_relative(self, delta_ns: int) -> None:
        self.seek(self.snapshot.position_ns + delta_ns)

    def previous(self) -> bool:
        if self.snapshot.position_ns > 3_000_000_000:
            self.seek(0)
            return True
        return self._move(-1)

    def next(self) -> bool:
        return self._move(1)

    def _target_position(self, direction: int, length: int, current: int | None = None) -> int | None:
        """The queue position `direction` steps from `current` (the playing
        position by default), honoring shuffle and repeat-all. `current` is
        passed explicitly when looking ahead from a position that has been
        handed to the engine for a gapless transition but has not started
        playing yet."""
        if current is None:
            current = self.snapshot.queue_position
        if self.snapshot.shuffle and length > 1:
            if not self._shuffle_order or sorted(self._shuffle_order) != list(range(length)):
                others = [value for value in range(length) if value != current]
                random.SystemRandom().shuffle(others)
                self._shuffle_order = [current, *others]
            at = self._shuffle_order.index(current)
            target_at = at + direction
            if 0 <= target_at < len(self._shuffle_order):
                return self._shuffle_order[target_at]
            if self.snapshot.repeat is RepeatMode.ALL:
                return self._shuffle_order[target_at % len(self._shuffle_order)]
            return None
        target = current + direction
        if 0 <= target < length:
            return target
        if self.snapshot.repeat is RepeatMode.ALL and length:
            return target % length
        return None

    def _move(self, direction: int, *, resume: bool | None = None) -> bool:
        self._clear_pending_advance()
        queue = self._queue()
        target = self._target_position(direction, len(queue))
        if target is None:
            self.stop()
            return False
        entry = queue[target]
        selected = self.store.select_copy(entry.track.id, entry.selected_copy_id)
        # Recovery passes resume=True: the failed item was being played, even
        # though the published state now says ERROR.
        was_playing = (
            resume if resume is not None
            else self.snapshot.state in (PlaybackState.PLAYING, PlaybackState.LOADING)
        )
        self.store.update_session(current_position=target, position_ns=0)
        self._publish(
            track=entry.track,
            copy=selected,
            queue_position=target,
            queue_length=len(queue),
            position_ns=0,
            duration_ns=entry.track.duration_ns,
            state=PlaybackState.STOPPED,
            error=None,
        )
        if was_playing:
            if selected is None:
                self._failed_positions.add(target)
                self._publish(
                    state=PlaybackState.ERROR,
                    error=f"{entry.track.title} is unavailable; trying the next track.",
                )
                if len(self._failed_positions) < len(queue):
                    return self._move(direction, resume=True)
                self.engine.stop()
                return False
            return self.play()
        return selected is not None

    def set_shuffle(self, enabled: bool) -> None:
        self._shuffle_order.clear()
        self.store.update_session(shuffle=enabled)
        self._publish(shuffle=enabled)

    def set_repeat(self, repeat: RepeatMode) -> None:
        self.store.update_session(repeat=repeat)
        self._publish(repeat=repeat)

    def set_volume(self, volume: float) -> None:
        bounded = min(1.0, max(0.0, volume))
        self.engine.set_volume(bounded)
        self.store.update_session(volume=bounded)
        self._publish(volume=bounded)

    def switch_copy(self, copy_id: str) -> bool:
        snapshot = self.snapshot
        if snapshot.track is None:
            return False
        selected = self.store.select_copy(snapshot.track.id, copy_id)
        if selected is None or selected.id != copy_id:
            self._publish(error="That copy is not currently reachable.")
            return False
        try:
            uri = self._resolve(selected)
        except PlaybackUnavailable as error:
            self._publish(error=str(error))
            return False
        self._clear_pending_advance()
        self.store.set_queue_copy(snapshot.queue_position, copy_id)
        playing = snapshot.state in (PlaybackState.PLAYING, PlaybackState.LOADING)
        position = self.engine.position() if playing else snapshot.position_ns
        self._publish(copy=selected, position_ns=max(0, position), error=None)
        self.engine.load(uri, position_ns=max(0, position), play=playing)
        return True

    def _on_engine_event(self, event: str, value: object) -> None:
        if event == "state":
            state = PlaybackState(str(value))
            self.store.update_session(playback_state=state.value)
            self._publish(state=state, error=None if state is not PlaybackState.ERROR else self.snapshot.error)
        elif event == "position":
            position = max(0, int(value))
            self.store.update_session(position_ns=position)
            self._publish(position_ns=position)
        elif event == "duration":
            self._publish(duration_ns=max(0, int(value)))
        elif event == "eos":
            # A real end-of-stream with nothing gapless in flight: either
            # there was nothing to preload (end of queue, no repeat) or
            # preloading it failed (see _compute_next_uri) and we're falling
            # back to the ordinary stop/reload path.
            self._clear_pending_advance()
            if self.snapshot.repeat is RepeatMode.ONE:
                self.seek(0)
                self.play()
            else:
                self.next()
        elif event == "advanced":
            self._commit_advance()
        elif event == "error":
            self._recover_from_failure(redact_uris(str(value)))

    def _clear_pending_advance(self) -> None:
        with self._lock:
            self._handed_over.clear()

    def _peek_next_position(self) -> int | None:
        """The queue position that should follow the last track handed to
        the engine -- or the playing one when nothing is in flight --
        honoring repeat/shuffle. Mirrors next()'s own semantics (including
        the repeat-one restart) so a gapless hand-off and the eos fallback
        never disagree about what comes next."""
        with self._lock:
            base = self._handed_over[-1][0] if self._handed_over else self.snapshot.queue_position
        if self.snapshot.repeat is RepeatMode.ONE:
            return base
        return self._target_position(1, len(self._queue()), base)

    def _provide_next_uri(self) -> str | None:
        """Called by the engine -- from GStreamer's 'about-to-finish' signal,
        on GStreamer's own thread, not the main loop -- to learn what to
        preload for a gapless transition. Must never raise and must never
        block on real network I/O (resolving a Subsonic URI only signs a
        URL; the actual prebuffering happens inside the engine once it sets
        the new uri). A failure here is never fatal to the track currently
        playing: it just means this one boundary falls back to the ordinary
        gappy stop/reload-on-eos path, surfaced as a transient error."""
        try:
            return self._compute_next_uri()
        except Exception:  # pragma: no cover - defensive: never break current playback
            # Deliberately leaves any already handed-over tracks alone:
            # they are in flight inside GStreamer and still have to be
            # committed in order when their streams start.
            self._publish(error="Couldn't prepare the next track; it will start after a short pause.")
            return None

    def _compute_next_uri(self) -> str | None:
        with self._lock:
            target = self._peek_next_position()
            queue = self._queue()
            if target is None or not (0 <= target < len(queue)):
                return None
            entry = queue[target]
            copy = self.store.select_copy(entry.track.id, entry.selected_copy_id)
        if copy is None:
            self._publish(
                error=f"Couldn't prepare {entry.track.title} for gapless playback; "
                      f"it will start after a short pause."
            )
            return None
        try:
            uri = self._resolve(copy)
        except PlaybackUnavailable as error:
            self._publish(error=f"Couldn't prepare the next track: {error}")
            return None
        with self._lock:
            self._handed_over.append((target, entry, copy))
        return uri

    def _commit_advance(self) -> None:
        """The engine confirmed (via STREAM_START) that it actually switched
        to the preloaded URI. This -- not 'about-to-finish', which fires
        while the old track is still audibly playing, and not a timer --
        is what moves the queue position and republishes track/MPRIS
        metadata, so listeners never see the "next" track named before its
        audio has actually started."""
        # The streaming thread may ask for the following URI while this
        # main-loop callback persists the advance. Removing the pending
        # cursor and publishing its replacement must be one operation;
        # otherwise that thread can see the old position with no pending
        # cursor and preload the track that just started a second time.
        with self._lock:
            if not self._handed_over:
                return
            target, entry, copy = self._handed_over.pop(0)
            self._snapshot = replace(
                self._snapshot,
                track=entry.track,
                copy=copy,
                queue_position=target,
                queue_length=len(self._queue()),
                position_ns=0,
                duration_ns=entry.track.duration_ns,
                state=PlaybackState.PLAYING,
                error=None,
            )
            snapshot = self._snapshot
        self.store.update_session(current_position=target, position_ns=0)
        for listener in tuple(self._listeners):
            listener(snapshot)

    def _recover_from_failure(self, message: str) -> None:
        self._clear_pending_advance()
        failed = self.snapshot.queue_position
        self._failed_positions.add(failed)
        self._publish(state=PlaybackState.ERROR, error=message)
        queue = self._queue()
        target = self._target_position(1, len(queue))
        if len(self._failed_positions) >= len(queue) or target is None or target in self._failed_positions:
            # Stopping the pipeline emits STOPPED; retain the failure for UI/MPRIS.
            self.engine.stop()
            self.store.update_session(playback_state=PlaybackState.ERROR.value)
            self._publish(state=PlaybackState.ERROR, error=message)
            return
        self._move(1, resume=True)

    def close(self) -> None:
        position = self.engine.position()
        if position >= 0:
            self.store.update_session(position_ns=position)
        self.engine.close()


class GStreamerEngine:
    """GStreamer playbin with PipeWire output and bounded position updates."""

    def __init__(self, *, audio_sink: str | None = None) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import GLib, Gst

        self.GLib = GLib
        self.Gst = Gst
        Gst.init(None)
        self._handler: Callable[[str, object], None] = lambda *_args: None
        self._pipeline = Gst.ElementFactory.make("playbin3", "tide-player") or Gst.ElementFactory.make(
            "playbin", "tide-player"
        )
        if self._pipeline is None:
            raise RuntimeError("GStreamer playbin is unavailable")
        # Let playbin select the platform audio sink. On Luma this uses the
        # standard PulseAudio-compatible PipeWire endpoint. Forcing the generic
        # pipewiresink bypasses GstAudioBaseSink scheduling and can deadlock
        # resume/seek or underrun when another client changes the graph quantum.
        # `audio_sink` is a test-only escape hatch (e.g. "appsink" or
        # "fakesink") so the real gapless mechanics can be exercised on a
        # real pipeline without a working PipeWire/PulseAudio graph, or with
        # the decoded audio inspectable for a continuity proof; production
        # callers never pass it.
        self._audio_sink = None
        if audio_sink:
            sink = Gst.ElementFactory.make(audio_sink, "tide-test-sink")
            if sink is None:
                raise RuntimeError(f"GStreamer sink '{audio_sink}' is unavailable")
            self._pipeline.set_property("audio-sink", sink)
            self._audio_sink = sink
        self._pipeline.connect("source-setup", self._source_setup)
        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_message)
        self._pending_seek = 0
        self._should_play = False
        self._timer = 0
        # Gapless playback: playbin/playbin3 both fire 'about-to-finish' a
        # short time before the current stream ends, while it is still
        # playing. Setting the 'uri' property from that signal (without
        # touching pipeline state) is the documented gapless mechanism --
        # GStreamer queues the new URI internally, starts prebuffering it in
        # the background, and switches to it with no stop/restart gap.
        self._next_track_provider: Callable[[], str | None] = lambda: None
        self._handoff_lock = threading.RLock()
        self._advance_pending = 0
        self._initial_stream_start_pending = False
        self._pipeline.connect("about-to-finish", self._on_about_to_finish)

    def set_event_handler(self, handler: Callable[[str, object], None]) -> None:
        self._handler = handler

    def set_next_track_provider(self, provider: Callable[[], str | None]) -> None:
        self._next_track_provider = provider

    def _on_about_to_finish(self, _element: object) -> None:
        # Runs on GStreamer's streaming thread, not the main/GLib thread --
        # the provider must be fast and non-blocking (see
        # PlaybackController._provide_next_uri) and this handler must never
        # let an exception cross back into C.
        try:
            uri = self._next_track_provider()
        except Exception:
            uri = None
        if not uri:
            return
        # Several short tracks can be prebuffered before the main loop
        # processes their stream-start messages. Count every hand-off and
        # arm it before setting the URI can start the next streaming thread.
        with self._handoff_lock:
            self._advance_pending += 1
        self._pipeline.set_property("uri", uri)

    @staticmethod
    def _source_setup(_pipeline: object, source: object) -> None:
        # A remote stream URI authenticates in its query string. Refusing
        # redirects keeps that query from following the server to another
        # scheme or host; TLS verification stays on (souphttpsrc's default).
        if hasattr(source.props, "automatic_redirect"):
            source.set_property("automatic-redirect", False)
        if hasattr(source.props, "ssl_strict"):
            source.set_property("ssl-strict", True)
        if hasattr(source.props, "user_agent"):
            source.set_property("user-agent", "Tide/0.1 (Project Luma)")

    def load(self, uri: str, *, position_ns: int = 0, play: bool = False) -> None:
        with self._handoff_lock:
            self._advance_pending = 0
            self._initial_stream_start_pending = True
        self._pipeline.set_state(self.Gst.State.NULL)
        self._pipeline.set_property("uri", uri)
        self._pending_seek = max(0, position_ns)
        self._should_play = play
        self._handler("state", PlaybackState.LOADING.value)
        result = self._pipeline.set_state(self.Gst.State.PAUSED)
        if result is self.Gst.StateChangeReturn.FAILURE:
            self._handler("error", "The selected media could not be opened.")

    def play(self) -> None:
        self._should_play = True
        if self._pipeline.set_state(self.Gst.State.PLAYING) is self.Gst.StateChangeReturn.FAILURE:
            self._handler("error", "The audio output could not start.")

    def pause(self) -> None:
        self._should_play = False
        self._pipeline.set_state(self.Gst.State.PAUSED)

    def stop(self) -> None:
        self._should_play = False
        with self._handoff_lock:
            self._advance_pending = 0
            self._initial_stream_start_pending = False
        self._pipeline.set_state(self.Gst.State.NULL)
        self._stop_timer()
        self._handler("state", PlaybackState.STOPPED.value)

    def seek(self, position_ns: int) -> None:
        self._pipeline.seek_simple(
            self.Gst.Format.TIME,
            self.Gst.SeekFlags.FLUSH | self.Gst.SeekFlags.KEY_UNIT,
            max(0, position_ns),
        )

    def set_volume(self, volume: float) -> None:
        self._pipeline.set_property("volume", min(1.0, max(0.0, volume)))

    def position(self) -> int:
        success, value = self._pipeline.query_position(self.Gst.Format.TIME)
        return int(value) if success else -1

    def _start_timer(self) -> None:
        if not self._timer:
            self._timer = self.GLib.timeout_add(500, self._tick)

    def _stop_timer(self) -> None:
        if self._timer:
            self.GLib.source_remove(self._timer)
            self._timer = 0

    def _tick(self) -> bool:
        position = self.position()
        if position >= 0:
            self._handler("position", position)
        success, duration = self._pipeline.query_duration(self.Gst.Format.TIME)
        if success:
            self._handler("duration", int(duration))
        return True

    def _on_message(self, _bus: object, message: object) -> None:
        message_type = message.type
        if message_type is self.Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._stop_timer()
            self._handler("error", str(error.message))
        elif message_type is self.Gst.MessageType.EOS:
            self._stop_timer()
            self._handler("eos", None)
        elif message_type is self.Gst.MessageType.STREAM_START:
            # The initial stream-start is always the hard load's own
            # track, even if its streaming thread already prebuffered the
            # next URI before this main-loop callback ran. Later messages
            # consume prebuffered hand-offs in order, one per track.
            with self._handoff_lock:
                if self._initial_stream_start_pending:
                    self._initial_stream_start_pending = False
                    advance = False
                else:
                    advance = self._advance_pending > 0
                    if advance:
                        self._advance_pending -= 1
            if advance:
                self._handler("advanced", None)
        elif message_type is self.Gst.MessageType.ASYNC_DONE:
            if self._pending_seek:
                seek = self._pending_seek
                self._pending_seek = 0
                self.seek(seek)
            if self._should_play:
                self.play()
        elif message_type is self.Gst.MessageType.STATE_CHANGED and message.src == self._pipeline:
            _old, state, _pending = message.parse_state_changed()
            if state is self.Gst.State.PLAYING:
                self._start_timer()
                self._handler("state", PlaybackState.PLAYING.value)
            elif state is self.Gst.State.PAUSED:
                self._stop_timer()
                self._handler("state", PlaybackState.PAUSED.value)
            elif state is self.Gst.State.NULL:
                self._stop_timer()

    def close(self) -> None:
        self._stop_timer()
        self._bus.remove_signal_watch()
        self._pipeline.set_state(self.Gst.State.NULL)
