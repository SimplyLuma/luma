# SPDX-License-Identifier: Apache-2.0

"""Phone's background agent (ADR-033): calls ring with the Phone window closed.

``prairie-phone --agent`` listens for calls without a window:

* **A phone paired through Luma Connect** (the phone chosen for calls in
  ``~/.config/luma-connect/calls-phone.json``, reached over the Connect relay or
  Bluetooth hands-free). An incoming call rings and shows a call notification
  with Answer and Decline; answering opens Phone on the call, which owns audio
  and the in-call controls. A call that stops ringing unanswered becomes a
  Missed Call notification with Call Back and Message. Without a phone chosen
  for calls it only watches for one to be chosen, having loaded nothing of
  Luma Connect.
* **This device's own modem** (a handset's IMS service). The full-screen call
  surface above the lock screen belongs to prairie-phone-daemon, which is a
  GTK layer-shell process. When that service is not running, this agent hears
  the call first and starts it; while it runs, the agent leaves native calls to
  it. Handset services are not changed by this agent.

While the Phone window is open it rings and shows calls itself; the agent
stays quiet and withdraws its own notification.

Caller numbers and names are shown, never logged.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from .background_agent import (Agent, AgentInfo, Notifier, SystemdUser, configure_logging, launch_app,
                               name_has_owner)
from .phone_backend import CallPhase, format_phone_number

LOG = logging.getLogger("prairie.phone.agent")

APP_ID = "org.projectluma.Phone"
INFO = AgentInfo(
    APP_ID, "Phone", "communication", ("prairie-phone", "--agent"),
    wake=("login", "network", "resume"),
    publishes=("call-state", "missed-calls", "badge", "live-extension:org.projectluma.Phone.Call"),
    purpose="Get calls when Phone is closed",
)
NATIVE_PRESENTER = "prairie-phone-daemon.service"
RINGING = {CallPhase.INCOMING, CallPhase.WAITING}
LIVE = {CallPhase.CONNECTING, CallPhase.ACTIVE, CallPhase.HELD, CallPhase.MULTI_CALL}
FINISHED = {CallPhase.ENDED, CallPhase.FAILED, CallPhase.IDLE}


def selection_path() -> Path:
    return Path.home() / ".config/luma-connect/calls-phone.json"


def default_provider(dispatch):
    try:
        from luma_continuity.call_provider import selected_provider
    except ImportError:
        return None
    return selected_provider(dispatch=dispatch)


class Ring:
    """The ringtone: feedbackd where there is one (handsets), else the sound theme, looped."""

    def __init__(self) -> None:
        self._feedback = None
        self._sound = None
        self._cancel: Gio.Cancellable | None = None
        self._source = 0
        self.ringing = False

    def start(self) -> None:
        if self.ringing:
            return
        self.ringing = True
        try:
            from .phone_feedback import Ringer
            self._feedback = self._feedback or Ringer(APP_ID)
            self._feedback.start()
            if getattr(self._feedback, "_event_id", None) is not None:
                return
        except Exception:
            self._feedback = None
        self._play()

    def _play(self) -> bool:
        self._source = 0
        if not self.ringing:
            return GLib.SOURCE_REMOVE
        try:
            if self._sound is None:
                import gi
                gi.require_version("GSound", "1.0")
                from gi.repository import GSound
                self._sound = GSound.Context()
                self._sound.init()
            from gi.repository import GSound
            self._cancel = Gio.Cancellable()
            self._sound.play_full({GSound.ATTR_EVENT_ID: "phone-incoming-call", GSound.ATTR_MEDIA_ROLE: "alarm",
                                   GSound.ATTR_EVENT_DESCRIPTION: "Incoming call"}, self._cancel, self._played)
        except Exception as error:
            LOG.info("No ringtone available (%s)", type(error).__name__)
        return GLib.SOURCE_REMOVE

    def _played(self, source, result) -> None:
        try:
            source.play_full_finish(result)
        except GLib.Error:
            pass
        if self.ringing and not self._source:
            self._source = GLib.timeout_add(900, self._play)

    def stop(self) -> None:
        self.ringing = False
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
        if self._cancel is not None:
            self._cancel.cancel()
            self._cancel = None
        if self._feedback is not None:
            try:
                self._feedback.stop()
            except Exception:
                pass


class PhoneAgent:
    def __init__(self, *, provider_factory: Callable = default_provider, ring: Ring | None = None,
                 selection: Path | None = None, contacts: Callable[[], tuple] | None = None) -> None:
        self.provider_factory = provider_factory
        self.ring = ring or Ring()
        self.selection = selection or selection_path()
        self._contacts_loader = contacts
        self._contacts = None
        self.agent: Agent | None = None
        self.notifier: Notifier | None = None
        self.provider = None
        self.calls: dict[str, dict] = {}
        self.missed = 0
        self._monitor: Gio.FileMonitor | None = None
        self._window_watch = 0
        self._window_open = False
        self._native_monitor = None
        self._reload_source = 0
        self._selection_seen: bytes | None = None

    # -- lifecycle -------------------------------------------------------------
    def start(self, agent: Agent) -> None:
        self.agent = agent
        self.notifier = Notifier(agent.connection, APP_ID, "Phone")
        self._window_watch = Gio.bus_watch_name_on_connection(
            agent.connection, APP_ID, Gio.BusNameWatcherFlags.NONE,
            lambda *_args: self._window_changed(True), lambda *_args: self._window_changed(False))
        try:
            self.selection.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._monitor = Gio.File.new_for_path(str(self.selection.parent)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._monitor.connect("changed", self._selection_changed)
        except (OSError, GLib.Error):
            self._monitor = None
        self._start_native()
        self._reload_provider()
        self._publish()

    def wake(self, event: str) -> None:
        if event in {"network", "resume"} and self.provider is not None:
            # A relay or hands-free link held over sleep is re-observed, never trusted.
            self.provider.invalidate()

    def stop(self) -> None:
        self.ring.stop()
        if self._monitor is not None:
            self._monitor.cancel()
        if self._window_watch:
            Gio.bus_unwatch_name(self._window_watch)
        if self._native_monitor is not None:
            self._native_monitor.stop()
        if self.provider is not None:
            self.provider.close()
            self.provider = None
        if self.notifier is not None:
            self.notifier.close()

    # -- which phone -----------------------------------------------------------------
    def _selection_changed(self, _monitor, file, other, _event) -> None:
        names = {item.get_basename() for item in (file, other) if item is not None}
        if self.selection.name not in names:
            return
        if not self._reload_source:
            self._reload_source = GLib.timeout_add(300, self._selection_settled)

    def _selection_settled(self) -> bool:
        self._reload_source = 0
        try:
            current = self.selection.read_bytes() if self.selection.exists() else None
        except OSError:
            current = None
        if current != self._selection_seen:
            self._reload_provider()
        return GLib.SOURCE_REMOVE

    def _reload_provider(self) -> None:
        try:
            self._selection_seen = self.selection.read_bytes() if self.selection.exists() else None
        except OSError:
            self._selection_seen = None
        present = self._selection_seen is not None
        if self.provider is not None:
            self.provider.close()
            self.provider = None
            self._clear_calls()
        if present:
            try:
                self.provider = self.provider_factory(GLib.idle_add)
            except Exception as error:
                LOG.warning("The phone chosen for calls could not be loaded (%s)", type(error).__name__)
                self.provider = None
            if self.provider is not None:
                self.provider.start(self._snapshot)
        if self.agent is not None:
            self.agent.set_state("running" if self.provider is not None or self._native_monitor is not None else "idle",
                                 "Listening for calls" if self.provider is not None else "No phone chosen for calls")


    # -- this device's modem -------------------------------------------------------------
    def _start_native(self) -> None:
        """Hear a native call and hand it to the call surface service if that isn't running."""
        if self.agent is None or not _ims_present():
            return
        from .phone_backend import IncomingCallMonitor
        try:
            self._native_monitor = IncomingCallMonitor(on_incoming=self._native_incoming)
            self._native_monitor.start()
        except GLib.Error:
            self._native_monitor = None

    def _native_incoming(self, _call_id: str, _number: str) -> None:
        systemd = SystemdUser(self.agent.connection)
        if systemd.active(NATIVE_PRESENTER) or not systemd.unit_installed(NATIVE_PRESENTER):
            return
        # prairie-phone-daemon snapshots calls that are already ringing when it starts.
        LOG.info("Native incoming call; starting the call surface")
        systemd.start(NATIVE_PRESENTER)

    # -- paired phone calls ------------------------------------------------------------
    def _window_changed(self, present: bool) -> None:
        self._window_open = present
        if present:
            self.ring.stop()
            for key in [key for key in (self.notifier.keys() if self.notifier else []) if key.startswith("incoming:")]:
                self.notifier.withdraw(key)
        elif self.provider is not None:
            self.provider.invalidate()

    def _snapshot(self, _snapshot: dict, ready: bool) -> None:
        if self.provider is None:
            return
        calls = {call.call_id: call for call in self.provider.calls()} if ready else {}
        for call_id, call in calls.items():
            known = self.calls.get(call_id)
            if known is None:
                known = self.calls[call_id] = {"address": call.address, "direction": call.direction,
                                               "rang": False, "answered": False, "declined": False}
            if call.phase in RINGING and call.direction == "incoming":
                if not known["rang"]:
                    known["rang"] = True
                    self._incoming(call_id, call.address)
            elif call.phase in LIVE:
                known["answered"] = True
                self._stop_ringing(call_id)
            elif call.phase in FINISHED:
                self._ended(call_id)
        if ready:
            for call_id in [key for key in self.calls if key not in calls]:
                self._ended(call_id)
        self._publish()

    def _caller(self, address: str) -> str:
        if self._contacts is None:
            try:
                loader = self._contacts_loader
                if loader is None:
                    from .eds_backend import load_contacts as loader
                self._contacts = tuple(loader())
            except Exception:
                self._contacts = ()
        digits = "".join(c for c in address if c.isdigit())[-10:]
        for contact in self._contacts:
            phone = "".join(c for c in getattr(contact, "phone", "") if c.isdigit())[-10:]
            if digits and phone == digits:
                return contact.name
        return format_phone_number(address) or "Unknown caller"

    def _phone_label(self) -> str:
        try:
            label = json.loads(self._selection_seen or b"{}").get("phone", {}).get("label", "")
        except (ValueError, AttributeError):
            return ""
        return label[:64] if isinstance(label, str) else ""

    def _incoming(self, call_id: str, address: str) -> None:
        if self._window_open or self.notifier is None:
            return  # the Phone window rings and shows the call itself
        caller = self._caller(address)
        label = self._phone_label() or "your phone"
        self.ring.start()
        self.notifier.notify(
            f"incoming:{call_id}", caller, f"Incoming call on {label}",
            actions=[("default", "Open"), ("decline", "Decline"), ("answer", "Answer")],
            on_action=lambda action, call_id=call_id: self._action(call_id, action),
            category="call.incoming", urgency=2, resident=True, timeout=0,
            public_summary="Incoming call", public_body="", sound="phone-incoming-call")
        LOG.info("Incoming paired-phone call")

    def _action(self, call_id: str, action: str) -> None:
        if self.provider is None:
            return
        if action == "decline":
            self.calls.get(call_id, {})["declined"] = True
            self._control(self.provider.decline, call_id)
            self._stop_ringing(call_id)
        elif action in {"answer", "default"}:
            if action == "answer":
                self.calls.get(call_id, {})["answered"] = True
                self._control(self.provider.accept, call_id)
            self._stop_ringing(call_id)
            open_phone()

    def _control(self, method, call_id: str) -> None:
        def run():
            try:
                method(call_id)
            except Exception as error:
                LOG.info("Call control failed (%s)", type(error).__name__)
        threading.Thread(target=run, daemon=True, name="phone-agent-control").start()

    def _stop_ringing(self, call_id: str) -> None:
        if not any(info["rang"] and not info["answered"] and not info["declined"]
                   for key, info in self.calls.items() if key != call_id):
            self.ring.stop()
        if self.notifier is not None:
            self.notifier.withdraw(f"incoming:{call_id}")

    def _ended(self, call_id: str) -> None:
        info = self.calls.pop(call_id, None)
        self._stop_ringing(call_id)
        if info is None or not info["rang"] or info["answered"] or info["declined"] or self.notifier is None:
            return
        self.missed += 1
        address = info["address"]
        self.notifier.notify(
            f"missed:{call_id}", "Missed call", self._caller(address),
            actions=[("default", "Open"), ("message", "Message"), ("call-back", "Call Back")],
            on_action=lambda action, address=address: self._missed_action(address, action),
            category="call.unanswered", urgency=1, public_summary="Missed call", public_body="")

    def _missed_action(self, address: str, action: str) -> None:
        # Acting on a missed call means the person has seen it: the dock badge
        # counts only calls nobody has looked at yet.
        if self.missed:
            self.missed = 0
            self._publish()
        if action == "call-back":
            launch_uri(f"tel:{address}")
        elif action == "message":
            launch_uri(f"sms:{address}")
        else:
            open_phone()

    def _clear_calls(self) -> None:
        self.ring.stop()
        for call_id in list(self.calls):
            if self.notifier is not None:
                self.notifier.withdraw(f"incoming:{call_id}")
        self.calls.clear()

    def _publish(self) -> None:
        if self.agent is None:
            return
        ringing = any(info["rang"] and not info["answered"] and not info["declined"] for info in self.calls.values())
        active = any(info["answered"] for info in self.calls.values())
        self.agent.publish("call-state", "ringing" if ringing else "active" if active else "idle")
        self.agent.publish("missed-calls", self.missed)
        # The dock's unread badge: calls the person has not seen yet.
        self.agent.publish("badge", GLib.Variant("u", max(0, self.missed)))


def _ims_present() -> bool:
    """Whether this device has its own IMS call service (a handset)."""
    try:
        system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        from .phone_backend import ImsVoiceTransport
        if name_has_owner(system, ImsVoiceTransport.BUS_NAME):
            return True
        names = system.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "ListActivatableNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE,
                                 2000, None).unpack()[0]
        return ImsVoiceTransport.BUS_NAME in names
    except GLib.Error:
        return False


def open_phone() -> None:
    launch_app(APP_ID, ["prairie-phone"])


def launch_uri(uri: str) -> None:
    """tel: opens Phone and sms: opens Messages, each in its own scope."""
    scheme = uri.split(":", 1)[0]
    if scheme == "tel":
        launch_app(APP_ID, ["prairie-phone", uri])
    elif scheme == "sms":
        launch_app("org.projectluma.Messages", ["prairie-messages", uri])


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    phone = PhoneAgent()
    return Agent(INFO, start=phone.start, wake=phone.wake, stop=phone.stop).run()


__all__ = ["INFO", "PhoneAgent", "Ring", "main", "name_has_owner"]
