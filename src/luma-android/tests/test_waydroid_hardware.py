# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
from unittest import TestCase, mock

from luma_android.waydroid_hardware import (
    GuardedGbinder, GuardedServiceManager, INTERFACE, PERMISSION_DENIED,
    SERVICE_NAME, install_guard,
)


class HardwareAdmission(TestCase):
    def test_kernel_identity_is_checked_before_the_provider_can_read_a_parcel(self):
        local = SimpleNamespace(new_reply=lambda: 'denied-reply')
        manager = mock.Mock()
        manager.new_local_object.return_value = local
        backend = SimpleNamespace(ServiceManager=mock.Mock(return_value=manager))
        provider = mock.Mock(return_value=('provider-reply', 0))
        guarded = GuardedServiceManager(backend, '/dev/binder')
        self.assertIs(guarded.new_local_object(INTERFACE, provider), local)
        handle = manager.new_local_object.call_args.args[1]
        for uid in (0, 1000, 1001, 1003, 2000, 9999, 10000, 10125,
                    99000, 100000, 110000, -1, None, False, '1000'):
            for code in range(1, 8):
                with self.subTest(uid=uid, code=code):
                    provider.reset_mock()
                    request = mock.Mock()
                    request.sender_euid.return_value = uid
                    request.init_reader.side_effect = AssertionError('Untrusted parcel read')
                    result = handle(request, code, 0)
                    if type(uid) is int and uid in (0, 1000):
                        self.assertEqual(result, ('provider-reply', 0))
                        provider.assert_called_once_with(request, code, 0)
                    else:
                        self.assertEqual(result, ('denied-reply', PERMISSION_DENIED))
                        provider.assert_not_called()
                    request.init_reader.assert_not_called()

    def test_missing_kernel_credentials_fail_closed(self):
        local = SimpleNamespace(new_reply=lambda: 'denied')
        backend = SimpleNamespace(ServiceManager=lambda: SimpleNamespace(
            new_local_object=lambda interface, callback: (setattr(local, 'callback', callback) or local)))
        provider = mock.Mock()
        manager = GuardedServiceManager(backend)
        manager.new_local_object(INTERFACE, provider)
        request = mock.Mock()
        request.sender_euid.side_effect = RuntimeError('Credentials unavailable')
        self.assertEqual(local.callback(request, 4, 1), ('denied', PERMISSION_DENIED))
        provider.assert_not_called()

    def test_only_the_hardware_module_receives_the_constructor_adapter(self):
        backend = SimpleNamespace(ServiceManager=mock.Mock(), unrelated='kept')
        hardware = SimpleNamespace(INTERFACE=INTERFACE, SERVICE_NAME=SERVICE_NAME,
                                   gbinder=backend)
        original_constructor = backend.ServiceManager
        install_guard(hardware)
        first = hardware.gbinder
        install_guard(hardware)
        self.assertIs(hardware.gbinder, first)
        self.assertIsInstance(first, GuardedGbinder)
        self.assertIs(backend.ServiceManager, original_constructor)
        self.assertEqual(first.unrelated, 'kept')
        first.ServiceManager('/dev/binder', 'aidl3', 'aidl')
        original_constructor.assert_called_once_with('/dev/binder', 'aidl3', 'aidl')

    def test_unknown_interface_is_never_exported_without_admission(self):
        hardware = SimpleNamespace(INTERFACE='changed', SERVICE_NAME=SERVICE_NAME)
        with self.assertRaises(RuntimeError):
            install_guard(hardware)
        backend = SimpleNamespace(ServiceManager=mock.Mock())
        manager = GuardedServiceManager(backend)
        with self.assertRaises(RuntimeError):
            manager.new_local_object('changed', mock.Mock())
        backend.ServiceManager.return_value.new_local_object.assert_not_called()
