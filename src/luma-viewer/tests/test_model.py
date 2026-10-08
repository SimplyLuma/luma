# SPDX-License-Identifier: Apache-2.0
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import os
import stat
from luma_viewer.annotations import History, Mark, Viewport
from luma_viewer.export import atomic_copy, CopyDurabilityError


class ModelTests(unittest.TestCase):
    def test_rotated_zoom_letterbox_inverse(self):
        for rotation in (0,90,180,270):
            for width,height in ((360,500),(500,360),(1024,768),(2048,1536)):
                vp=Viewport(360,540,width,height,rotation)
                for point in ((0,0),(20,40),(180,270),(360,540)):
                    actual=vp.to_page(vp.to_view(point),clamp=True)
                    self.assertAlmostEqual(actual[0],point[0])
                    self.assertAlmostEqual(actual[1],point[1])
        self.assertIsNone(Viewport(100,200,500,500).to_page((0,0)))

    def test_page_ownership_clear_undo_redo_saved_branch(self):
        h=History();a=Mark('ink','#202428',(10,10),(20,20),points=((10,10),(20,20)),page=0)
        b=Mark('box','#e2564d',(10,10),(20,20),page=1)
        h.add(a);h.add(b);h.mark_saved();self.assertFalse(h.dirty)
        h.apply(m for m in h.marks if m.page != 1);self.assertTrue(h.dirty)
        h.undo();self.assertFalse(h.dirty);self.assertEqual(h.marks,(a,b))
        h.redo();self.assertEqual(h.marks,(a,));h.undo();h.undo();h.add(a.moved(10,0))
        h.redo();self.assertNotIn(b,h.marks);self.assertTrue(h.dirty)

    def test_freehand_export_bounds_include_the_entire_curve(self):
        for tool in ('ink','highlight','sign'):
            mark=Mark(tool,'#111111',(40,40),(50,40),width=6,points=((40,40),(90,10),(50,40)))
            left,top,right,bottom=mark.bounds()
            self.assertLess(top,10);self.assertGreater(right,90)

    def test_step_export_bounds_include_circle_and_border(self):
        mark=Mark('step','#111111',(100,100),(100,100),width=12,text='2')
        left,top,right,bottom=mark.bounds()
        self.assertAlmostEqual(left,48.4);self.assertAlmostEqual(right,151.6)
        self.assertAlmostEqual(top,48.4);self.assertAlmostEqual(bottom,151.6)

    def test_no_clobber_failure_and_atomic_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);src=root/'source.pdf';src.write_bytes(b'original');out=root/'copy.pdf'
            def fail(path):
                path.write_bytes(b'partial');raise OSError('disk full')
            with self.assertRaises(OSError):atomic_copy(src,out,fail)
            self.assertFalse(out.exists());self.assertEqual(list(root.iterdir()),[src])
            with self.assertRaises(ValueError):atomic_copy(src,src,lambda p:p.write_bytes(b'x'))
            atomic_copy(src,out,lambda p:p.write_bytes(b'complete'))
            self.assertEqual(out.read_bytes(),b'complete')
            with self.assertRaises(FileExistsError):atomic_copy(src,out,lambda p:p.write_bytes(b'bad'))
            link=root/'link.pdf';link.symlink_to(src)
            with self.assertRaises(ValueError):atomic_copy(src,link,lambda p:p.write_bytes(b'bad'))
            def race(path):
                path.write_bytes(b'candidate');(root/'race.pdf').write_bytes(b'other')
            with self.assertRaises(FileExistsError):atomic_copy(src,root/'race.pdf',race)
            self.assertEqual((root/'race.pdf').read_bytes(),b'other')
            self.assertEqual(src.read_bytes(),b'original')

    def test_directory_sync_failure_retains_complete_copy_and_reports_uncertainty(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);src=root/'source';src.write_bytes(b'original');out=root/'copy'
            real_sync=os.fsync
            calls=[]
            def sync(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    calls.append('directory')
                    raise OSError('directory sync unavailable')
                calls.append('file')
                return real_sync(fd)
            with patch('luma_viewer.export.os.fsync',side_effect=sync):
                with self.assertRaises(CopyDurabilityError):
                    atomic_copy(src,out,lambda p:p.write_bytes(b'complete'))
            self.assertEqual(calls,['file','directory'])
            self.assertEqual(out.read_bytes(),b'complete')
            self.assertEqual(src.read_bytes(),b'original')
            self.assertEqual(set(root.iterdir()),{src,out})

if __name__=='__main__': unittest.main()
