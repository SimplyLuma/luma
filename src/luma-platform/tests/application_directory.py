# SPDX-License-Identifier: Apache-2.0
"""Actual GIO FD/variant contracts; bounded async completion and cancellation.

A controlled portal boundary checks the original FD and read-only permission.
It does not replace the separately required signed host/portal acceptance gate.
"""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from gi.repository import Gio, GLib
from luma_appkit import application_directory as api


def spin(test, seconds=3):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
        if test(): return True
        time.sleep(.002)
    return test()


class PortalBoundary:
    def __init__(self,path,token='a1b2'): self.path=path; self.calls=[]; self.token=token
    def call_with_unix_fd_list_sync(self,*args):
        self.calls.append(args[3]); self.params=args[4].unpack()
        fd=args[8].get(self.params[0])
        try:
            assert os.fstat(fd).st_ino == self.path.stat().st_ino
            assert Path('/proc/self/fd/'+str(fd)).read_bytes() == self.path.read_bytes()
        finally: os.close(fd)
        return GLib.Variant('(s)',(self.token,)),None
    def call_sync(self,*args):
        self.calls.append(args[3])
        if args[3]=='GrantPermissions':
            assert args[4].unpack()==(self.token,'org.projectluma.Notes',['read'])
            return GLib.Variant('()',())
        assert args[3]=='GetMountPoint'
        return GLib.Variant('(ay)',(list(os.fsencode('/run/user/'+str(os.getuid())+'/doc')+b'\0'),))


class Contracts(unittest.TestCase):
    def test_original_fd_transient_owner_readonly_target_not_reused_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'a b.flac';path.write_bytes(b'original audio\0\xff')
            boundary=PortalBoundary(path)
            uri=api._document_uri(boundary,Gio.File.new_for_path(str(path)),
                                  'org.projectluma.Notes.desktop',None)
            self.assertEqual(boundary.params,(0,False,False))
            self.assertEqual(boundary.calls,['Add','GrantPermissions','GetMountPoint'])
            self.assertEqual(uri,Gio.File.new_for_path('/run/user/'+str(os.getuid())+'/doc/a1b2/a b.flac').get_uri())
    def test_actual_opaque_portal_tokens_and_malformed_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'export.md';path.write_bytes(b'owned exported note')
            for token in ('a1b2','9AVY_dAacjHr_F6nIKEYcQ'):
                boundary=PortalBoundary(path,token)
                uri=api._document_uri(boundary,Gio.File.new_for_path(str(path)),'org.projectluma.Notes.desktop',None)
                self.assertEqual(uri,Gio.File.new_for_path('/run/user/'+str(os.getuid())+'/doc/'+token+'/export.md').get_uri())
            for token in ('', '.', '..', 'a/b', 'a:b', 'a'*65, 'a b', None):
                boundary=PortalBoundary(path,token)
                with self.subTest(token=token), self.assertRaises((ValueError,TypeError)):
                    api._document_uri(boundary,Gio.File.new_for_path(str(path)),'org.projectluma.Notes.desktop',None)
                self.assertEqual(boundary.calls,['Add'])

    def test_native_desktop_name_does_not_grant_invalid_sandbox_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'file';path.touch();boundary=PortalBoundary(path)
            api._document_uri(boundary,Gio.File.new_for_path(str(path)),'firefox.desktop',None)
            self.assertEqual(boundary.calls,['Add','GetMountPoint'])
    def test_catalogue_completion_runs_main_context_and_cancellation_drops_reply(self):
        entered=threading.Event();release=threading.Event();replies=[]
        def wire(*args):
            entered.set();assert release.wait(2)
            return [('org.projectluma.Notes.desktop','Notes','org.projectluma.Notes',False,True,True)]
        with mock.patch.object(api,'sandboxed',return_value=True),mock.patch.object(api,'_call',side_effect=wire):
            cancel=api.discover('text/plain',lambda *args:replies.append((threading.get_ident(),args)))
            self.assertTrue(entered.wait(2));cancel.cancel();release.set()
            spin(lambda:bool(replies),seconds=.08)
            self.assertEqual(replies,[])
            entered.clear()
            main=threading.get_ident()
            api.discover('text/plain',lambda *args:replies.append((threading.get_ident(),args)))
            self.assertTrue(spin(lambda:bool(replies)))
            self.assertEqual(replies[0][0],main)
            self.assertIsNone(replies[0][1][1]);self.assertEqual(replies[0][1][0][0].get_id(),'org.projectluma.Notes.desktop')
    def test_launch_refusal_is_returned_on_main_context_and_oversize_never_calls_host(self):
        replies=[];main=threading.get_ident()
        with mock.patch.object(api,'sandboxed',return_value=True),mock.patch.object(api,'_call',return_value=False) as call:
            api.launch('org.projectluma.Notes.desktop',callback=lambda *args:replies.append((threading.get_ident(),args)))
            self.assertTrue(spin(lambda:bool(replies)))
            self.assertEqual(replies[0][0],main);self.assertFalse(replies[0][1][0]);self.assertIn('did not accept',replies[0][1][1])
            call.reset_mock();replies.clear()
            api.launch('org.projectluma.Notes.desktop',[Gio.File.new_for_uri('https://example.com/a')]*257,callback=lambda *args:replies.append(args))
            self.assertTrue(spin(lambda:bool(replies)));call.assert_not_called();self.assertFalse(replies[0][0])


if __name__=='__main__':unittest.main()
