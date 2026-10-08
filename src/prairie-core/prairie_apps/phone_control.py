# SPDX-License-Identifier: Apache-2.0
"""Existing native/paired voice operations behind the Phone presentation.

Only service events set ACTIVE. Construction performs no I/O. The Phone
fixture never constructs this controller.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import time

from .phone_backend import (CallPhase, CallSession, CallStore, ImsVoiceTransport, NativeCall,
                            IncomingCallMonitor, PhoneCapability, classify_call_outcome,
                            inspect_phone_capability, preferred_voice_transport,
                            set_audio_input_muted, set_audio_output_route)
from .phone_fixture import digits
from .phone_data import back_up_call_log


_DISCOVER_PROVIDER = object()


class VoiceController:
    def __init__(self, *, dispatch, on_state, on_capability, on_error, is_blocked=None,
                 on_provider=lambda provider: None) -> None:
        self.dispatch, self._on_state = dispatch, on_state
        self.on_state = self._publish
        self.is_blocked = is_blocked
        self.on_provider = on_provider
        self._screening = self._screened = self._rejected = None
        self.on_capability, self.on_error = on_capability, on_error
        self.session = CallSession()
        self.provider = self.transport = self.monitor = self.ringer = None
        self.closed = self.pending = False
        self._audio_call = None
        self.audio_pending = False
        self._recorded = set()
        self._generation = 0
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="phone-voice")

    def _publish(self, session) -> None:
        key = (session.call_id, session.address)
        if self._rejected == key:
            if session.phase in {CallPhase.ENDED, CallPhase.FAILED}:
                self._rejected = self._screened = None
            return
        if (self.is_blocked is not None and session.phase is CallPhase.INCOMING
                and self._screened != key):
            if self._screening == key:
                return
            self._screening = key
            generation = self._generation
            def screened(blocked):
                if self._screening == key:
                    self._screening = None
                if (generation != self._generation or (self.session.call_id, self.session.address) != key
                        or self.session.phase is not CallPhase.INCOMING):
                    return
                self._screened = key
                if blocked and not self.pending:
                    self._rejected = key
                    if self.ringer:
                        self.ringer.stop()
                    self.control("decline")
                else:
                    if self.ringer:
                        self.ringer.start()
                    self._on_state(self.session)
            # A damaged policy must not silently hide an incoming call.
            self._task(lambda: self.is_blocked(session.address), screened,
                       on_failure=lambda _: screened(False))
            return
        self._on_state(session)

    def _task(self, operation, then, *, on_failure=None) -> None:
        if self.closed:
            return
        future = self.executor.submit(operation)
        def finished(result):
            def publish():
                if self.closed:
                    return False
                try:
                    value = result.result()
                except Exception as error:
                    if on_failure is not None:
                        on_failure(error)
                    self.on_error(str(error))
                    self.on_state(self.session)
                else:
                    then(value)
                return False
            self.dispatch(publish)
        future.add_done_callback(finished)

    def start(self, provider=_DISCOVER_PROVIDER) -> None:
        supplied = provider
        def discover():
            provider = None if supplied is _DISCOVER_PROVIDER else supplied
            if supplied is _DISCOVER_PROVIDER and importlib.util.find_spec("luma_continuity") is not None:
                from luma_continuity.call_provider import selected_provider
                provider = selected_provider(dispatch=self.dispatch)
            return provider, inspect_phone_capability() if provider is None else PhoneCapability(False, "Connecting to phone…")
        self._task(discover, self._discovered)

    def _discovered(self, found) -> None:
        self.provider, capability = found
        self.on_provider(self.provider)
        if self.provider is None:
            self.on_capability(capability)
        if self.provider is not None:
            from .phone_feedback import Ringer
            self.ringer = Ringer("org.projectluma.Phone")
            self.provider.start(self._paired_changed)
        elif not capability.no_modem:
            self.monitor = IncomingCallMonitor(on_state=self._native_changed, on_ended=self._native_ended,
                                              on_added=self._native_added)
            try:
                self.monitor.start()
            except Exception as error:
                self.on_error(str(error))
            generation = self._generation
            def restore():
                transport = ImsVoiceTransport()
                return transport, transport.calls(), generation
            self._task(restore, self._restore)

    def _restore(self, result) -> None:
        transport, calls, generation = result
        if calls and generation == self._generation and self.session.phase is CallPhase.IDLE:
            self.transport = transport
            self.session = CallSession.from_native(calls[0])
            self.on_state(self.session)

    def _native_added(self, call_id: str, direction: str, address: str) -> None:
        if self.closed:
            return
        if call_id == self.session.call_id:
            return
        if (self.session.phase in {CallPhase.IDLE, CallPhase.ENDED, CallPhase.FAILED}
                or (self.session.phase is CallPhase.PREPARING
                    and digits(address) == digits(self.session.address))):
            self._generation += 1
            self.session = CallSession.from_native(NativeCall(call_id, address, direction,
                CallPhase.INCOMING if direction == "incoming" else CallPhase.DIALLING, started_at=int(time.time())))
            self.on_state(self.session)

    def _native_changed(self, call_id: str, state: str, reason: str) -> None:
        if self.closed:
            return
        if call_id == self.session.call_id:
            self.session = self.session.native_state(state, reason)
            self.on_state(self.session)

    def _native_ended(self, call_id: str) -> None:
        if self.closed:
            return
        if call_id == self.session.call_id:
            self._generation += 1
            self.pending = False
            self._record()
            self.session = self.session.deleted()
            self.on_state(self.session)

    def _paired_changed(self, snapshot, ready) -> None:
        if self.closed:
            return
        available = ready and snapshot.get("voice_available") is True and self.provider.control_authorized()
        reason = ("Calls via your phone." if available else
                  "Your phone cannot make calls right now." if ready and snapshot.get("voice_available") is False else
                  "Phone connection unavailable.")
        self.on_capability(PhoneCapability(available, reason))
        calls = self.provider.calls() if ready else ()
        call = next((call for call in calls if call.phase not in {CallPhase.IDLE, CallPhase.ENDED, CallPhase.FAILED}), None)
        if call is not None:
            if call.call_id != self.session.call_id:
                self._generation += 1
            self.transport = self.provider
            self.session = CallSession.from_native(call)
            if call.phase is CallPhase.INCOMING:
                if self.is_blocked is None:
                    self.ringer.start()
            else:
                self.ringer.stop()
            self._start_requested_audio()
        # An accepted dial has no native ID until the paired phone reports it.
        # An empty snapshot before that event cannot establish a hangup.
        elif ready and self.session.call_id and self.session.phase not in {CallPhase.IDLE, CallPhase.ENDED}:
            self.ringer.stop()
            self._record()
            self._generation += 1
            self.pending = False
            self.session = self.session.deleted("remote-hangup")
        self.on_state(self.session)

    def _start_requested_audio(self) -> None:
        if (not self.provider or self._audio_call != self.session.call_id
                or self.session.phase is not CallPhase.ACTIVE):
            return
        provider, call_id = self.provider, self._audio_call
        # Consume explicit user intent before work; duplicate snapshots must
        # not request a second handoff for the same answered call.
        self._audio_call = None
        self.audio_pending = True
        def finished(_):
            self.audio_pending = False
        self._task(lambda: provider.start_audio(call_id), finished, on_failure=finished)

    def dial(self, address: str) -> None:
        if self.pending or self.session.phase not in {CallPhase.IDLE, CallPhase.ENDED, CallPhase.FAILED}:
            return
        try:
            preparing = CallSession.preparing(address)
        except ValueError as error:
            self.on_error(str(error))
            return
        self.pending = True
        self._generation += 1
        attempt = self._generation
        self.session = preparing
        self.on_state(self.session)
        def dial():
            transport = self.provider or preferred_voice_transport()
            return transport, transport.dial(address)
        def dialled(result):
            self.pending = False
            transport, call_id = result
            if self.session.phase in {CallPhase.ENDED, CallPhase.FAILED}:
                return
            if self._generation != attempt and self.session.call_id != call_id:
                return
            self.transport = transport
            if call_id:
                self._audio_call = call_id if self.provider else None
                # A service snapshot can arrive before the dial reply. Keep
                # its authoritative state rather than regressing to DIALLING.
                if self.session.call_id != call_id:
                    self.session = self.session.with_call_id(call_id)
                self.on_state(self.session)
        def failed(error):
            self.pending = False
            if self._generation == attempt and self.session.phase is CallPhase.PREPARING:
                self.session = self.session.deleted(str(error))
        self._task(dial, dialled, on_failure=failed)

    def control(self, operation: str) -> None:
        if self.pending or not self.session.call_id or self.session.input_locked:
            return
        self.pending = True
        call_id = self.session.call_id
        transport = self.transport
        def operate():
            return getattr(transport or ImsVoiceTransport(), operation)(call_id)
        def confirmed(_):
            self.pending = False
            if operation in {"hangup", "decline"} and self.session.call_id == call_id and self.session.phase not in {CallPhase.ENDED, CallPhase.FAILED}:
                self.session = self.session.ending()._replace(reason="local-hangup")
            if operation == "accept" and self.provider:
                self._audio_call = call_id
                self._start_requested_audio()
            if self.provider:
                self.provider.invalidate()
            self.on_state(self.session)
        def failed(_):
            self.pending = False
            if self._rejected == (self.session.call_id, self.session.address):
                self._rejected = None
                if self.ringer:
                    self.ringer.start()
        self._task(operate, confirmed, on_failure=failed)

    def dtmf(self, tone: str) -> None:
        if self.session.phase is CallPhase.ACTIVE and self.session.call_id:
            call_id = self.session.call_id
            transport = self.provider if self.provider is not None else self.transport
            def send():
                selected = transport if transport is not None else ImsVoiceTransport()
                operation = getattr(selected, "send_dtmf", None)
                if not callable(operation):
                    raise RuntimeError("This phone connection cannot send keypad tones.")
                return operation(call_id, tone)
            self._task(send, lambda _: None)

    def mute(self, active: bool, *, confirmed=lambda value: None) -> None:
        provider = self.provider
        def operation():
            return provider.mute_audio(active) if provider is not None else set_audio_input_muted(active)
        self._task(operation, lambda _: confirmed(active))

    def audio(self, route: str, *, confirmed=lambda value: None) -> None:
        provider, call_id = self.provider, self.session.call_id
        def operation():
            if provider:
                return provider.start_audio(call_id) if route == "speaker" else provider.stop_audio()
            return set_audio_output_route(route)
        self._task(operation, lambda _: confirmed(route))

    def _record(self) -> None:
        previous = self.session
        if not previous.call_id or previous.call_id in self._recorded:
            return
        self._recorded.add(previous.call_id)
        def record():
            # Same existing kind of write: one real call in Phone's call log.
            from .phone_shared_data import directory
            path = directory("prairie/phone") / "calls.db"
            back_up_call_log(path)
            store = CallStore(path)
            try:
                store.add(previous.address, direction=previous.direction, started=previous.started_at,
                          duration=previous.elapsed(), uid=previous.call_id,
                          outcome=classify_call_outcome(direction=previous.direction,
                                                       connected_at=previous.connected_at, reason=previous.reason))
            finally:
                store.close()
        self._task(record, lambda _: None)

    def close(self) -> None:
        self.closed = True
        if self.monitor:
            self.monitor.stop()
        if self.provider:
            self.provider.close()
        if self.ringer:
            self.ringer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
