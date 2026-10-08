# SPDX-License-Identifier: GPL-3.0-only
"""Bridge native clipboard offers while Chromium retains copy/paste semantics."""
import fcntl
import os

MAX_BYTES = 16 * 1024 * 1024
MIME_TYPES = frozenset(('text/plain', 'text/plain;charset=utf-8', 'text/html',
                       'text/rtf', 'image/png', 'image/svg+xml'))


def decode_offer(metadata, descriptors):
    size = metadata.get('size')
    formats = metadata.get('formats')
    if (metadata.get('version') != 1 or len(descriptors) != 1
            or type(size) is not int or not 0 <= size <= MAX_BYTES
            or not isinstance(formats, list) or not 1 <= len(formats) <= len(MIME_TYPES)):
        raise ValueError('Invalid clipboard offer')
    descriptor = descriptors[0]
    seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    if os.fstat(descriptor).st_size != size or fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & seals != seals:
        raise ValueError('Clipboard payload must be sealed and correctly sized')
    result, position = {}, 0
    for item in formats:
        if not isinstance(item, dict):
            raise ValueError('Invalid clipboard format')
        mime, offset, length = item.get('mime'), item.get('offset'), item.get('size')
        if (not isinstance(mime, str) or mime not in MIME_TYPES or mime in result
                or type(offset) is not int or offset != position
                or type(length) is not int or not 0 <= length <= size - offset):
            raise ValueError('Invalid clipboard format bounds')
        value = os.pread(descriptor, length, offset)
        if len(value) != length:
            raise ValueError('Incomplete clipboard payload')
        result[mime] = value
        position += length
    if position != size:
        raise ValueError('Unaccounted clipboard payload')
    return result


def publish_offer(formats):
    from gi.repository import Gdk, GLib
    providers = [Gdk.ContentProvider.new_for_bytes(mime, GLib.Bytes.new(value))
                 for mime, value in formats.items()]
    provider = Gdk.ContentProvider.new_union(providers)
    Gdk.Display.get_default().get_clipboard().set_content(provider)
    return False


class EngineClipboard:
    """Offer desktop formats to the owned Mutter session, reading on demand.

    Chromium still performs the paste, including permission checks, trusted
    events and rich-text handling. Clipboard contents never enter the page IPC.
    """
    SERVICE = 'org.gnome.Mutter.RemoteDesktop'
    INTERFACE = SERVICE + '.Session'

    def __init__(self, display):
        from gi.repository import Gdk, Gio, GLib
        self.connection = display.pointer_connection
        self.session = display.pointer_session
        self.clipboard = Gdk.Display.get_default().get_clipboard()
        self.closed = False
        self.revision = 0
        self.pending = {}
        self.formats = ()
        self.signal = self.connection.signal_subscribe(self.SERVICE, self.INTERFACE,
            'SelectionTransfer', self.session, None, Gio.DBusSignalFlags.NONE,
            self._transfer)
        self.connection.call_sync(self.SERVICE, self.session, self.INTERFACE,
            'EnableClipboard', GLib.Variant('(a{sv})', ({},)), None,
            Gio.DBusCallFlags.NONE, 1000, None)
        self.changed = self.clipboard.connect('changed', self._changed)
        self._changed()

    def _call(self, method, parameters, done=None):
        from gi.repository import Gio
        if self.closed or self.connection.is_closed():
            return
        def finished(connection, result):
            try:
                value = connection.call_finish(result)
                if done:
                    done(value)
            except Exception:
                # A clipboard owner can disappear without affecting browsing.
                pass
        self.connection.call(self.SERVICE, self.session, self.INTERFACE,
            method, parameters, None, Gio.DBusCallFlags.NONE, 1000, None, finished)

    def _changed(self, *_args):
        from gi.repository import GLib
        self.revision += 1
        for serial in list(self.pending):
            self._done(serial, False)
        self.formats = tuple(mime for mime in self.clipboard.get_formats().get_mime_types()
                             if mime in MIME_TYPES)
        self._call('SetSelection', GLib.Variant('(a{sv})',
            ({'mime-types': GLib.Variant('as', self.formats)},)))

    def _done(self, serial, success):
        from gi.repository import GLib
        request = self.pending.pop(serial, None)
        if request is None:
            return False
        request['cancel'].cancel()
        if request.get('stream'):
            request['stream'].close_async(GLib.PRIORITY_DEFAULT, None, None)
        self._call('SelectionWriteDone', GLib.Variant('(ub)', (serial, success)))
        return False

    def _transfer(self, _connection, _sender, _path, _interface, _signal, parameters):
        from gi.repository import Gio, GLib
        mime, serial = parameters.unpack()
        if self.closed or mime not in self.formats or len(self.pending) >= 8:
            self._call('SelectionWriteDone', GLib.Variant('(ub)', (serial, False)))
            return
        request = {'revision': self.revision, 'cancel': Gio.Cancellable(), 'data': bytearray()}
        self.pending[serial] = request
        GLib.timeout_add(5000, lambda: self._done(serial, False) if serial in self.pending else False)
        def opened(clipboard, result):
            try:
                stream, actual = clipboard.read_finish(result)
                if serial not in self.pending or request['revision'] != self.revision or actual != mime:
                    stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
                    self._done(serial, False)
                    return
                request['stream'] = stream
                self._read(serial, request)
            except Exception:
                self._done(serial, False)
        self.clipboard.read_async([mime], GLib.PRIORITY_DEFAULT, request['cancel'], opened)

    def _read(self, serial, request):
        from gi.repository import GLib
        def chunk(stream, result):
            try:
                data = stream.read_bytes_finish(result).get_data()
                if (serial not in self.pending or request['revision'] != self.revision
                        or len(request['data']) + len(data) > MAX_BYTES):
                    self._done(serial, False)
                    return
                if data:
                    request['data'].extend(data)
                    self._read(serial, request)
                else:
                    self._write(serial, request)
            except Exception:
                self._done(serial, False)
        request['stream'].read_bytes_async(65536, GLib.PRIORITY_DEFAULT, request['cancel'], chunk)

    def _write(self, serial, request):
        import threading
        from gi.repository import Gio, GLib
        def work():
            import select
            import time
            descriptor = None
            success = False
            try:
                reply, descriptors = self.connection.call_with_unix_fd_list_sync(
                    self.SERVICE, self.session, self.INTERFACE, 'SelectionWrite',
                    GLib.Variant('(u)', (serial,)), None, Gio.DBusCallFlags.NONE,
                    1000, None, request['cancel'])
                descriptor = descriptors.get(reply.unpack()[0])
                os.set_blocking(descriptor, False)
                data = memoryview(request['data'])
                deadline = time.monotonic() + 4
                while data and not request['cancel'].is_cancelled():
                    if time.monotonic() >= deadline:
                        break
                    if select.select([], [descriptor], [], .1)[1]:
                        try:
                            data = data[os.write(descriptor, data):]
                        except BlockingIOError:
                            pass
                success = not data and not request['cancel'].is_cancelled()
            except Exception:
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                GLib.idle_add(self._done, serial, success)
        threading.Thread(target=work, name='viola-clipboard-transfer', daemon=True).start()

    def close(self):
        if self.closed:
            return
        self.clipboard.disconnect(self.changed)
        self.connection.signal_unsubscribe(self.signal)
        for serial in list(self.pending):
            self._done(serial, False)
        self.closed = True
