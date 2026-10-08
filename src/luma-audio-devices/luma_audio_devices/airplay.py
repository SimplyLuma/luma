# SPDX-License-Identifier: MPL-2.0
"""Opt-in AirPlay: the picker, remembered receivers and their sinks.

The model is the one macOS uses. Receivers are listed only while the person
looks at the picker. Choosing one checks that it will accept a stream (asking
for a password if it has one), creates that one sink, makes it the output and
remembers the receiver. A remembered receiver's sink comes back by itself
whenever the receiver is on the network, but never takes the sound: the
WirePlumber policy lets a network output become the default only when the
person picks it while it is present. Forgetting removes the sink, the memory
and the stored password.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Callable

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from . import raop  # noqa: E402
from .classify import AIRPLAY_NODE_PREFIX  # noqa: E402
from .state import RememberedReceiver, Store  # noqa: E402

__all__ = ("AirPlayManager", "ConnectError", "ERROR_CODES")

log = logging.getLogger("luma-audio-devices")

BROWSE_LIMIT_SECONDS = 180
ABSENT_GRACE_MS = 20000          # mDNS flaps; do not tear a sink down at once
RETRY_AFTER_FAILURE_MS = 30000
NODE_APPEAR_TIMEOUT_MS = 6000

ERROR_CODES = {
    raop.PROBE_PASSWORD_REQUIRED: "PasswordRequired",
    raop.PROBE_PASSWORD_INCORRECT: "PasswordIncorrect",
    raop.PROBE_DENIED: "Denied",
    raop.PROBE_UNREACHABLE: "Unreachable",
    raop.PROBE_UNSUPPORTED: "Unsupported",
    "not-found": "NotFound",
    "failed": "Failed",
}


@dataclass
class ConnectError(Exception):
    code: str
    message: str


def _messages(name: str) -> dict[str, str]:
    return {
        raop.PROBE_PASSWORD_REQUIRED: f"“{name}” needs a password.",
        raop.PROBE_PASSWORD_INCORRECT: f"The password for “{name}” isn’t right.",
        raop.PROBE_DENIED: f"“{name}” accepts AirPlay only from its owner’s devices.",
        raop.PROBE_UNREACHABLE: f"“{name}” isn’t responding.",
        raop.PROBE_UNSUPPORTED: f"“{name}” can’t play sound from this computer.",
        "not-found": f"“{name}” isn’t on this network right now.",
        "failed": f"Sound couldn’t start on “{name}”.",
    }


class AirPlayManager:
    def __init__(self, store: Store, control, passwords, discovery,
                 on_changed: Callable[[], None], on_sink_failed: Callable[[str], None],
                 probe: Callable[..., raop.ProbeResult] = raop.probe) -> None:
        self.store = store
        self.control = control
        self.passwords = passwords
        self.discovery = discovery
        self._on_changed = on_changed
        self._on_sink_failed = on_sink_failed
        self._probe = probe
        self._browsers: dict[str, int] = {}     # D-Bus client -> expiry timeout id
        self._connecting: set[str] = set()
        self._errors: dict[str, str] = {}
        self._absent_timers: dict[str, int] = {}
        self._loaded_endpoint: dict[str, tuple[str, int, str]] = {}
        self._retry_timers: dict[str, int] = {}
        self._present_nodes: set[str] = set()
        self._waiters: dict[str, Callable[[bool], None]] = {}

    # Picker -----------------------------------------------------------------

    def start_browsing(self, client: str) -> None:
        old = self._browsers.pop(client, 0)
        if old:
            GLib.source_remove(old)
        self._browsers[client] = GLib.timeout_add_seconds(BROWSE_LIMIT_SECONDS, self._browse_expired, client)
        log.info("AirPlay picker opened by %s; looking for receivers", client)
        self.discovery.set_browsing(True)
        self._on_changed()

    def stop_browsing(self, client: str) -> None:
        timeout = self._browsers.pop(client, 0)
        if timeout:
            GLib.source_remove(timeout)
        if not self._browsers:
            log.info("AirPlay picker closed; no longer looking for receivers")
            self.discovery.set_browsing(False)
            self._errors = {k: v for k, v in self._errors.items() if k in self.store.receivers}
        self._on_changed()

    def _browse_expired(self, client: str) -> bool:
        self._browsers.pop(client, None)
        self.stop_browsing(client)
        return GLib.SOURCE_REMOVE

    @property
    def browsing(self) -> bool:
        return bool(self._browsers)

    def receivers(self) -> list[dict]:
        discovered = self.discovery.receivers()
        entries: dict[str, dict] = {}
        for receiver_id, receiver in discovered.items():
            if not self.browsing and receiver_id not in self.store.receivers:
                continue
            entries[receiver_id] = self._entry(receiver_id, receiver.name, receiver.model, True,
                                               receiver.announces_password, receiver.supported)
        for receiver_id, remembered in self.store.receivers.items():
            if receiver_id not in entries:
                entries[receiver_id] = self._entry(receiver_id, remembered.name, remembered.model, False,
                                                   remembered.has_password, True)
        return sorted(entries.values(), key=lambda e: (not e["remembered"], e["name"].casefold()))

    def _entry(self, receiver_id: str, name: str, model: str, present: bool,
               password: bool, supported: bool) -> dict:
        node_name = AIRPLAY_NODE_PREFIX + receiver_id
        if receiver_id in self._connecting:
            state = "connecting"
        elif node_name in self._present_nodes:
            state = "connected"
        elif present:
            state = "available"
        else:
            state = "unavailable"
        return {
            "id": receiver_id, "name": name, "model": model, "kind": raop.receiver_kind(model),
            "state": state, "remembered": receiver_id in self.store.receivers,
            "requires_password": bool(password), "supported": bool(supported),
            "node_name": node_name, "error": self._errors.get(receiver_id, ""),
        }

    # Choosing a receiver ----------------------------------------------------

    def connect(self, receiver_id: str, password: str | None,
                reply: Callable[[ConnectError | None], None]) -> None:
        receiver = self.discovery.receivers().get(receiver_id)
        name = receiver.name if receiver else self.store.receivers.get(
            receiver_id, RememberedReceiver(receiver_id, receiver_id, "")).name
        if receiver is None:
            reply(self._fail(receiver_id, "not-found", name))
            return
        if not receiver.supported:
            reply(self._fail(receiver_id, raop.PROBE_UNSUPPORTED, name))
            return
        if receiver_id in self._connecting:
            reply(ConnectError("Busy", f"Already connecting to “{name}”."))
            return
        self._connecting.add(receiver_id)
        self._errors.pop(receiver_id, None)
        self._on_changed()

        def with_password(stored: str | None) -> None:
            chosen = password or stored
            self._run_probe(receiver, chosen, lambda result: self._probed(receiver, chosen, password, result, reply))

        if password:
            with_password(None)
        else:
            self.passwords.lookup(receiver_id, with_password)

    def _run_probe(self, receiver: raop.Receiver, password: str | None,
                   done: Callable[[raop.ProbeResult], None]) -> None:
        def work() -> None:
            result = self._probe(receiver.connect_address(), receiver.port, password)
            GLib.idle_add(lambda: (done(result), GLib.SOURCE_REMOVE)[1])
        threading.Thread(target=work, name="airplay-probe", daemon=True).start()

    def _probed(self, receiver: raop.Receiver, password: str | None, typed: str | None,
                result: raop.ProbeResult, reply) -> None:
        receiver_id = receiver.id
        if result.status == raop.PROBE_PASSWORD_INCORRECT and not typed:
            # A stored password went stale: ask again rather than calling it wrong.
            self.passwords.clear(receiver_id)
            result = raop.ProbeResult(raop.PROBE_PASSWORD_REQUIRED)
        if result.status != raop.PROBE_OK:
            self._connecting.discard(receiver_id)
            log.info("AirPlay %s: %s %s", receiver_id, result.status, result.detail)
            reply(self._fail(receiver_id, result.status, receiver.name))
            return
        if not self._load(receiver, password):
            self._connecting.discard(receiver_id)
            reply(self._fail(receiver_id, "failed", receiver.name))
            return

        def appeared(ok: bool) -> None:
            self._connecting.discard(receiver_id)
            if not ok:
                self.control.unload(receiver_id)
                reply(self._fail(receiver_id, "failed", receiver.name))
                return
            now = time.time()
            remembered = self.store.receivers.get(receiver_id)
            if remembered is None:
                remembered = RememberedReceiver(receiver_id, receiver.name, receiver.service_name,
                                                receiver.model, remembered_at=now)
                self.store.receivers[receiver_id] = remembered
            remembered.name = receiver.name
            remembered.service_name = receiver.service_name
            remembered.model = receiver.model
            remembered.last_connected = now
            remembered.has_password = bool(password)
            self.store.save()
            if password:
                self.passwords.store(receiver_id, receiver.name, password)
            self.discovery.set_watched(self.watched())
            self.control.set_default_sink(receiver.node_name)
            self._errors.pop(receiver_id, None)
            self._on_changed()
            reply(None)

        self._wait_for_node(receiver.node_name, appeared)

    def _fail(self, receiver_id: str, status: str, name: str) -> ConnectError:
        self._errors[receiver_id] = status
        self._on_changed()
        return ConnectError(ERROR_CODES.get(status, "Failed"), _messages(name).get(status, _messages(name)["failed"]))

    def _wait_for_node(self, node_name: str, done: Callable[[bool], None]) -> None:
        if node_name in self._present_nodes:
            done(True)
            return
        timeout_id = 0

        def finish(ok: bool) -> None:
            nonlocal timeout_id
            if self._waiters.get(node_name) is not finish:
                return
            self._waiters.pop(node_name, None)
            if timeout_id:
                GLib.source_remove(timeout_id)
                timeout_id = 0
            done(ok)

        def expired() -> bool:
            nonlocal timeout_id
            timeout_id = 0
            finish(False)
            return GLib.SOURCE_REMOVE

        self._waiters[node_name] = finish
        timeout_id = GLib.timeout_add(NODE_APPEAR_TIMEOUT_MS, expired)

    def forget(self, receiver_id: str) -> bool:
        remembered = self.store.receivers.pop(receiver_id, None)
        self._cancel_timer(self._absent_timers, receiver_id)
        self._cancel_timer(self._retry_timers, receiver_id)
        self.control.unload(receiver_id)
        self._loaded_endpoint.pop(receiver_id, None)
        self._errors.pop(receiver_id, None)
        self.passwords.clear(receiver_id)
        if remembered is None:
            return False
        self.store.save()
        self.discovery.set_watched(self.watched())
        self._on_changed()
        return True

    def watched(self) -> dict[str, str]:
        return {rid: r.service_name for rid, r in self.store.receivers.items() if r.service_name}

    # Keeping remembered sinks in step with the network -----------------------

    def reconcile(self) -> None:
        discovered = self.discovery.receivers()
        for receiver_id, remembered in list(self.store.receivers.items()):
            receiver = discovered.get(receiver_id)
            if receiver is None:
                if self.control.loaded(receiver_id) and receiver_id not in self._absent_timers:
                    self._absent_timers[receiver_id] = GLib.timeout_add(
                        ABSENT_GRACE_MS, self._absent_expired, receiver_id)
                continue
            self._cancel_timer(self._absent_timers, receiver_id)
            if remembered.service_name != receiver.service_name or remembered.name != receiver.name:
                remembered.service_name = receiver.service_name
                remembered.name = receiver.name
                self.store.save()
                self.discovery.set_watched(self.watched())
            if receiver_id in self._connecting or receiver_id in self._retry_timers:
                continue
            endpoint = (receiver.connect_address(), receiver.port, receiver.service_name)
            if self.control.loaded(receiver_id) and self._loaded_endpoint.get(receiver_id) == endpoint:
                continue
            self._restore(receiver)
        self._on_changed()

    def _restore(self, receiver: raop.Receiver) -> None:
        remembered = self.store.receivers.get(receiver.id)
        if remembered is None:
            return

        def with_password(password: str | None) -> None:
            if remembered.has_password and not password:
                self._errors[receiver.id] = raop.PROBE_PASSWORD_REQUIRED
                self._on_changed()
                return
            if self._load(receiver, password):
                log.info("AirPlay receiver %s is back; its output is listed again", receiver.id)

        if remembered.has_password:
            self.passwords.lookup(receiver.id, with_password)
        else:
            with_password(None)

    def _load(self, receiver: raop.Receiver, password: str | None) -> bool:
        ok = self.control.load_raop_sink(receiver.id, raop.sink_module_args(receiver, password))
        if ok:
            self._loaded_endpoint[receiver.id] = (receiver.connect_address(), receiver.port,
                                                  receiver.service_name)
        return ok

    def _absent_expired(self, receiver_id: str) -> bool:
        self._absent_timers.pop(receiver_id, None)
        if receiver_id not in self.discovery.receivers():
            log.info("AirPlay receiver %s left the network; removing its output", receiver_id)
            self.control.unload(receiver_id)
            self._loaded_endpoint.pop(receiver_id, None)
            self._on_changed()
        return GLib.SOURCE_REMOVE

    def outputs_changed(self, node_names: set[str]) -> None:
        """The graph changed. Notice sinks that appeared, and sinks that
        module-raop-sink destroyed itself because the receiver refused or
        stopped answering while the person was listening."""
        before = self._present_nodes
        self._present_nodes = {n for n in node_names if n.startswith(AIRPLAY_NODE_PREFIX)}
        for node_name in self._present_nodes - before:
            waiter = self._waiters.get(node_name)
            if waiter is not None:
                waiter(True)
        for node_name in before - self._present_nodes:
            receiver_id = node_name.removeprefix(AIRPLAY_NODE_PREFIX)
            if self.control.loaded(receiver_id) and receiver_id not in self._connecting:
                if self.control.module_alive(receiver_id):
                    continue
                self.control.unload(receiver_id)
                self._loaded_endpoint.pop(receiver_id, None)
                remembered = self.store.receivers.get(receiver_id)
                name = remembered.name if remembered else receiver_id
                self._errors[receiver_id] = "failed"
                self._on_sink_failed(name)
                self._retry_timers[receiver_id] = GLib.timeout_add(
                    RETRY_AFTER_FAILURE_MS, self._retry_expired, receiver_id)
        if before != self._present_nodes:
            self._on_changed()

    def _retry_expired(self, receiver_id: str) -> bool:
        self._retry_timers.pop(receiver_id, None)
        self.reconcile()
        return GLib.SOURCE_REMOVE

    def pipewire_reconnected(self) -> None:
        self._loaded_endpoint.clear()
        self.reconcile()

    @staticmethod
    def _cancel_timer(timers: dict[str, int], key: str) -> None:
        source = timers.pop(key, 0)
        if source:
            GLib.source_remove(source)
