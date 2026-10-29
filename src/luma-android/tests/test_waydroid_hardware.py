# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace
from unittest import TestCase, mock
from pathlib import Path
import tempfile

from luma_android.waydroid_hardware import (
    GuardedGbinder, GuardedServiceManager, INTERFACE, EX_SECURITY,
    SERVICE_NAME, install_guard, install_session_guards,
    verify_confinement, EX_UNSUPPORTED_OPERATION,
)


class Reply:
    def __init__(self):
        self.values = []

    def append_int32(self, value):
        if not 0 <= value <= 0xffffffff:
            raise OverflowError('python-gbinder requires an unsigned 32-bit word')
        self.values.append(('int32', value))

    def append_string16(self, value):
        self.values.append(('string16', value))


class HardwareAdmission(TestCase):
    def test_selinux_hosts_require_the_provider_execution_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            enforce = Path(directory) / 'enforce'
            current = Path(directory) / 'current'
            enforce.write_text('1')
            current.write_text('system_u:system_r:waydroid_t:s0\n')
            verify_confinement(enforce, current)
            for context in ('system_u:system_r:init_t:s0',
                            'system_u:system_r:unconfined_service_t:s0', 'unconfined'):
                current.write_text(context)
                with self.subTest(context=context), self.assertRaises(RuntimeError):
                    verify_confinement(enforce, current)
            current.unlink()
            with self.assertRaises(FileNotFoundError):
                verify_confinement(enforce, current)
            enforce.unlink()
            verify_confinement(enforce, current)

    def test_kernel_identity_is_checked_before_the_provider_can_read_a_parcel(self):
        local = SimpleNamespace(new_reply=Reply)
        manager = mock.Mock()
        manager.new_local_object.return_value = local
        backend = SimpleNamespace(ServiceManager=mock.Mock(return_value=manager))
        provider = mock.Mock(return_value=('provider-reply', 0))
        guarded = GuardedServiceManager(backend, '/dev/binder')
        self.assertIs(guarded.new_local_object(INTERFACE, provider), local)
        handle = manager.new_local_object.call_args.args[1]
        for uid in (0, 1000, 1001, 1003, 2000, 9999, 10000, 10125,
                    99000, 100000, 110000, -1, None, False, '1000'):
            for code, flags in ((code, flags) for code in range(1, 8) for flags in (0, 1)):
                with self.subTest(uid=uid, code=code, flags=flags):
                    provider.reset_mock()
                    request = mock.Mock()
                    request.sender_euid.return_value = uid
                    request.init_reader.side_effect = AssertionError('Untrusted parcel read')
                    result = handle(request, code, flags)
                    if type(uid) is int and uid in (0, 1000):
                        self.assertEqual(result, ('provider-reply', 0))
                        provider.assert_called_once_with(request, code, flags)
                    else:
                        self.assertEqual(result[1], 0)
                        self.assertEqual(result[0].values, [
                            ('int32', EX_SECURITY & 0xffffffff),
                            ('string16', 'Android host service access denied'),
                            ('int32', 0)])
                        provider.assert_not_called()
                    request.init_reader.assert_not_called()

    def test_missing_kernel_credentials_fail_closed(self):
        local = SimpleNamespace(new_reply=Reply)
        backend = SimpleNamespace(ServiceManager=lambda: SimpleNamespace(
            new_local_object=lambda interface, callback: (setattr(local, 'callback', callback) or local)))
        provider = mock.Mock()
        manager = GuardedServiceManager(backend)
        manager.new_local_object(INTERFACE, provider)
        request = mock.Mock()
        request.sender_euid.side_effect = RuntimeError('Credentials unavailable')
        reply, status = local.callback(request, 4, 1)
        self.assertEqual(status, 0)
        self.assertEqual(reply.values[0], ('int32', EX_SECURITY & 0xffffffff))
        provider.assert_not_called()

    def test_unknown_operation_has_a_typed_reply_and_never_dispatches_hardware(self):
        local = SimpleNamespace(new_reply=Reply)
        backend = SimpleNamespace(ServiceManager=lambda: SimpleNamespace(
            new_local_object=lambda interface, callback: (setattr(local, 'callback', callback) or local)))
        provider = mock.Mock()
        GuardedServiceManager(backend).new_local_object(INTERFACE, provider)
        for uid, exception in ((0, EX_UNSUPPORTED_OPERATION), (10126, EX_SECURITY)):
            request = SimpleNamespace(sender_euid=lambda: uid)
            reply, status = local.callback(request, 0x10000, 0)
            self.assertEqual(status, 0)
            self.assertEqual(reply.values[0], ('int32', exception & 0xffffffff))
            self.assertEqual(reply.values[-1], ('int32', 0))
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

    def test_session_bridges_admit_only_system_server_before_parcel_read(self):
        modules = []
        for descriptor, name in (
            ('lineageos.waydroid.IClipboard', 'waydroidclipboard'),
            ('lineageos.waydroid.IUserMonitor', 'waydroidusermonitor'),
        ):
            local = SimpleNamespace(new_reply=Reply)
            manager = mock.Mock()
            manager.new_local_object.return_value = local
            backend = SimpleNamespace(ServiceManager=mock.Mock(return_value=manager))
            modules.append(SimpleNamespace(INTERFACE=descriptor, SERVICE_NAME=name,
                                           gbinder=backend))
        install_session_guards(*modules)
        install_session_guards(*modules)
        for module in modules:
            provider = mock.Mock(return_value=('provider-reply', 0))
            manager = module.gbinder.ServiceManager('/dev/binder')
            manager.new_local_object(module.INTERFACE, provider)
            handle = manager.manager.new_local_object.call_args.args[1]
            for uid in (0, 1000, 1003, 2000, 10000, 10129, 99000, 101000, 110129):
                for code in (1, 2, 65536):
                    with self.subTest(service=module.SERVICE_NAME, uid=uid, code=code):
                        provider.reset_mock()
                        request = mock.Mock()
                        request.sender_euid.return_value = uid
                        result = handle(request, code, 0)
                        if uid == 1000 and code in (1, 2):
                            self.assertEqual(result, ('provider-reply', 0))
                            provider.assert_called_once_with(request, code, 0)
                        else:
                            expected = EX_UNSUPPORTED_OPERATION if uid == 1000 else EX_SECURITY
                            self.assertEqual(result[1], 0)
                            self.assertEqual(result[0].values[0], ('int32', expected & 0xffffffff))
                            provider.assert_not_called()
                        request.init_reader.assert_not_called()

    def test_a_second_install_cannot_silently_change_the_admission_role(self):
        hardware = SimpleNamespace(INTERFACE=INTERFACE, SERVICE_NAME=SERVICE_NAME,
                                   gbinder=SimpleNamespace())
        install_guard(hardware)
        with self.assertRaises(RuntimeError):
            install_guard(hardware, allowed_uids=(1000,))
