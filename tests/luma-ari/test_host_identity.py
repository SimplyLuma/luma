# SPDX-License-Identifier: Apache-2.0
"""Native-process and signed-role admission negatives (mock custody is not release proof)."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ari import host_identity as identity

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.connection = Mock()
        def call(_bus, _path, _iface, method, args, *_):
            target=args.unpack()[0]
            return SimpleNamespace(unpack=lambda:(1001 if method=='GetConnectionUnixUser' else 77 if target=='org.freedesktop.DBus' else 42,))
        self.connection.call_sync.side_effect=call
        self.uid=patch.object(identity.os,'getuid',return_value=1001);self.uid.start()
        self.start=patch.object(identity,'start',return_value='123');self.start.start()
        self.path=patch.object(identity,'Path');self.P=self.path.start()
        self.P.return_value.open.side_effect=FileNotFoundError()
        self.P.return_value.stat.return_value=SimpleNamespace(st_dev=1,st_ino=2,st_uid=1001)
        self.ns=patch.object(identity.os,'readlink',return_value='mnt:[100]');self.ns.start()
    def tearDown(self):
        self.ns.stop();self.path.stop();self.start.stop();self.uid.stop()
    def test_host_same_uid_and_broker_namespace_admitted(self):
        self.assertEqual(identity.authenticate(self.connection,':1.1'),'native')
    def test_identity_permission_error_never_native_fallback(self):
        self.P.return_value.open.side_effect=PermissionError()
        with self.assertRaises(PermissionError):identity.authenticate(self.connection,':1.1')
        identity.os.readlink.assert_not_called()
    def test_foreign_mount_namespace_refused(self):
        identity.os.readlink.side_effect=['mnt:[other]','mnt:[host]']
        with self.assertRaises(identity.Refused):identity.authenticate(self.connection,':1.1')
    def test_process_changed_refused(self):
        identity.start.side_effect=['123','124']
        with self.assertRaises(identity.Refused):identity.authenticate(self.connection,':1.1')
    def test_root_and_foreign_user_refused(self):
        for uid in (0,1002):
            with patch.object(identity.os,'getuid',return_value=uid):
                with self.assertRaises(identity.Refused):identity.authenticate(self.connection,':1.1')
    def test_signed_wrong_role_refused_without_native_fallback(self):
        import sys
        self.P.return_value.open.side_effect=None
        approved=Mock(return_value='org.projectluma.Notes')
        package=SimpleNamespace(authenticate=approved)
        with patch.dict(sys.modules,{'luma_installer':SimpleNamespace(),'luma_installer.app_data_broker':package}):
            with self.assertRaises(identity.Refused):identity.authenticate(self.connection,':1.1')
        identity.os.readlink.assert_not_called()
    def test_installer_signed_deployment_rejection_propagates(self):
        import sys
        self.P.return_value.open.side_effect=None
        approved=Mock(side_effect=ValueError('unsigned'))
        with patch.dict(sys.modules,{'luma_installer':SimpleNamespace(),'luma_installer.app_data_broker':SimpleNamespace(authenticate=approved)}):
            with self.assertRaises(ValueError):identity.authenticate(self.connection,':1.1')
        identity.os.readlink.assert_not_called()

if __name__=='__main__':unittest.main()
