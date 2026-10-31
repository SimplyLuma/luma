# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from luma_android import waydroid_notifications as transport


class BinderNotificationTransport(unittest.TestCase):
    def handle(self, bridge):
        """Create the production dispatch closure with explicit Binder fixtures."""
        local = Mock()
        manager = Mock()
        manager.new_local_object.return_value = local
        manager.add_presence_handler.return_value = 0
        binder = SimpleNamespace(ServiceManager=Mock(return_value=manager))
        glib = SimpleNamespace(MainLoop=Mock())
        helpers = SimpleNamespace(drivers=SimpleNamespace(loadBinderNodes=Mock()))
        args = SimpleNamespace(BINDER_DRIVER='binder', SERVICE_MANAGER_PROTOCOL='aidl3',
                               BINDER_PROTOCOL='aidl3')
        modules = {'gbinder': binder, 'gi.repository': SimpleNamespace(GLib=glib),
                   'tools': SimpleNamespace(helpers=helpers)}
        with patch.dict('sys.modules', modules):
            transport.add_service(args, bridge, SimpleNamespace(is_set=lambda: False))
        notification = next(call for call in manager.new_local_object.call_args_list
                            if call.args[0] == transport.INTERFACE)
        return notification.args[1], local

    def test_reply_capability_needs_both_negotiation_and_live_shell_owner(self):
        for supported, owner, expected in ((False, ':1.4', 0), (True, None, 0),
                                            (True, ':1.4', 1)):
            bridge = SimpleNamespace(sink=SimpleNamespace(supports_inline_reply=supported),
                                     reply_endpoint=SimpleNamespace(owner=owner))
            handle, local = self.handle(bridge)
            request = Mock()
            request.sender_euid.return_value = 1000
            reply, status = handle(request, 4, 0)
            self.assertEqual(status, 0)
            self.assertEqual([call.args for call in reply.append_int32.call_args_list],
                             [(0,), (expected,)])

    def test_reply_post_requires_same_kernel_pid_as_registered_live_listener(self):
        record = SimpleNamespace(reply_action='')
        # Use a real frozen dataclass for dataclasses.replace in production.
        from luma_android.notification_bridge import Notification
        record = Notification('org.example.Chat', 'Chat', 'Title', 'Body', (('r', 'Reply'),))
        for registered_pid, dead, expected_status in ((91, False, 0), (92, False, -22),
                                                     (91, True, -22)):
            listener = SimpleNamespace(origin_pid=registered_pid, dead=dead)
            bridge = Mock()
            bridge.sink.supports_inline_reply = True
            bridge.reply_endpoint.owner = ':1.4'
            bridge.listeners = [listener]
            bridge.post.return_value = 17
            handle, _ = self.handle(bridge)
            request = Mock()
            request.sender_euid.return_value = 1000
            request.sender_pid.return_value = 91
            request.init_reader.return_value.read_string16.return_value = 'r'
            with patch.object(transport, 'read_notification', return_value=(record, 0)):
                reply, status = handle(request, 5, 0)
            self.assertEqual(status, expected_status)
            if expected_status == 0:
                self.assertIs(bridge.post.call_args.args[2], listener)
                self.assertEqual(bridge.post.call_args.args[0].reply_action, 'r')
            else:
                bridge.post.assert_not_called()

    def test_dispatch_denies_application_uid_before_parsing_reply_text(self):
        handle, _ = self.handle(Mock())
        for code in (4, 5):
            request = Mock()
            request.sender_euid.return_value = 10001
            _, status = handle(request, code, 0)
            self.assertEqual(status, -1)
            request.init_reader.assert_not_called()

    def test_kernel_uid_is_required_before_any_parcel_is_read(self):
        for uid in (0, 10000, 10001, None):
            request = Mock()
            request.sender_euid.return_value = uid
            self.assertFalse(transport.admitted(request))
            request.init_reader.assert_not_called()
        self.assertTrue(transport.admitted(SimpleNamespace(sender_euid=lambda: 1000)))

    def test_existing_action_and_new_lifecycle_callback_wire_numbers(self):
        gbinder = SimpleNamespace(Client=Mock())
        with patch.dict('sys.modules', {'gbinder': gbinder}):
            listener = transport.Listener(Mock(), Mock())
        request = listener.client.new_request.return_value
        listener.action(42, 'default', 'token')
        self.assertEqual(request.append_int32.call_args.args, (42,))
        self.assertEqual([c.args for c in request.append_string16.call_args_list],
                         [('default',), ('token',)])
        self.assertEqual(listener.client.transact.call_args.args[:2], (1, 1))
        listener.dismissed(42)
        self.assertEqual(listener.client.transact.call_args.args[:2], (2, 1))
        listener.reset()
        self.assertEqual(listener.client.transact.call_args.args[:2], (3, 1))

    def test_truncated_and_excessive_parcels_are_rejected(self):
        reader = Mock()
        reader.read_int32.return_value = (False, 0)
        with self.assertRaises(ValueError):
            transport.read_notification(reader)
        reader.read_int32.side_effect = [(True, 0), (True, 33)]
        reader.read_string16.side_effect = ['Chat', 'org.example.Chat', 'Title', 'Body']
        with self.assertRaises(ValueError):
            transport.read_notification(reader)
        reader.read_byte_array.assert_not_called()

    def test_aidl_byte_padding_does_not_corrupt_appended_reply_action(self):
        import struct
        payload = bytearray()
        def number(value):
            payload.extend(struct.pack('<i', value))
        def string(value):
            number(len(value)); payload.extend(value.encode('utf-16le') + b'\0\0')
            while len(payload) % 4: payload.append(0)
        number(0)
        for value in ('Chat', 'org.example.Chat', 'Title', 'Body'): string(value)
        number(0)  # no actions in this parser-only control
        number(0)  # no image
        string('')
        for value in (1, -1, 1, 0, 2): number(value)
        # Parcel.writeByte(byte) writes an int32. libgbinder read_byte consumes
        # one byte, whereas read_int32 consumes all four including padding.
        string('reply-action')
        class Reader:
            offset = 0
            def read_int32(self):
                value, = struct.unpack_from('<i', payload, self.offset)
                self.offset += 4
                return True, value
            def read_bool(self):
                ok, value = self.read_int32(); return ok, bool(value)
            def read_byte(self):
                value = payload[self.offset]; self.offset += 1
                return True, value
            def read_string16(self):
                _, size = self.read_int32()
                if size < 0 or size * 2 > len(payload) - self.offset:
                    raise ValueError('Invalid string length')
                start = self.offset
                self.offset = (start + (size + 1) * 2 + 3) // 4 * 4
                return payload[start:start + size * 2].decode('utf-16le')
        reader = Reader()
        record, replacement = transport.read_notification(reader)
        self.assertEqual((record.urgency, replacement), (2, 0))
        self.assertEqual(reader.read_string16(), 'reply-action')
        self.assertEqual(reader.offset, len(payload))


