# SPDX-License-Identifier: GPL-3.0-only
"""Bounded native texture receiver for the isolated GTK host integration."""
import array
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading

import gi
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib


class _FrameConnection:
    def __init__(self, owner, peer):
        self.owner = owner
        self.on_clipboard = owner.on_clipboard
        self.pending_clipboard = None
        self.clipboard_scheduled = False
        self.on_cursor, self.on_frame, self.on_error = owner.on_cursor, owner.on_frame, owner.on_error
        self.peer = peer
        self.window_id = None
        self.disconnected = False
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.pending = set()
        self.imported = self.released = 0
        self.worker = None

    def start(self, engine_pid):
        self.worker = threading.Thread(target=self._receive, args=(engine_pid,), daemon=True)
        self.worker.start()

    def _release(self, frame_id, descriptors):
        with self.lock:
            if frame_id not in self.pending:
                return
            self.pending.remove(frame_id)
            for descriptor in descriptors:
                os.close(descriptor)
            try:
                if not self.disconnected:
                    self.peer.sendall(struct.pack('!I', frame_id))
                self.released += 1
            except OSError:
                # Window teardown invalidates the peer, but its descriptors
                # must still be released when the last GTK texture is dropped.
                self.disconnected = True

    def _import(self, metadata, descriptors):
        frame_id = metadata['id']
        if self.stopped.is_set():
            self._release(frame_id, descriptors)
            return GLib.SOURCE_REMOVE
        try:
            builder = Gdk.DmabufTextureBuilder.new()
            builder.set_display(Gdk.Display.get_default())
            builder.set_width(metadata['width'])
            builder.set_height(metadata['height'])
            builder.set_fourcc(int.from_bytes(metadata['fourcc'].encode('ascii'), 'little'))
            builder.set_modifier(int(metadata['modifier']))
            builder.set_premultiplied(True)
            builder.set_n_planes(len(descriptors))
            for index, (plane, descriptor) in enumerate(zip(metadata['planes'], descriptors)):
                builder.set_fd(index, descriptor)
                builder.set_stride(index, plane['stride'])
                builder.set_offset(index, int(plane['offset']))
            texture = builder.build(lambda *_: self._release(frame_id, descriptors), frame_id)
            if texture is None:
                raise RuntimeError('GDK rejected the native texture')
            self.imported += 1
        except Exception as error:
            self._release(frame_id, descriptors)
            self.on_error(str(error))
            return GLib.SOURCE_REMOVE
        # The consumer owns the texture lifetime. Do not release its lease if
        # a consumer callback raises after retaining it.
        try:
            self.on_frame(texture, metadata)
        except Exception as error:
            self.on_error(str(error))
        return GLib.SOURCE_REMOVE

    def _deliver_clipboard(self):
        with self.lock:
            formats, self.pending_clipboard = self.pending_clipboard, None
            self.clipboard_scheduled = False
        if not self.stopped.is_set() and formats is not None and self.on_clipboard:
            self.on_clipboard(formats)
        return GLib.SOURCE_REMOVE

    def _receive(self, engine_pid):
        try:
            pid, uid, _ = struct.unpack('3i', self.peer.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            if pid != engine_pid or uid != os.geteuid():
                raise RuntimeError('Frame sender is not the owned Chromium child')
            self.peer.settimeout(1)
            while not self.stopped.is_set():
                try:
                    data, ancillary, flags, _ = self.peer.recvmsg(4096,
                        socket.CMSG_SPACE(4 * array.array('i').itemsize), socket.MSG_CMSG_CLOEXEC)
                except socket.timeout:
                    continue
                if not data:
                    return
                descriptors = []
                try:
                    for level, kind, payload in ancillary:
                        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                            values = array.array('i')
                            values.frombytes(payload)
                            descriptors.extend(values)
                    metadata = json.loads(data)
                    if metadata.get('kind') == 'window':
                        if (descriptors or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                                or metadata.get('version') != 1 or self.window_id is not None
                                or type(metadata.get('window_id')) is not int
                                or not all(isinstance(metadata.get(key), str) and 1 <= len(metadata[key]) <= 128
                                           for key in ('sidebar_target_id', 'overlay_target_id'))):
                            raise ValueError('Invalid native window announcement')
                        self.window_id = metadata['window_id']
                        if self.owner.on_window:
                            GLib.idle_add(self.owner.on_window, metadata)
                        continue
                    if metadata.get('kind') != 'clipboard' and (
                            self.window_id is None or metadata.get('window_id') != self.window_id):
                        raise ValueError('Frame or cursor belongs to a different window')
                    if metadata.get('kind') == 'clipboard':
                        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                            raise ValueError('Truncated clipboard offer')
                        from native_clipboard import decode_offer
                        formats = decode_offer(metadata, descriptors)
                        with self.lock:
                            self.pending_clipboard = formats
                            if not self.clipboard_scheduled:
                                self.clipboard_scheduled = True
                                GLib.idle_add(self._deliver_clipboard)
                        continue
                    if metadata.get('kind') == 'cursor':
                        if (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or descriptors
                                or metadata.get('version') != 1
                                or not isinstance(metadata.get('target_id'), str)
                                or not 1 <= len(metadata['target_id']) <= 128
                                or type(metadata.get('cursor')) is not int
                                or not -1 <= metadata['cursor'] <= 53):
                            raise ValueError('Invalid cursor metadata')
                        if self.on_cursor:
                            GLib.idle_add(self.on_cursor, metadata)
                        continue
                    if (flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                            or metadata['version'] != 1 or not 1 <= len(descriptors) <= 4
                            or len(descriptors) != len(metadata['planes'])
                            or metadata['fourcc'] not in ('AR24', 'AB24')
                            or not 0 < metadata['id'] < 2**31
                            or not 0 < metadata['width'] <= 5120
                            or not 0 < metadata['height'] <= 3200
                            or not 0 <= int(metadata['modifier']) < 2**64
                            or not isinstance(metadata.get('target_id'), str)
                            or not 1 <= len(metadata['target_id']) <= 128):
                        raise ValueError('Invalid frame metadata')
                    for plane in metadata['planes']:
                        if (not 0 < plane['stride'] < 2**32
                                or not 0 <= int(plane['offset']) < 2**32
                                or not 0 < int(plane['size']) < 2**63):
                            raise ValueError('Invalid plane metadata')
                    with self.lock:
                        if metadata['id'] in self.pending or len(self.pending) >= 3:
                            raise ValueError('Duplicate or excess frame lease')
                        self.pending.add(metadata['id'])
                    GLib.idle_add(self._import, metadata, descriptors)
                    descriptors = []
                finally:
                    for descriptor in descriptors:
                        os.close(descriptor)
        except (ConnectionResetError, BrokenPipeError):
            # Chromium can reset a seqpacket connection with an unread lease
            # ACK when that browser window closes. This is that window's EOF,
            # not an application-wide transport failure.
            pass
        except Exception as error:
            if not self.stopped.is_set():
                GLib.idle_add(self.on_error, str(error))
        finally:
            with self.lock:
                self.disconnected = True
                self.peer.close()
            if not self.stopped.is_set() and self.window_id is not None and self.owner.on_window_closed:
                GLib.idle_add(self.owner.on_window_closed, self.window_id)

    def stop(self):
        self.stopped.set()
        if self.worker:
            self.worker.join(timeout=2)
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

    def close(self):
        self.stop()
        if self.pending:
            raise RuntimeError('Release all GTK textures before closing their transport')
        if self.peer:
            self.peer.close()


class FrameReceiver:
    """One authenticated socket connection and lease namespace per browser window."""
    def __init__(self, on_frame, on_error, on_cursor=None, on_clipboard=None,
                 on_window=None, on_window_closed=None):
        self.on_frame, self.on_error = on_frame, on_error
        self.on_cursor, self.on_clipboard = on_cursor, on_clipboard
        self.on_window, self.on_window_closed = on_window, on_window_closed
        self.directory = tempfile.TemporaryDirectory(prefix='viola-gpu-', dir=os.environ['XDG_RUNTIME_DIR'])
        self.path = Path(self.directory.name) / 'frames.sock'
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.listener.listen(16)
        self.listener.settimeout(1)
        self.stopped = threading.Event()
        self.connections = []
        self.worker = None

    @property
    def pending(self):
        return {(index, frame) for index, connection in enumerate(self.connections)
                for frame in list(connection.pending)}

    @property
    def imported(self):
        return sum(connection.imported for connection in self.connections)

    @property
    def released(self):
        return sum(connection.released for connection in self.connections)

    def set_visible(self, window_id, visible):
        for connection in tuple(self.connections):
            if connection.window_id != window_id or connection.disconnected:
                continue
            with connection.lock:
                try:
                    connection.peer.send(struct.pack('!I', 0xffffffff if visible else 0xfffffffe),
                                         socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL)
                    return True
                except BlockingIOError:
                    return False
                except OSError:
                    return True  # Closed windows need no retry.
        return True

    def start(self, engine_pid):
        self.worker = threading.Thread(target=self._accept, args=(engine_pid,), daemon=True)
        self.worker.start()

    def _accept(self, engine_pid):
        while not self.stopped.is_set():
            try:
                peer, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.stopped.is_set():
                    GLib.idle_add(self.on_error, 'Native frame listener closed unexpectedly')
                return
            connection = _FrameConnection(self, peer)
            self.connections.append(connection)
            connection.start(engine_pid)

    def stop(self):
        self.stopped.set()
        if self.worker:
            self.worker.join(timeout=2)
        for connection in self.connections:
            connection.stop()

    def close(self):
        self.stop()
        for connection in self.connections:
            connection.close()
        self.listener.close()
        self.directory.cleanup()
        return GLib.SOURCE_REMOVE
