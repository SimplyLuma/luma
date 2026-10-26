# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from luma_tester import host_devices,vm
from luma_tester.core import TesterError


class HostDevicesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.sys=self.root/'sys';self.dev=self.root/'dev'
        self.usb=self.sys/'bus/usb/devices';self.usb.mkdir(parents=True)
        self.mounts=self.root/'mountinfo';self.mounts.write_text('')

    def add_usb(self,name,kind,number):
        path=self.usb/name;path.mkdir()
        for file,text in dict(busnum='1',devnum=str(number),product='Test '+kind).items():(path/file).write_text(text)
        interface=self.usb/(name+':1.0');interface.mkdir();(interface/'bInterfaceClass').write_text(kind)
        node=self.dev/f'bus/usb/001/{number:03}';node.parent.mkdir(parents=True,exist_ok=True);node.touch()
        return path

    def test_usb_storage_and_cameras_appear_but_host_input_stays_connected(self):
        self.add_usb('1-1','0e',1);self.add_usb('1-2','08',2);self.add_usb('1-3','03',3)
        items=host_devices.usb_devices(self.sys,self.dev,self.mounts)
        self.assertEqual([p['kind'] for p in items],['camera','storage'])
        self.assertTrue(all(p['accessible'] for p in items))

    def test_mounted_thumb_drive_cannot_be_transferred(self):
        drive=self.add_usb('1-2','08',2)
        partition=drive/'block/sdb/sdb1';partition.mkdir(parents=True);(partition/'dev').write_text('8:17')
        blocks=self.sys/'class/block';blocks.mkdir(parents=True);(blocks/'sdb1').symlink_to(partition)
        self.mounts.write_text('1 0 8:17 / /media/drive rw - vfat /dev/sdb1 rw\n')
        items=host_devices.usb_devices(self.sys,self.dev,self.mounts)
        self.assertTrue(items[0]['mounted'])
        cfg=dict(vm.DEFAULTS,usb=[{key:items[0][key] for key in ('name','bus','address')}])
        with patch.object(vm,'usb_devices',return_value=items):
            with self.assertRaises(TesterError):vm.devices(cfg)

    def test_ipu_capture_endpoints_become_one_visible_integrated_camera(self):
        device=self.sys/'devices/pci0000:00/0000:00:05.0';device.mkdir(parents=True)
        video=self.sys/'class/video4linux';video.mkdir(parents=True)
        for i in range(32):
            path=video/('video'+str(i));path.mkdir();(path/'device').symlink_to(device)
            (path/'name').write_text('Intel IPU7 ISYS Capture '+str(i))
        items=host_devices.integrated_cameras(self.sys)
        self.assertEqual(len(items),1);self.assertEqual(items[0]['name'],'Integrated camera (Intel IPU7)')
        self.assertFalse(items[0]['accessible']);self.assertIn('coming soon',items[0]['reason'])