class NotificationEpoch(unittest.TestCase):
    def test_native_forwarder_restart_preserves_shell_ids_but_shell_restart_resets(self):
        bridge, endpoint = Mock(), Mock()
        bridge.sink.supports_inline_reply = True
        epoch = transport.NotificationServerEpoch(bridge, endpoint)
        epoch.changed('org.gnome.Shell', ':1.10')
        epoch.changed('org.freedesktop.Notifications', ':1.11')
        bridge.server_changed.reset_mock()
        epoch.changed('org.freedesktop.Notifications', '')
        epoch.changed('org.freedesktop.Notifications', ':1.12')
        bridge.server_changed.assert_not_called()
        epoch.changed('org.gnome.Shell', '')
        epoch.changed('org.gnome.Shell', ':1.13')
        self.assertEqual(bridge.server_changed.call_count, 2)
        endpoint.set_owner.assert_called_with(':1.13')

    def test_other_servers_follow_fdo_owner_and_capability_downgrade_resets(self):
        bridge, endpoint = Mock(), Mock()
        bridge.sink.supports_inline_reply = False
        epoch = transport.NotificationServerEpoch(bridge, endpoint)
        epoch.changed('org.freedesktop.Notifications', ':1.11')
        bridge.server_changed.reset_mock()
        epoch.changed('org.gnome.Shell', ':1.10')
        bridge.server_changed.assert_not_called()
        epoch.changed('org.freedesktop.Notifications', '')
        bridge.server_changed.assert_called_once()
        bridge.sink.supports_inline_reply = True
        epoch.changed('org.freedesktop.Notifications', ':1.12')
        bridge.server_changed.reset_mock()
        bridge.sink.refresh_capabilities.side_effect = lambda: setattr(bridge.sink, 'supports_inline_reply', False)
        epoch.changed('org.freedesktop.Notifications', ':1.13')
        bridge.server_changed.assert_called_once()


if __name__ == '__main__':
    unittest.main()
