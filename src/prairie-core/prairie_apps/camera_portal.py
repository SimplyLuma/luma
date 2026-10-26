# SPDX-License-Identifier: Apache-2.0
"""Camera portal grant and its scoped PipeWire device provider.

No raw hardware or unrestricted PipeWire socket permission is needed. The
provider and remote FD remain owned by this window until capture has stopped.
"""
import os
import uuid
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, Gst
from .camera_backend import CameraDevice

PORTAL = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
CAMERA = 'org.freedesktop.portal.Camera'


class CameraPortal:
    def __init__(self):
        self.bus = None
        self.cancel = Gio.Cancellable()
        self.fd = -1
        self.provider = None
        self.subscription = 0
        self.request = None
        self.closed = False

    def access(self, callback):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        token = 'luma_camera_' + uuid.uuid4().hex
        sender = self.bus.get_unique_name().lstrip(':').replace('.', '_')
        self.request = f'{PATH}/request/{sender}/{token}'

        def responded(_bus, _sender, _path, _interface, _signal, parameters):
            if self.closed:
                return
            self.bus.signal_unsubscribe(self.subscription)
            self.subscription = 0
            self.request = None
            response, _results = parameters.unpack()
            if response != 0:
                callback('Camera access was not granted. Allow Camera in privacy settings and try again.')
                return
            self.bus.call_with_unix_fd_list(PORTAL, PATH, CAMERA, 'OpenPipeWireRemote',
                GLib.Variant('(a{sv})', ({},)), GLib.VariantType.new('(h)'),
                Gio.DBusCallFlags.NONE, 5000, None, self.cancel, opened)

        def opened(bus, result):
            try:
                reply, descriptors = bus.call_with_unix_fd_list_finish(result)
                fd = descriptors.get(reply.unpack()[0])
            except GLib.Error:
                if not self.closed:
                    callback('Camera access is unavailable. Try again.')
                return
            if self.closed:
                os.close(fd)
                return
            self.fd = fd
            callback('')

        def requested(bus, result):
            try:
                (handle,) = bus.call_finish(result).unpack()
            except GLib.Error:
                if not self.closed:
                    callback('Camera access is unavailable. Try again.')
                return
            if self.closed or (self.request is not None and handle != self.request):
                bus.call(PORTAL, handle, 'org.freedesktop.portal.Request', 'Close',
                         None, None, Gio.DBusCallFlags.NONE, 1000, None, None)
                if not self.closed:
                    callback('Camera access is unavailable. Try again.')

        self.subscription = self.bus.signal_subscribe(PORTAL,
            'org.freedesktop.portal.Request', 'Response', self.request, None,
            Gio.DBusSignalFlags.NONE, responded)
        self.bus.call(PORTAL, PATH, CAMERA, 'AccessCamera',
            GLib.Variant('(a{sv})', ({'handle_token': GLib.Variant('s', token)},)),
            GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, -1,
            self.cancel, requested)

    def devices(self):
        """Worker-thread discovery; return real photographic Video/Source nodes."""
        Gst.init(None)
        self.provider = Gst.DeviceProviderFactory.get_by_name('pipewiredeviceprovider')
        if self.provider is None or self.provider.find_property('fd') is None:
            raise RuntimeError('The camera portal streaming component is unavailable.')
        self.provider.set_property('fd', self.fd)
        if not self.provider.start():
            raise RuntimeError('The camera service could not enumerate cameras.')
        devices = []
        seen = set()
        for candidate in self.provider.get_devices():
            props = candidate.get_properties()
            if props is None or props.get_string('media.class') != 'Video/Source':
                continue
            identifier = props.get_string('object.serial') or props.get_string('node.name') or candidate.get_display_name()
            if identifier in seen:
                continue
            caps = candidate.get_caps()
            selected = None
            for width, height in ((1280, 720), (640, 480)):
                preferred = Gst.Caps.from_string(f'video/x-raw,width={width},height={height}')
                intersection = caps.intersect(preferred)
                if not intersection.is_empty():
                    selected = intersection.fixate()
                    break
            if selected is None:
                raw = caps.intersect(Gst.Caps.from_string('video/x-raw'))
                if raw.is_empty():
                    continue
                selected = raw.fixate()
            structure = selected.get_structure(0)
            if structure.get_string('format') in ('GRAY8', 'GRAY16_LE', 'GRAY16_BE'):
                continue
            width, height = structure.get_value('width'), structure.get_value('height')
            location = props.get_string('api.libcamera.location') or props.get_string('api.libcamera.Location') or ''
            location = 'front' if location in ('front', 'CameraLocationFront') else 'back' if location in ('back', 'CameraLocationBack') else 'external'
            seen.add(identifier)
            label = 'Front camera' if location == 'front' else 'Rear camera' if location == 'back' else props.get_string('node.description') or candidate.get_display_name()
            devices.append(CameraDevice(identifier=identifier,
                label=label,
                location=location, preview_width=width, preview_height=height,
                viewfinder_width=width, viewfinder_height=height,
                still_width=width, still_height=height, portal_device=candidate))
        return tuple(devices)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel.cancel()
        if self.subscription:
            self.bus.signal_unsubscribe(self.subscription)
            self.subscription = 0
        if self.request:
            self.bus.call(PORTAL, self.request, 'org.freedesktop.portal.Request',
                'Close', None, None, Gio.DBusCallFlags.NONE, 1000, None, None)
            self.request = None
        if self.provider:
            self.provider.stop()
            self.provider = None
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
