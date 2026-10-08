# SPDX-License-Identifier: GPL-3.0-only
"""Native GTK contacts into Chromium's touch recognizer, with bounded motion.

The installed PyGObject cannot marshal boxed GdkTouchEvent. This small public
GDK/GObject ABI bridge reads events synchronously; it retains no event pointers.
Chrome owns touch-action, scrolling, pinch and tap recognition.
"""
import ctypes
from gi.repository import Gdk, GLib, Graphene, Gtk


class PageTouch:
    def __init__(self, owner):
        self.owner = owner
        self.contacts = {}
        self.ignored = set()
        self.identity = None
        self.enabled_session = None
        self.next_id = 0
        self.move = None
        self.timer = None
        self.inflight = 0
        self.controller = Gtk.EventControllerLegacy()
        self.controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        owner.page.add_controller(self.controller)
        self.gdk = ctypes.CDLL('libgtk-4.so.1')
        self.gobject = ctypes.CDLL('libgobject-2.0.so.0')
        self.gdk.gdk_event_get_event_type.argtypes = [ctypes.c_void_p]
        self.gdk.gdk_event_get_event_type.restype = ctypes.c_int
        self.gdk.gdk_event_get_event_sequence.argtypes = [ctypes.c_void_p]
        self.gdk.gdk_event_get_event_sequence.restype = ctypes.c_void_p
        self.gdk.gdk_event_get_position.argtypes = [ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)]
        self.gdk.gdk_event_get_position.restype = ctypes.c_int
        self.gdk.gdk_event_get_modifier_state.argtypes = [ctypes.c_void_p]
        self.gdk.gdk_event_get_modifier_state.restype = ctypes.c_uint
        capsule = ctypes.pythonapi.PyCapsule_GetPointer
        capsule.argtypes = [ctypes.py_object, ctypes.c_char_p]
        capsule.restype = ctypes.c_void_p
        self.pointer = capsule(self.controller.__gpointer__, None)
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p)
        self.callback = callback_type(self.event)
        self.gobject.g_signal_connect_data.argtypes = [ctypes.c_void_p,
            ctypes.c_char_p, callback_type, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        self.gobject.g_signal_connect_data.restype = ctypes.c_ulong
        self.handler = self.gobject.g_signal_connect_data(
            self.pointer, b'event', self.callback, None, None, 0)
        self.gobject.g_signal_handler_disconnect.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.gobject.g_signal_handler_disconnect.restype = None

    def event(self, _controller, event, _data):
        kinds = {int(Gdk.EventType.TOUCH_BEGIN): 'start',
                 int(Gdk.EventType.TOUCH_UPDATE): 'move',
                 int(Gdk.EventType.TOUCH_END): 'end',
                 int(Gdk.EventType.TOUCH_CANCEL): 'cancel'}
        kind = kinds.get(self.gdk.gdk_event_get_event_type(event))
        if kind is None:
            return False
        self.owner.metrics['native_touch_events'] = self.owner.metrics.get('native_touch_events', 0) + 1
        try:
            sequence = self.gdk.gdk_event_get_event_sequence(event)
            if kind in ('end', 'cancel'):
                self.feed(kind, sequence, 0, 0,
                          self.gdk.gdk_event_get_modifier_state(event))
                return True
            x, y = ctypes.c_double(), ctypes.c_double()
            if not self.gdk.gdk_event_get_position(event, ctypes.byref(x), ctypes.byref(y)):
                self.cancel()
                return True
            native = self.owner.page.get_native()
            offset_x, offset_y = native.get_surface_transform()
            ok, point = native.compute_point(self.owner.page,
                Graphene.Point().init(x.value - offset_x, y.value - offset_y))
            if ok:
                self.feed(kind, sequence, point.x, point.y,
                          self.gdk.gdk_event_get_modifier_state(event))
            else:
                self.cancel()
        except Exception as error:
            self.cancel()
            self.owner.on_error('Native touch input: ' + type(error).__name__)
        return True

    def current_identity(self):
        return (self.owner.session, self.owner.target, self.owner.tab_id)

    def snapshot(self, kind, state):
        from page_input import modifiers
        return dict(type=kind, touchPoints=[dict(p) for p in self.contacts.values()],
                    modifiers=modifiers(state))

    def feed(self, kind, sequence, x, y, state=0):
        if self.contacts and (self.identity != self.current_identity() or not self.owner.valid_identity()):
            self.cancel()
        if self.ignored:
            if kind == 'start':
                self.ignored.add(sequence)
            elif kind in ('end', 'cancel'):
                self.ignored.discard(sequence)
            return
        if kind == 'cancel':
            self.cancel()
            self.ignored.discard(sequence)
            return
        point = self.owner.point(x, y)
        if kind == 'start':
            if not point or not self.owner.valid_identity() or len(self.contacts) >= 10:
                self.cancel()
                self.ignored.add(sequence)
                return
            if not self.contacts:
                self.identity = self.current_identity()
                self.owner.page.grab_focus()
                if self.enabled_session != self.owner.session:
                    self.owner.dispatch('Emulation.setTouchEmulationEnabled',
                                        dict(enabled=True, maxTouchPoints=10))
                    self.enabled_session = self.owner.session
            self.flush(force=True)
            self.next_id += 1
            self.contacts[sequence] = dict(id=self.next_id, **point)
            self.owner.dispatch('Input.dispatchTouchEvent', self.snapshot('touchStart', state))
        elif sequence in self.contacts:
            previous = self.contacts[sequence]
            if kind == 'move' and point:
                getattr(self.owner, 'on_scroll', lambda *_: None)(
                    previous['x'] - point['x'], previous['y'] - point['y'])
                self.contacts[sequence] = dict(id=previous['id'], **point)
                self.move = (self.identity, self.snapshot('touchMove', state))
                if self.timer is None and self.inflight < 2:
                    self.timer = GLib.timeout_add(16, self.flush)
            elif kind == 'end':
                self.flush(force=True)
                del self.contacts[sequence]
                # CDP diffs the active contact list. A move with one remaining
                # point releases only the lifted finger; touchEnd ends them all.
                self.owner.dispatch('Input.dispatchTouchEvent',
                    self.snapshot('touchMove' if self.contacts else 'touchEnd', state))
                if not self.contacts:
                    self.identity = None
        self.owner.metrics['touch_events'] = self.owner.metrics.get('touch_events', 0) + 1

    def flush(self, *, force=False):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.move is None or (self.inflight >= 2 and not force):
            return GLib.SOURCE_REMOVE
        identity, params = self.move
        self.move = None
        if identity != self.current_identity() or not self.owner.valid_identity():
            return GLib.SOURCE_REMOVE
        self.inflight += 1
        if not self.owner.dispatch('Input.dispatchTouchEvent', params, on_settled=self.settled):
            self.inflight -= 1
        return GLib.SOURCE_REMOVE

    def settled(self):
        self.inflight = max(0, self.inflight - 1)
        if self.move is not None and self.timer is None:
            self.timer = GLib.timeout_add(16, self.flush)
        return GLib.SOURCE_REMOVE

    def cancel(self):
        self.move = None
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        if self.contacts:
            self.ignored.update(self.contacts)
            if self.identity == self.current_identity() and self.owner.valid_identity():
                self.owner.dispatch('Input.dispatchTouchEvent', dict(type='touchCancel', touchPoints=[]))
        self.contacts.clear()
        self.identity = None

    def close(self):
        self.cancel()
        self.gobject.g_signal_handler_disconnect(self.pointer, self.handler)


def touchscreen(controller):
    device = getattr(controller, 'get_current_event_device', lambda: None)()
    return bool(device and device.get_source() == Gdk.InputSource.TOUCHSCREEN)
