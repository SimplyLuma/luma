# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from luma_android import waydroid_notifications as transport


class BinderNotificationTransport(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
