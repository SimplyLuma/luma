# SPDX-License-Identifier: Apache-2.0
"""Read-only audio preview using GStreamer's supported native playbin API.

Keep the pipeline scoped to the open preview. Gtk.MediaFile's GstPlay adapter
aborts on some valid ID3-tagged streams with this GTK/GStreamer combination;
the direct pipeline handles those streams without changing global settings.
"""
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, GObject, Gst


class AudioPreview(GObject.Object):
    __gsignals__ = {'changed': (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, file):
        super().__init__()
        Gst.init(None)
        self._file = file
        self._error = None
        self._prepared = False
        self._playing = False
        self._ended = False
        self._duration = 0
        self._timestamp = 0
        self._tick_id = 0
        self._pipeline = Gst.ElementFactory.make('playbin', 'viewer-audio-preview')
        if self._pipeline is None:
            self._error = GLib.Error.new_literal(Gio.io_error_quark(), 'Audio playback is unavailable', Gio.IOErrorEnum.NOT_SUPPORTED)
            return
        # Audio only; no video decoder or renderer is started by a file preview.
        self._pipeline.set_property('flags', 2)
        self._pipeline.set_property('uri', file.get_uri())
        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus_handler = self._bus.connect('message', self._message)
        if self._pipeline.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE:
            self._fail('This audio file could not be opened')

    def _message(self, _bus, message):
        if self._file is None:
            return
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._fail(error.message)
        elif message.type == Gst.MessageType.ASYNC_DONE:
            self._prepared = True
            self._refresh()
        elif message.type == Gst.MessageType.DURATION_CHANGED:
            self._refresh()
        elif message.type == Gst.MessageType.EOS:
            self.set_playing(False)
            self._ended = True
            self.emit('changed')
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self._pipeline:
            _old, state, _pending = message.parse_state_changed()
            self._playing = state == Gst.State.PLAYING
            self._refresh()

    def _fail(self, message):
        self._error = GLib.Error.new_literal(Gio.io_error_quark(), message, Gio.IOErrorEnum.FAILED)
        self._stop_tick()
        self._playing = False
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
        self.emit('changed')

    def _refresh(self):
        if self._pipeline is None or self._file is None:
            return False
        valid, value = self._pipeline.query_duration(Gst.Format.TIME)
        if valid and value >= 0:
            self._duration = value // 1000
        valid, value = self._pipeline.query_position(Gst.Format.TIME)
        if valid and value >= 0:
            self._timestamp = value // 1000
        self.emit('changed')
        return self._playing and self._file is not None

    def _tick(self):
        keep = self._refresh()
        if not keep:
            self._tick_id = 0
        return keep

    def _stop_tick(self):
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def set_playing(self, playing):
        if self._file is None or self._pipeline is None or self._error:
            return
        if playing and self._ended:
            self.seek(0)
        self._ended = False
        self._playing = bool(playing)
        self._pipeline.set_state(Gst.State.PLAYING if playing else Gst.State.PAUSED)
        if playing and not self._tick_id:
            self._tick_id = GLib.timeout_add(100, self._tick)
        elif not playing:
            self._stop_tick()
        self._refresh()

    def seek(self, timestamp):
        if self._file is not None and self._pipeline is not None:
            self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, max(0, int(timestamp)) * 1000)

    def set_volume(self, volume):
        if self._pipeline is not None:
            self._pipeline.set_property('volume', min(1.0, max(0.0, volume)))

    def clear(self):
        self._file = None
        self._playing = False
        self._prepared = False
        self._stop_tick()
        if self._pipeline is not None:
            self._bus.disconnect(self._bus_handler)
            self._bus.remove_signal_watch()
            self._pipeline.set_state(Gst.State.NULL)
            self._bus = None
            self._pipeline = None

    def is_prepared(self):
        return self._prepared

    def has_audio(self):
        return self._pipeline is not None and self._pipeline.get_property('n-audio') > 0

    def get_error(self):
        return self._error

    def get_duration(self):
        return self._duration

    def get_timestamp(self):
        return self._timestamp

    def get_playing(self):
        return self._playing

    def get_file(self):
        return self._file
