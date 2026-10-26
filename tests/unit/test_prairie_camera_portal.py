# SPDX-License-Identifier: Apache-2.0
import os
import ast
from pathlib import Path
import socket
import sys
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gio, GLib, Gst
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/prairie-core'))
from prairie_apps.camera_portal import CameraPortal, PORTAL, PATH, CAMERA
from prairie_apps.camera_backend import CameraDevice, create_camera_source

XML = '''<node><interface name="org.freedesktop.portal.Camera">
<method name="AccessCamera"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="OpenPipeWireRemote"><arg type="a{sv}" direction="in"/><arg type="h" direction="out"/></method>
</interface></node>'''


def until(predicate):
    deadline = time.monotonic() + 3
    context = GLib.MainContext.default()
    while not predicate() and time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(.002)
    if not predicate():
        raise AssertionError('camera portal did not answer')


class CameraPortalTests(unittest.TestCase):
    def setUp(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.registration = self.bus.register_object(PATH,
            Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self.called, None, None)
        self.owner = Gio.bus_own_name_on_connection(self.bus, PORTAL,
            Gio.BusNameOwnerFlags.NONE, None, None)
        self.left, self.right = socket.socketpair()
        self.session = CameraPortal()
        self.response = 0
        self.opened = 0

    def tearDown(self):
        self.session.close()
        self.left.close(); self.right.close()
        Gio.bus_unown_name(self.owner)
        self.bus.unregister_object(self.registration)
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

    def called(self, bus, sender, path, interface, method, parameters, invocation):
        if method == 'OpenPipeWireRemote':
            self.opened += 1
            descriptors = Gio.UnixFDList.new()
            index = descriptors.append(self.left.fileno())
            invocation.return_value_with_unix_fd_list(GLib.Variant('(h)', (index,)), descriptors)
            return
        (options,) = parameters.unpack()
        token = options['handle_token']
        request = PATH + '/request/' + sender.lstrip(':').replace('.', '_') + '/' + token
        invocation.return_value(GLib.Variant('(o)', (request,)))
        bus.emit_signal(sender, request, 'org.freedesktop.portal.Request', 'Response',
                        GLib.Variant('(ua{sv})', (self.response, {})))

    def test_granted_remote_fd_is_owned_until_close(self):
        errors = []
        self.session.access(errors.append)
        until(lambda: bool(errors))
        self.assertEqual(errors, [''])
        fd = self.session.fd
        os.write(fd, b'grant')
        self.assertEqual(self.right.recv(5), b'grant')
        self.session.close(); self.session.close()
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertEqual(self.session.subscription, 0)

    def test_denied_grant_never_opens_pipewire(self):
        self.response = 2
        errors = []
        self.session.access(errors.append)
        until(lambda: bool(errors))
        self.assertIn('not granted', errors[0])
        self.assertEqual(self.opened, 0)
        self.assertEqual(self.session.fd, -1)

    def test_capture_source_uses_granted_device_instead_of_raw_hardware(self):
        device = CameraDevice('serial', 'Camera', 'front', portal_device=Mock())
        source = create_camera_source(device, 'preview')
        self.assertIs(source, device.portal_device.create_element.return_value)
        self.assertTrue(device.managed_stream)
        device.portal_device.create_element.assert_called_once_with('preview')

    def test_provider_duplicates_do_not_duplicate_lenses_and_metadata_is_preserved(self):
        Gst.init(None)
        candidates = []
        for identifier, location in (('12', 'front'), ('13', 'back')):
            candidate = Mock()
            props = {'media.class': 'Video/Source', 'object.serial': identifier,
                     'api.libcamera.location': location, 'node.description': location}
            candidate.get_properties.return_value.get_string.side_effect = props.get
            candidate.get_caps.return_value = Gst.Caps.from_string('video/x-raw,format=RGB,width=1280,height=720')
            candidates.extend((candidate, candidate))
        provider = Mock()
        provider.get_devices.return_value = candidates
        with patch('prairie_apps.camera_portal.Gst.DeviceProviderFactory.get_by_name', return_value=provider):
            devices = self.session.devices()
        self.assertEqual([device.location for device in devices], ['front', 'back'])
        self.assertEqual([device.identifier for device in devices], ['12', '13'])
        self.assertEqual(len(devices), 2)

    def test_portal_camera_offers_video_when_recording_components_exist(self):
        path = Path(__file__).resolve().parents[2] / 'src/prairie-core/prairie_apps/camera_surface.py'
        cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'CameraSurface')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'available_modes')
        namespace = {'__name__': 'prairie_apps.camera_surface', '__package__': 'prairie_apps'}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(path), 'exec'), namespace)
        window = SimpleNamespace(_fixture=None, _devices=[CameraDevice('12', 'Front camera', 'front', portal_device=Mock())], state=SimpleNamespace(source=0))
        with patch('prairie_apps.camera_video.VideoRecording.available', return_value=True):
            self.assertEqual(namespace['available_modes'](SimpleNamespace(window=window)), [('photo', 'Photo'), ('video', 'Video')])


if __name__ == '__main__':
    unittest.main()
