# SPDX-License-Identifier: MPL-2.0
"""Native service policy/handler tests; signed process admission has installed gates."""
from types import SimpleNamespace
import unittest
from unittest import mock
from gi.repository import GLib
from luma_background.service import BackgroundService
from luma_background.identity import Caller
from luma_background.manager import Refused

class NativeServiceTests(unittest.TestCase):
    def owner(self, *, allowed=True, native=True, trusted=True, has_unit=True):
        service=BackgroundService.__new__(BackgroundService)
        service.connection=mock.Mock()
        service.connection.call_sync.return_value=GLib.Variant('(b)', (True,))
        service.manager=mock.Mock()
        service.identity=mock.Mock()
        service.identity.is_trusted_host.return_value=False
        service.manager.agents={'org.projectluma.Messages': object()}
        service.manager.record.return_value=SimpleNamespace(origin='native' if native else 'flatpak', trusted_install=trusted, has_unit=has_unit)
        service.manager.request_background.side_effect=lambda app, **kw: kw['reply'](0 if allowed else 1, {'background':allowed,'autostart':allowed})
        return service

    def invoke(self, owner, *, identity='org.projectluma.Messages', role=True, authentication=None):
        caller=Caller(':1.21', 1234, 1000, 'org.projectluma.Messages', True)
        invocation=mock.Mock()
        with mock.patch('luma_installer.app_data_broker.authenticate', return_value=identity, side_effect=authentication), \
             mock.patch('luma_background.service.native_service_role', return_value=role):
            owner._method_RequestBackground(caller, GLib.Variant('(sa{sv})', ('',{'reason':GLib.Variant('s','Messages service')})), invocation)
        return invocation

    def test_allowed_signed_ui_reaches_existing_native_policy_only(self):
        owner=self.owner(); invocation=self.invoke(owner)
        self.assertEqual(owner.manager.request_background.call_args.args, ('org.projectluma.Messages',))
        self.assertEqual(invocation.return_value.call_args.args[0].unpack(), (0, {'background':True,'autostart':True}))

    def test_person_deny_remains_deny(self):
        owner=self.owner(allowed=False); invocation=self.invoke(owner)
        self.assertEqual(invocation.return_value.call_args.args[0].unpack(), (1, {'background':False,'autostart':False}))

    def test_unsigned_foreign_role_and_sandbox_agent_are_refused_before_request(self):
        for settings in ({'authentication':PermissionError('unsigned')},{'identity':'org.projectluma.Notes'},{'role':False},{'native':False},{'trusted':False},{'has_unit':False}):
            with self.subTest(settings=settings):
                owner=self.owner(**{k:v for k,v in settings.items() if k in {'native','trusted','has_unit'}})
                with self.assertRaises(Refused):
                    self.invoke(owner, **{k:v for k,v in settings.items() if k not in {'native','trusted','has_unit'}})
                owner.manager.request_background.assert_not_called()

    def test_foreground_caller_disconnect_during_start_releases_only_its_lease(self):
        owner=self.owner()
        owner.connection.call_sync.return_value=GLib.Variant('(b)', (False,))
        owner.manager.acquire_foreground.side_effect=lambda app, sender, ready: ready(True)
        caller=Caller(':1.21',1234,1000,'org.projectluma.Messages',True)
        invocation=mock.Mock()
        with mock.patch('luma_installer.app_data_broker.authenticate',return_value=caller.app_id), \
             mock.patch('luma_background.service.native_service_role',return_value=True):
            owner._method_RequestForeground(caller,None,invocation)
        owner.manager.acquire_foreground.assert_called_once()
        owner.manager.release_foreground.assert_called_once_with(':1.21','org.projectluma.Messages')
        self.assertEqual(invocation.return_value.call_args.args[0].unpack(),(False,))
        owner.manager.request_background.assert_not_called()

    def test_foreground_requires_signed_sandbox_and_never_caller_selected_app(self):
        for sandbox in (False,True):
            owner=self.owner(); invocation=mock.Mock()
            caller=Caller(':1.21',1234,1000,'org.projectluma.Messages',sandbox)
            with mock.patch('luma_installer.app_data_broker.authenticate',side_effect=PermissionError('unsigned')), \
                 self.assertRaises(Refused):
                owner._method_RequestForeground(caller,None,invocation)
            owner.manager.acquire_foreground.assert_not_called()

    def test_bus_vanish_and_explicit_release_cannot_release_another_sender(self):
        owner=self.owner()
        caller=Caller(':1.21',1234,1000,'org.projectluma.Messages',True)
        owner._method_ReleaseForeground(caller,None,mock.Mock())
        owner.manager.release_foreground.assert_called_once_with(':1.21')
        owner.manager.release_foreground.reset_mock()
        owner._owner_changed(None,None,None,None,None,GLib.Variant('(sss)',(':1.22',':1.22','')))
        owner.manager.release_foreground.assert_called_once_with(':1.22')
        owner.manager.release_foreground.reset_mock()
        owner._owner_changed(None,None,None,None,None,GLib.Variant('(sss)',('org.example.Owner',':1.22','')))
        owner.manager.release_foreground.assert_not_called()

    def test_unsigned_native_id_cannot_stop_or_revoke_trusted_host_service(self):
        owner=self.owner(); caller=Caller(':1.21',1234,1000,'org.projectluma.Messages',True)
        for method, params in ((owner._method_StopNow, GLib.Variant('(s)',(caller.app_id,))),
                               (owner._method_SetAllowed, GLib.Variant('(sb)',(caller.app_id,False)))):
            with mock.patch('luma_installer.app_data_broker.authenticate',side_effect=PermissionError('unsigned')), \
                 self.assertRaises(Refused):
                method(caller,params,mock.Mock())
        owner.manager.stop_now.assert_not_called()
        owner.manager.set_allowed.assert_not_called()

if __name__=='__main__':unittest.main()
