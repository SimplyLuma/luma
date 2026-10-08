# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-disks'))
from luma_disks.usage import scan_folder


class UsageTests(unittest.TestCase):
    def test_real_folder_totals_and_drilldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'large').mkdir()
            (root/'large'/'movie').write_bytes(b'x'*20000)
            (root/'small').write_bytes(b'x'*100)
            result=scan_folder(root,threading.Event())
            self.assertEqual(result.files,2)
            self.assertEqual(result.apparent,20100)
            self.assertEqual(result.entries[0].name,'large')
            self.assertTrue(result.complete)
            self.assertEqual(scan_folder(root/'large',threading.Event()).apparent,20000)

    def test_links_cannot_loop_or_count_file_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'file').write_bytes(b'x'*100)
            os.link(root/'file',root/'hardlink')
            (root/'loop').symlink_to(root,target_is_directory=True)
            result=scan_folder(root,threading.Event())
            self.assertEqual(result.apparent,100)
            self.assertEqual(result.files,1)
            self.assertEqual(result.skipped,1)
            self.assertFalse(result.complete)

    def test_sparse_file_is_not_reported_as_fully_allocated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with (root/'sparse').open('wb') as file: file.truncate(10**8)
            result=scan_folder(root,threading.Event())
            self.assertEqual(result.apparent,10**8)
            self.assertLess(result.allocated,result.apparent)

    def test_cancelled_and_limited_results_are_explicitly_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i in range(5): (root/str(i)).write_bytes(b'x')
            cancel=threading.Event();cancel.set()
            self.assertTrue(scan_folder(root,cancel).cancelled)
            result=scan_folder(root,threading.Event(),max_entries=2)
            self.assertTrue(result.limited)
            self.assertFalse(result.complete)
            self.assertEqual(result.files,2)

    def test_directory_replaced_by_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'scan'; root.mkdir()
            target=root/'child'; target.mkdir()
            outside=Path(tmp)/'outside'; outside.mkdir();(outside/'private').write_bytes(b'secret')
            original_open=os.open
            def raced_open(path,flags,*args,**kwargs):
                if Path(path)==target:
                    target.rename(root/'old-child'); target.symlink_to(outside,target_is_directory=True)
                return original_open(path,flags,*args,**kwargs)
            with patch('luma_disks.usage.os.open',side_effect=raced_open):
                report=scan_folder(root,threading.Event())
            self.assertEqual(report.files,0)
            self.assertGreater(report.skipped,0)
            self.assertFalse(report.complete)

    def test_scan_never_changes_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); file=root/'keep'
            file.write_bytes(b'important contents')
            before=file.stat()
            scan_folder(root,threading.Event())
            self.assertEqual(file.read_bytes(),b'important contents')
            self.assertEqual(file.stat().st_mtime_ns,before.st_mtime_ns)

if __name__=='__main__': unittest.main()
