# SPDX-License-Identifier: Apache-2.0
"""One-shot location through the system's permission portal.

No GeoClue bypass, persistent monitor, background position collection or disk
history. A portal fix is not consumed until the portal confirms permission.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LocationFix:
    latitude: float
    longitude: float
    accuracy: float

    @classmethod
    def valid(cls, latitude, longitude, accuracy):
        try:
            values = tuple(float(value) for value in (latitude, longitude, accuracy))
        except (TypeError, ValueError):
            return None
        lat, lon, precision = values
        if not all(math.isfinite(value) for value in values):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and precision >= 0):
            return None
        return cls(lat, lon, precision)

    @property
    def zoom(self):
        # A city-level permission must not masquerade as a street-level fix.
        return max(3.0, min(15.0, math.log2(40075016 / max(200.0, self.accuracy * 4))))


def portal_backend(window):
    import gi
    gi.require_version('Xdp', '1.0')
    gi.require_version('XdpGtk4', '1.0')
    from gi.repository import Gio, GLib, Xdp, XdpGtk4
    portal = Xdp.Portal.initable_new()
    parent = XdpGtk4.parent_new_gtk(window)
    return portal, parent, Gio.Cancellable(), GLib, Xdp.LocationAccuracy.EXACT, Xdp.LocationMonitorFlags.NONE


class LocationRequest:
    def __init__(self, window, on_fix, on_error, backend=portal_backend):
        self.window, self.on_fix, self.on_error = window, on_fix, on_error
        self.backend = backend
        self.active = False
        self.granted = False
        self.pending = None
        self.timer = 0
        self.portal = None
        self.cancellable = None
        self.signal = 0

    def start(self):
        if self.active:
            return
        self.active = True
        try:
            self.portal, self.parent, self.cancellable, self.loop, accuracy, flags = self.backend(self.window)
            self.signal = self.portal.connect('location-updated', self._updated)
            self.portal.location_monitor_start(self.parent, 0, 0, accuracy, flags,
                                               self.cancellable, self._started)
        except Exception:
            self._fail('Location is unavailable. You can still search and browse the map.')

    def _started(self, portal, result, *_data):
        # Finish even a cancelled asynchronous operation; do not revive it.
        try:
            granted = portal.location_monitor_start_finish(result)
        except Exception:
            granted = False
        if not self.active:
            return
        if not granted:
            self._fail('Location was not shared. You can still search and browse the map.')
            return
        self.granted = True
        if self.pending is not None:
            self._deliver(self.pending)
        else:
            # Never time out while the person is deciding on permission.
            self.timer = self.loop.timeout_add_seconds(30, self._timeout)

    def _updated(self, _portal, latitude, longitude, _altitude, accuracy,
                 _speed, _heading, _description, _timestamp_s, _timestamp_ms):
        if not self.active:
            return
        fix = LocationFix.valid(latitude, longitude, accuracy)
        if fix is None:
            return
        if self.granted:
            self._deliver(fix)
        else:
            self.pending = fix

    def _deliver(self, fix):
        self.close()
        self.on_fix(fix)

    def _timeout(self):
        self.timer = 0
        self._fail('This device could not find a location. You can still search and browse the map.')
        return False

    def _fail(self, message):
        self.close()
        self.on_error(message)

    def close(self):
        self.active = False
        if self.timer:
            self.loop.source_remove(self.timer)
            self.timer = 0
        if self.portal is not None:
            if self.signal:
                self.portal.disconnect(self.signal)
                self.signal = 0
            if self.cancellable is not None:
                self.cancellable.cancel()
            self.portal.location_monitor_stop()
        self.pending = None
