# SPDX-License-Identifier: Apache-2.0
"""Shared native incoming-call feedback, owned by feedbackd."""
from gi.repository import Gio, GLib

class Ringer:
    """feedbackd ringtone + haptics, on the session bus.

    Absent or unavailable feedbackd is not an error: the call surface still
    appears, it is simply silent.
    """

    NAME = "org.sigxcpu.Feedback"
    PATH = "/org/sigxcpu/Feedback"
    IFACE = "org.sigxcpu.Feedback"

    def __init__(self, app_id="org.projectluma.PhoneDaemon") -> None:
        self.app_id = app_id
        self._proxy = None
        self._event_id = None
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                self.NAME, self.PATH, self.IFACE, None,
            )
        except GLib.Error:
            self._proxy = None

    def start(self) -> None:
        if self._proxy is None or self._event_id is not None:
            return
        try:
            result = self._proxy.call_sync(
                "TriggerFeedback",
                GLib.Variant("(ssa{sv}i)", (self.app_id, "phone-incoming-call", {}, 0)),
                Gio.DBusCallFlags.NONE, 5_000, None,
            )
            self._event_id = result.unpack()[0] if result is not None else None
        except GLib.Error:
            self._event_id = None

    def stop(self) -> None:
        if self._proxy is None or self._event_id is None:
            return
        try:
            self._proxy.call_sync(
                "EndFeedback", GLib.Variant("(u)", (self._event_id,)),
                Gio.DBusCallFlags.NONE, 5_000, None,
            )
        except GLib.Error:
            pass
        self._event_id = None


