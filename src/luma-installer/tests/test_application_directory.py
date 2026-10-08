# SPDX-License-Identifier: Apache-2.0
"""Actual GIO desktop catalogue and launch, plus bounded broker controls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from gi.repository import Gio, GLib
from luma_installer import application_directory as directory


class Protocol(unittest.TestCase):
    def test_desktop_ids_refuse_commands_paths_and_controls(self):
        for value in ('../../bin/sh.desktop', '/usr/bin/sh', 'x.desktop\n', 'x.desktop --evil', '', 'x'*260+'.desktop'):
            with self.subTest(value=value), self.assertRaises(ValueError): directory.validate_id(value)
        self.assertEqual(directory.validate_id('org.projectluma.Notes.desktop'),'org.projectluma.Notes.desktop')
    def test_mime_size_and_syntax(self):
        for value in ('x', '/x', 'text/plain\n', 'x/'+'y'*128):
            with self.subTest(value=value), self.assertRaises(ValueError): directory.validate_mime(value)
        directory.validate_mime('text/calendar');directory.validate_mime('')
    def test_file_authority_is_portal_and_uid_scoped(self):
        for uri in ('file:///home/user/.local/share/private/key','file:///run/user/123/doc/ab/../key',
                    'file:///run/user/124/doc/ab/x', 'file://host/run/user/123/doc/ab/x',
                    'file:///run/user/123/doc/not.a.token/x', 'file:///run/user/123/doc/ab/x?key=x',
                    'mailto:user@example.com','https://user:password@example.com/x'):
            with self.subTest(uri=uri), self.assertRaises(ValueError): directory.validate_uris([uri],123,check_files=False)
        directory.validate_uris(['file:///run/user/123/doc/ab/report.pdf','https://example.com/a'],123,check_files=False)
        directory.validate_uris(['file:///run/user/123/doc/9AVY_dAacjHr_F6nIKEYcQ/report.pdf'],123,check_files=False)
        for token in ('', '.', '..', 'a%2Fb', 'a%00b', 'a'*65, 'a%20b'):
            with self.subTest(token=token), self.assertRaises(ValueError):
                directory.validate_uris(['file:///run/user/123/doc/'+token+'/report.pdf'],123,check_files=False)
    def test_document_count_and_total_budget(self):
        directory.validate_uris(['https://example.com/track.flac']*256,check_files=False)
        with self.assertRaises(ValueError):directory.validate_uris(['https://example.com/a']*257,check_files=False)
        with self.assertRaises(ValueError):directory.validate_uris(['https://example.com/'+'a'*4096],check_files=False)
    def test_unsupported_receiver_does_not_launch(self):
        app=mock.Mock();app.should_show.return_value=True;app.supports_files.return_value=False;app.supports_uris.return_value=False
        with mock.patch.object(Gio.DesktopAppInfo,'new',return_value=app),self.assertRaises(ValueError):
            directory.launch_application('org.example.Test.desktop',['https://example.com/a'])
        app.launch.assert_not_called();app.launch_uris.assert_not_called()
    def test_catalogue_is_real_registered_desktop_metadata_and_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);apps=base/'data/applications';apps.mkdir(parents=True)
            marker=base/'accepted';script=base/'accept';script.write_text('#!/bin/sh\nprintf accepted > '+str(marker)+'\n');script.chmod(0o700)
            (apps/'org.projectluma.DirectoryProbe.desktop').write_text('[Desktop Entry]\nType=Application\nName=Actual directory probe\nIcon=application-x-executable\nExec='+str(script)+'\nMimeType=application/x-luma-directory-probe;\n')
            (apps/'org.projectluma.HiddenDirectoryProbe.desktop').write_text('[Desktop Entry]\nType=Application\nName=Hidden directory probe\nNoDisplay=true\nExec=/usr/bin/false\nMimeType=application/x-luma-directory-probe;\n')
            code='''import json,time
from luma_installer.application_directory import application_rows,launch_application
rows=application_rows('');assert any(r[0]=='org.projectluma.DirectoryProbe.desktop' for r in rows)
assert not any(r[0]=='org.projectluma.HiddenDirectoryProbe.desktop' for r in rows)
assert launch_application('org.projectluma.DirectoryProbe.desktop',[])
print(json.dumps(rows))
'''
            env={**os.environ,'XDG_DATA_HOME':str(base/'data'),'XDG_DATA_DIRS':str(base/'empty')}
            result=subprocess.run([sys.executable,'-B','-c',code],env=env,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            import time
            deadline=time.monotonic()+3
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(marker.read_text(),'accepted')
            self.assertTrue(json.loads(result.stdout)[0][0].endswith('.desktop'))


class MainContextAdmission(unittest.TestCase):
    def broker(self):
        b=directory.Broker.__new__(directory.Broker);b.active={};b.last=0
        return b
    def test_invalid_requests_never_authenticate_or_launch(self):
        b=self.broker();inv=mock.Mock()
        with mock.patch('luma_installer.app_data_broker.authenticate',side_effect=AssertionError('invalid request reached auth')):
            b._call(mock.Mock(),':1.1','','','Launch',GLib.Variant('(sas)',('/bin/sh',[])),inv)
        inv.return_dbus_error.assert_called_once();self.assertFalse(b.active)
    def test_four_jobs_and_one_per_sender_are_bounded(self):
        b=self.broker();b.active={':1.'+str(i):Gio.Cancellable() for i in range(4)};inv=mock.Mock()
        b._call(mock.Mock(),':1.8','','','List',GLib.Variant('(s)',('',)),inv)
        inv.return_dbus_error.assert_called_once();self.assertEqual(len(b.active),4)
    def test_actual_dbus_signature_is_bounded_public_rows(self):
        node=Gio.DBusNodeInfo.new_for_xml(directory.XML)
        self.assertEqual(node.interfaces[0].name,directory.BUS)
        row=('org.projectluma.Notes.desktop','Notes','org.projectluma.Notes',False,True,True)
        self.assertEqual(GLib.Variant('(a(sssbbb))',([row],)).unpack(),([row],))


if __name__=='__main__':unittest.main()
