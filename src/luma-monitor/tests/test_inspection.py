# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from luma_monitor.inspection import inspect_table, inspect_properties


def stat(start):
    fields=['0']*22;fields[0]='S';fields[19]=str(start)
    return '42 (example) '+' '.join(fields)


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.pid=self.root/'42';self.pid.mkdir();(self.pid/'fd').mkdir()
        (self.pid/'stat').write_text(stat(50));(self.pid/'cgroup').write_text('0::/app-example.scope\n')
        self.process=SimpleNamespace(pid=42,start=50,cgroup='/app-example.scope')
    def tearDown(self):self.temp.cleanup()
    def test_links_are_read_without_opening_target(self):
        (self.pid/'fd'/'3').symlink_to('/sacred/never-open-this')
        (self.pid/'fd'/'1').symlink_to('pipe:[123]')
        with patch('pathlib.Path.write_text',side_effect=AssertionError('write')):
            columns,rows=inspect_table(self.process,'files',self.root)
        self.assertEqual(columns,['FD','Type','Object'])
        self.assertEqual(rows,[['1','pipe','pipe:[123]'],['3','file','/sacred/never-open-this']])
    def test_maps_keep_real_address_flags_size_and_name(self):
        (self.pid/'maps').write_text('100000-200000 r-xp 00000000 00:00 0 /usr/bin/example\n200000-202000 rw-p 00000000 00:00 0 [heap]\n')
        columns,rows=inspect_table(self.process,'maps',self.root)
        self.assertEqual(rows,[['100000','r-xp','1.0 MB','/usr/bin/example'],['200000','rw-p','8 KB','[heap]']])
    def test_pid_reuse_rejected_before_read(self):
        (self.pid/'stat').write_text(stat(51))
        with self.assertRaises(ProcessLookupError):inspect_table(self.process,'maps',self.root)
    def test_pid_reuse_rejected_after_read(self):
        from luma_monitor import inspection
        original=inspection.validate;calls=[]
        def changing(process,proc):
            calls.append(True)
            if len(calls)==2:(self.pid/'stat').write_text(stat(51))
            return original(process,proc)
        (self.pid/'maps').write_text('100000-200000 r-xp 0 00:00 0 /bin/example\n')
        with patch.object(inspection,'validate',changing):
            with self.assertRaises(ProcessLookupError):inspect_table(self.process,'maps',self.root)
        self.assertEqual(len(calls),2)
    def test_moved_group_or_bad_kind_rejected(self):
        (self.pid/'cgroup').write_text('0::/other.scope\n')
        with self.assertRaises(ProcessLookupError):inspect_table(self.process,'files',self.root)
        with self.assertRaises(ValueError):inspect_table(self.process,'write',self.root)

    def test_properties_use_real_status_and_command_without_writing(self):
        (self.pid/'status').write_text('State: S (sleeping)\nUid: 1000 1000 1000 1000\nThreads: 7\nVmRSS: 2048 kB\nRssFile: 512 kB\nRssShmem: 256 kB\n')
        (self.pid/'cmdline').write_bytes(b'/usr/bin/example\x00--file\x00a b\x00')
        with patch('pathlib.Path.write_text',side_effect=AssertionError('write')):
            facts=dict(inspect_properties(self.process,self.root))
        self.assertEqual(facts['Status'],'S (sleeping)')
        self.assertEqual(facts['Threads'],7)
        self.assertEqual(facts['Memory'],'2 MB resident · 768 KB shared')
        self.assertEqual(facts['Command line'],"/usr/bin/example --file 'a b'")
        self.assertEqual(facts['Control group'],'/app-example.scope')

    def test_properties_reject_a_reused_pid(self):
        (self.pid/'stat').write_text(stat(51))
        with self.assertRaises(ProcessLookupError):inspect_properties(self.process,self.root)
