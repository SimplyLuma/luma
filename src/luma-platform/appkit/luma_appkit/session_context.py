# SPDX-License-Identifier: Apache-2.0
"""One asynchronous session context subscription per native application."""
import weakref

from gi.repository import Gio

from .context import AppContext, InputMode, PresentationMode, context_from_session, _set_session_context

_broker = None


def _apply_window_context(window, context):
    """Apply the shared window frame policy without replacing app content."""
    window.context = context
    decorated = context.presentation is PresentationMode.WINDOWED
    window.phone_device = not decorated
    window.set_decorated(decorated)
    window.title_bar.set_visible(decorated)
    window.title_bar.set_can_target(decorated)
    window.identity.set_visible(decorated)
    if window.window_controls is not None:
        window.window_controls.set_visible(decorated)
    window._toolbar_view.set_extend_content_to_top_edge(not decorated)
    for name, enabled in (("lumaui-phone-device", not decorated),
                          ("lumaui-tablet-mode", context.device_class == "tablet"),
                          ("luma-touch", context.input_mode is InputMode.TOUCH)):
        (window.add_css_class if enabled else window.remove_css_class)(name)


class SessionContext:
    def __init__(self):
        self.context = AppContext.from_environment()
        self._listeners = []
        self._proxy = None
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
            "org.project_luma.ShellState1", "/org/project_luma/ShellState1",
            "org.project_luma.ShellState1", None, self._ready)

    def watch(self, callback):
        reference = weakref.WeakMethod(callback)
        self._listeners.append(reference)
        callback(self.context)
        def unsubscribe():
            if reference in self._listeners:
                self._listeners.remove(reference)
        return unsubscribe

    def _ready(self, _source, result):
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except Exception:
            return  # Standalone apps keep their explicit environment context.
        self._proxy.connect("g-properties-changed", self._changed)
        self._changed()

    def _changed(self, *_args):
        mode = self._proxy.get_cached_property("Presentation")
        capabilities = self._proxy.get_cached_property("InputCapabilities")
        if mode is None:
            return
        context = context_from_session(self.context, mode.unpack(),
            capabilities.unpack() if capabilities is not None else {})
        if context == self.context:
            return
        self.context = context
        _set_session_context(context)
        self._listeners = [item for item in self._listeners if item() is not None]
        for item in self._listeners:
            callback = item()
            if callback is not None:
                callback(context)


def session_context():
    global _broker
    if _broker is None:
        _broker = SessionContext()
    return _broker
