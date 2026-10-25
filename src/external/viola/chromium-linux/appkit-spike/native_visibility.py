# SPDX-License-Identifier: GPL-3.0-only
"""Forward native mapping/suspension, not keyboard focus, to page capture."""
from gi.repository import Gdk, GLib
from luma_appkit.session_activity import observe_session_activity


class NativeVisibility:
    def __init__(self, window, window_id, receiver):
        self.window, self.window_id, self.receiver = window, window_id, receiver
        self.surface = None
        self.sent = None
        self.has_mapped = False
        self.retry = None
        self.closed = False
        self.session_hidden = False
        self.signals = []
        for signal in ('map', 'unmap', 'realize'):
            self.signals.append((window, window.connect(signal, self.changed)))
        self.disconnect_activity = observe_session_activity(self.session_changed)
        self.changed()

    def session_changed(self, hidden):
        self.session_hidden = hidden
        self.changed()

    def changed(self, *_):
        if self.closed:
            return
        surface = self.window.get_surface()
        if surface is not None and surface is not self.surface:
            self.surface = surface
            self.signals.append((surface, surface.connect('notify::state', self.changed)))
        # Before the first native map, Chromium is still restoring/materializing
        # the selected page. Hiding that page can strand its initial capture.
        # Observe visibility only after this native window has actually mapped.
        mapped = self.window.get_mapped()
        self.has_mapped = self.has_mapped or mapped
        if not self.has_mapped:
            return
        suspended = Gdk.ToplevelState.MINIMIZED | getattr(Gdk.ToplevelState, 'SUSPENDED', 0)
        visible = bool(mapped and surface and not self.session_hidden and
                       not (surface.get_state() & suspended))
        if visible != self.sent:
            if self.receiver.set_visible(self.window_id, visible):
                self.sent = visible
            elif self.retry is None:
                self.retry = GLib.timeout_add(100, self.again)

    def again(self):
        self.retry = None
        self.changed()
        return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.disconnect_activity()
        if self.retry is not None:
            GLib.source_remove(self.retry)
            self.retry = None
        for owner, signal in self.signals:
            owner.disconnect(signal)
        self.signals.clear()
