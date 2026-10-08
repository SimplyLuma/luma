# SPDX-License-Identifier: MPL-2.0
"""Finding AirPlay receivers through Avahi's D-Bus interface.

Two kinds of looking, both released as soon as nobody needs them:

* Browsing lists every _raop._tcp service on the network. It runs only while
  a person has the AirPlay picker open (``set_browsing``).
* Watching follows the SRV record of each receiver the person chose before,
  by name (``set_watched``). That asks the network about those receivers only,
  never lists the others, and tells us when one comes back or goes away.

Every service instance is kept per interface and protocol, the way Avahi
reports it; a receiver exists while any instance does. Addresses are resolved
IPv4 first, because module-raop-sink's RTP timing is most reliable there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from typing import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .raop import SERVICE_TYPE, Receiver, dns_escape_instance, parse_service_name, txt_to_dict  # noqa: E402

__all__ = ("AirPlayDiscovery",)

log = logging.getLogger("luma-audio-devices")

AVAHI = "org.freedesktop.Avahi"
SERVER = "org.freedesktop.Avahi.Server2"
PROTO_UNSPEC, PROTO_INET, PROTO_INET6 = -1, 0, 1
IF_UNSPEC = -1
LOOKUP_RESULT_LOCAL = 8
DNS_CLASS_IN, DNS_TYPE_SRV = 1, 33
SERVER_RUNNING = 2
RESOLVE_TIMEOUT_MS = 10000


@dataclass
class _Tracked:
    receivers: dict[tuple[int, int], Receiver] = field(default_factory=dict)
    sources: dict[tuple[int, int], set[str]] = field(default_factory=dict)


class AirPlayDiscovery:
    def __init__(self, on_changed: Callable[[], None]) -> None:
        self._on_changed = on_changed
        self._bus: Gio.DBusConnection | None = None
        self._watch_id = 0
        self.available = False
        self._browsing = False
        self._browser_path: str | None = None
        self._browser_signal = 0
        self._watched: dict[str, str] = {}                 # receiver id -> service name
        self._record_browsers: dict[str, tuple[str, int]] = {}  # receiver id -> (path, signal)
        self._tracked: dict[str, _Tracked] = {}           # receiver id -> instances
        self._include_local = os.environ.get("LUMA_AUDIO_DEVICES_AIRPLAY_LOCAL") == "1"

    # Lifecycle --------------------------------------------------------------

    def start(self) -> None:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as error:
            log.info("no system bus, so no AirPlay: %s", error.message)
            return
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._bus, AVAHI, Gio.BusNameWatcherFlags.NONE, self._on_avahi_appeared, self._on_avahi_vanished)

    def _on_avahi_appeared(self, connection, _name, _owner) -> None:
        self.available = True
        log.info("Avahi is available")
        self._sync()
        self._on_changed()

    def _on_avahi_vanished(self, _connection, _name) -> None:
        was_available = self.available
        self.available = False
        self._browser_path = None
        if self._browser_signal and self._bus is not None:
            self._bus.signal_unsubscribe(self._browser_signal)
        self._browser_signal = 0
        for path, signal in self._record_browsers.values():
            if self._bus is not None:
                self._bus.signal_unsubscribe(signal)
        self._record_browsers.clear()
        self._tracked.clear()
        if was_available:
            self._on_changed()

    # What to look for -------------------------------------------------------

    def set_browsing(self, browsing: bool) -> None:
        if browsing == self._browsing:
            return
        self._browsing = browsing
        if not browsing:
            self._drop_source("browse")
        self._sync()

    @property
    def browsing(self) -> bool:
        return self._browsing

    def set_watched(self, watched: dict[str, str]) -> None:
        """Receiver id -> DNS-SD service name for every remembered receiver."""
        self._watched = dict(watched)
        for receiver_id in list(self._record_browsers):
            if self._watched.get(receiver_id) is None:
                self._free_record_browser(receiver_id)
                self._drop_source("watch", receiver_id)
        self._sync()

    # Results ----------------------------------------------------------------

    def receivers(self) -> dict[str, Receiver]:
        result: dict[str, Receiver] = {}
        for receiver_id, tracked in self._tracked.items():
            if not tracked.receivers:
                continue
            candidates = sorted(tracked.receivers.values(),
                                key=lambda r: (":" in r.address, r.interface))
            result[receiver_id] = candidates[0]
        return result

    # Internals --------------------------------------------------------------

    def _sync(self) -> None:
        if not self.available or self._bus is None:
            return
        if self._browsing and self._browser_path is None:
            self._bus.call(AVAHI, "/", SERVER, "ServiceBrowserPrepare",
                           GLib.Variant("(iissu)", (IF_UNSPEC, PROTO_UNSPEC, SERVICE_TYPE, "", 0)),
                           GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, -1, None,
                           self._on_browser_prepared)
        elif not self._browsing and self._browser_path is not None:
            path, self._browser_path = self._browser_path, None
            if self._browser_signal:
                self._bus.signal_unsubscribe(self._browser_signal)
                self._browser_signal = 0
            self._call_quietly(path, "org.freedesktop.Avahi.ServiceBrowser", "Free")
        for receiver_id, service_name in self._watched.items():
            if receiver_id not in self._record_browsers:
                self._record_browsers[receiver_id] = ("", 0)
                record = f"{dns_escape_instance(service_name)}.{SERVICE_TYPE}.local"
                self._bus.call(AVAHI, "/", SERVER, "RecordBrowserPrepare",
                               GLib.Variant("(iisqqu)", (IF_UNSPEC, PROTO_UNSPEC, record,
                                                         DNS_CLASS_IN, DNS_TYPE_SRV, 0)),
                               GLib.VariantType.new("(o)"), Gio.DBusCallFlags.NONE, -1, None,
                               self._on_record_browser_prepared, (receiver_id, service_name))

    def _call_quietly(self, path: str, interface: str, method: str) -> None:
        if self._bus is None:
            return
        self._bus.call(AVAHI, path, interface, method, None, None, Gio.DBusCallFlags.NONE, -1, None,
                       lambda connection, result: _finish_quietly(connection, result))

    def _on_browser_prepared(self, connection, result) -> None:
        try:
            path = connection.call_finish(result).unpack()[0]
        except GLib.Error as error:
            log.warning("cannot browse for AirPlay receivers: %s", error.message)
            return
        if not self._browsing:
            self._call_quietly(path, "org.freedesktop.Avahi.ServiceBrowser", "Free")
            return
        self._browser_path = path
        self._browser_signal = connection.signal_subscribe(
            AVAHI, "org.freedesktop.Avahi.ServiceBrowser", None, path, None,
            Gio.DBusSignalFlags.NONE, self._on_browser_signal)
        self._call_quietly(path, "org.freedesktop.Avahi.ServiceBrowser", "Start")

    def _on_browser_signal(self, _connection, _sender, _path, _interface, signal, parameters) -> None:
        if signal == "ItemNew":
            interface, protocol, name, _type, domain, flags = parameters.unpack()
            if flags & LOOKUP_RESULT_LOCAL and not self._include_local:
                return
            self._resolve(interface, protocol, name, domain, "browse")
        elif signal == "ItemRemove":
            interface, protocol, name, _type, _domain, _flags = parameters.unpack()
            self._remove_instance(parse_service_name(name)[0], (interface, protocol), "browse")
        elif signal == "Failure":
            log.warning("AirPlay browsing failed: %s", parameters.unpack()[0])

    def _on_record_browser_prepared(self, connection, result, data) -> None:
        receiver_id, service_name = data
        try:
            path = connection.call_finish(result).unpack()[0]
        except GLib.Error as error:
            log.warning("cannot watch AirPlay receiver %s: %s", receiver_id, error.message)
            self._record_browsers.pop(receiver_id, None)
            return
        if self._watched.get(receiver_id) != service_name:
            self._call_quietly(path, "org.freedesktop.Avahi.RecordBrowser", "Free")
            if self._record_browsers.get(receiver_id) == ("", 0):
                self._record_browsers.pop(receiver_id, None)
            return

        def on_signal(_c, _sender, _path, _interface, signal, parameters) -> None:
            if signal == "ItemNew":
                interface, protocol, _name, _clazz, _type, _rdata, flags = parameters.unpack()
                if flags & LOOKUP_RESULT_LOCAL and not self._include_local:
                    return
                self._resolve(interface, protocol, service_name, "local", "watch")
            elif signal == "ItemRemove":
                interface, protocol, *_rest = parameters.unpack()
                self._remove_instance(receiver_id, (interface, protocol), "watch")

        signal = connection.signal_subscribe(AVAHI, "org.freedesktop.Avahi.RecordBrowser", None, path, None,
                                             Gio.DBusSignalFlags.NONE, on_signal)
        self._record_browsers[receiver_id] = (path, signal)
        self._call_quietly(path, "org.freedesktop.Avahi.RecordBrowser", "Start")

    def _free_record_browser(self, receiver_id: str) -> None:
        path, signal = self._record_browsers.pop(receiver_id, ("", 0))
        if self._bus is not None and signal:
            self._bus.signal_unsubscribe(signal)
        if path:
            self._call_quietly(path, "org.freedesktop.Avahi.RecordBrowser", "Free")

    def _resolve(self, interface: int, protocol: int, name: str, domain: str, source: str,
                 address_protocol: int = PROTO_INET) -> None:
        if self._bus is None:
            return
        self._bus.call(AVAHI, "/", SERVER, "ResolveService",
                       GLib.Variant("(iisssiu)", (interface, protocol, name, SERVICE_TYPE, domain or "local",
                                                  address_protocol, 0)),
                       GLib.VariantType.new("(iissssisqaayu)"), Gio.DBusCallFlags.NONE, RESOLVE_TIMEOUT_MS,
                       None, self._on_resolved, (interface, protocol, name, domain, source, address_protocol))

    def _on_resolved(self, connection, result, data) -> None:
        interface, protocol, name, domain, source, address_protocol = data
        try:
            (r_interface, r_protocol, r_name, _type, _domain, host, _aprotocol, address, port,
             txt, flags) = connection.call_finish(result).unpack()
        except GLib.Error as error:
            if address_protocol == PROTO_INET:
                # An IPv6-only receiver.
                self._resolve(interface, protocol, name, domain, source, PROTO_UNSPEC)
            else:
                log.info("could not resolve AirPlay receiver %s: %s", name, error.message)
            return
        if source == "browse" and not self._browsing:
            return
        receiver = Receiver(service_name=r_name, host_name=host, address=address, port=port,
                            interface=r_interface, txt=txt_to_dict(txt),
                            local=bool(flags & LOOKUP_RESULT_LOCAL))
        tracked = self._tracked.setdefault(receiver.id, _Tracked())
        slot = (interface, protocol)
        before = tracked.receivers.get(slot)
        tracked.receivers[slot] = receiver
        tracked.sources.setdefault(slot, set()).add(source)
        if before != receiver:
            self._on_changed()

    def _remove_instance(self, receiver_id: str, slot: tuple[int, int], source: str) -> None:
        tracked = self._tracked.get(receiver_id)
        if tracked is None or slot not in tracked.sources:
            return
        tracked.sources[slot].discard(source)
        if tracked.sources[slot]:
            return
        tracked.sources.pop(slot, None)
        tracked.receivers.pop(slot, None)
        if not tracked.receivers:
            self._tracked.pop(receiver_id, None)
        self._on_changed()

    def _drop_source(self, source: str, receiver_id: str | None = None) -> None:
        changed = False
        for rid in [receiver_id] if receiver_id else list(self._tracked):
            tracked = self._tracked.get(rid)
            if tracked is None:
                continue
            for slot in list(tracked.sources):
                tracked.sources[slot].discard(source)
                if not tracked.sources[slot]:
                    tracked.sources.pop(slot)
                    tracked.receivers.pop(slot, None)
                    changed = True
            if not tracked.receivers:
                self._tracked.pop(rid, None)
        if changed:
            self._on_changed()


def _finish_quietly(connection, result) -> None:
    try:
        connection.call_finish(result)
    except GLib.Error as error:
        log.debug("Avahi call failed: %s", error.message)
