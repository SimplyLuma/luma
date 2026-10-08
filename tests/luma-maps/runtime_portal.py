#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Real GTK4/libportal ABI against a private, explicitly synthetic portal.

Run under dbus-run-session and Xvfb. This verifies parenting and wire/lifecycle
compatibility, not GeoClue, a real permission UI or a person's real location.
Import luma_maps from an extracted exact candidate RPM via PYTHONPATH.
"""
import os
import unittest
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk
from luma_maps.location import LocationRequest

LOCATION_XML = '''<node><interface name="org.freedesktop.portal.Location">
<method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<property name="version" type="u" access="read"/>
<signal name="LocationUpdated"><arg type="o"/><arg type="a{sv}"/></signal>
</interface></node>'''
REQUEST_XML = '''<node><interface name="org.freedesktop.portal.Request"><method name="Close"/>
<signal name="Response"><arg type="u"/><arg type="a{sv}"/></signal></interface></node>'''
SESSION_XML = '''<node><interface name="org.freedesktop.portal.Session"><method name="Close"/>
<signal name="Closed"/></interface></node>'''
DESKTOP = '/org/freedesktop/portal/desktop'


class FixturePortal:
    def __init__(self):
        self.connection = Gio.DBusConnection.new_for_address_sync(
            os.environ['DBUS_SESSION_BUS_ADDRESS'],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'RequestName',
            GLib.Variant('(su)', ('org.freedesktop.portal.Desktop', 0)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
        self.interfaces = {key: Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
                           for key, xml in [('Location', LOCATION_XML), ('Request', REQUEST_XML), ('Session', SESSION_XML)]}
        self.connection.register_object(DESKTOP, self.interfaces['Location'], self.call,
                                        lambda *args: GLib.Variant('u', 2), None)
        self.parent = ''
        self.closed = 0
        self.mode = 'grant'

    def register(self, path, kind):
        self.connection.register_object(path, self.interfaces[kind], self.call, None, None)

    def respond(self, sender, request, result, values):
        self.connection.emit_signal(sender, request, 'org.freedesktop.portal.Request',
                                    'Response', GLib.Variant('(ua{sv})', (result, values)))
        return False

    def fix(self, sender, session):
        values = {key: GLib.Variant('d', value) for key, value in {
            'Latitude': 41.88, 'Longitude': -87.63, 'Altitude': 0.0, 'Accuracy': 25.0,
            'Speed': 0.0, 'Heading': 0.0}.items()}
        values['Description'] = GLib.Variant('s', 'Synthetic portal ABI fixture')
        values['Timestamp'] = GLib.Variant('(tt)', (1234, 0))
        self.connection.emit_signal(sender, DESKTOP, 'org.freedesktop.portal.Location',
                                    'LocationUpdated', GLib.Variant('(oa{sv})', (session, values)))
        return False

    def call(self, _connection, sender, path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        if method == 'Close':
            if '/session/' in path:
                self.closed += 1
            invocation.return_value(GLib.Variant('()', ()))
            return
        options = args[-1]
        owner = sender[1:].replace('.', '_')
        if method == 'CreateSession':
            # Location CreateSession returns the session directly; unlike
            # ScreenCast it does not go through a Request.Response object.
            session = DESKTOP + '/session/' + owner + '/' + options['session_handle_token']
            self.register(session, 'Session')
            self.created_options = options
            invocation.return_value(GLib.Variant('(o)', (session,)))
        elif method == 'Start':
            token = options['handle_token']
            request = DESKTOP + '/request/' + owner + '/' + token
            self.register(request, 'Request')
            invocation.return_value(GLib.Variant('(o)', (request,)))
            session, self.parent, _options = args
            response = 1 if self.mode == 'deny' else 0
            GLib.timeout_add(100 if self.mode == 'cancel' else 10,
                             self.respond, sender, request, response, {})
            if response == 0:
                GLib.timeout_add(150 if self.mode == 'cancel' else 50, self.fix, sender, session)



class NativePortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        cls.portal = FixturePortal()

    def run_case(self, mode):
        self.portal.mode = mode
        self.portal.parent = ""
        window = Gtk.Window(title='Maps native portal ABI fixture')
        fixes, errors = [], []
        loop = GLib.MainLoop()
        request = LocationRequest(window, fixes.append, errors.append)
        window.present()
        expired = [False]
        def start():
            self.assertTrue(window.get_mapped(), 'native GTK parent must be mapped')
            request.start()
            if mode == 'cancel':
                GLib.timeout_add(40, lambda: request.close() or False)
            return False
        GLib.timeout_add(100, start)
        def done():
            if fixes or errors or mode == 'cancel':
                loop.quit()
                return False
            return True
        check_id = GLib.timeout_add(300, done)
        def timeout():
            expired[0] = True
            loop.quit()
            return False
        timeout_id = GLib.timeout_add_seconds(8, timeout)
        loop.run()
        if not expired[0]:
            GLib.source_remove(timeout_id)
        else:
            GLib.source_remove(check_id)
        request.close()
        window.destroy()
        self.assertFalse(expired[0], 'native portal operation timed out')
        self.assertTrue(self.portal.parent.startswith('x11:'), self.portal.parent)
        return fixes, errors, request

    def test_granted_fix_uses_real_libportal_native_parent_and_cleans_monitor(self):
        before = self.portal.closed
        fixes, errors, request = self.run_case('grant')
        self.assertEqual(errors, [])
        self.assertEqual(self.portal.created_options["accuracy"], 5)
        self.assertEqual(self.portal.created_options["distance-threshold"], 0)
        self.assertEqual(len(fixes), 1)
        self.assertAlmostEqual(fixes[0].latitude, 41.88)
        self.assertAlmostEqual(fixes[0].longitude, -87.63)
        self.assertFalse(request.active)
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        self.assertGreater(self.portal.closed, before)

    def test_native_denial_has_no_position(self):
        fixes, errors, request = self.run_case('deny')
        self.assertEqual(fixes, [])
        self.assertEqual(len(errors), 1)
        self.assertFalse(request.active)

    def test_cancel_ignores_late_native_reply_and_signal(self):
        fixes, errors, request = self.run_case('cancel')
        self.assertEqual(fixes, [])
        self.assertEqual(errors, [])
        self.assertFalse(request.active)


if __name__ == '__main__':
    unittest.main()
