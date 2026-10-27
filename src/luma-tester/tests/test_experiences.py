# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from luma_tester import experiences, presets, vm
from luma_tester.core import Session, read_manifest, TesterError


class ExperienceTests(unittest.TestCase):
    def test_legacy_architecture_defaults_migrate_to_host(self):
        legacy={'architectures':dict(desktop='aarch64',tablet='aarch64',mobile='x86_64')}
        for host in ('x86_64','aarch64'):
            values,explicit=experiences.architecture_preferences(legacy,host)
            self.assertEqual(values['desktop'],host)
            self.assertEqual(values['tablet'],host)
            self.assertEqual(values['mobile'],'aarch64')
            self.assertEqual(explicit,set())

    def test_deliberate_architecture_choices_survive_restart(self):
        saved={'architectures':dict(desktop='aarch64',tablet='auto',mobile='x86_64'),
               'architecture_choices':['desktop','tablet','mobile','invalid']}
        values,explicit=experiences.architecture_preferences(saved,'x86_64')
        self.assertEqual(values['desktop'],'aarch64')
        self.assertEqual(values['tablet'],'auto')
        self.assertEqual(values['mobile'],'x86_64')
        self.assertEqual(explicit,{'desktop','tablet','mobile'})
        again,choices=experiences.architecture_preferences(dict(architectures=values,architecture_choices=list(explicit)),'x86_64')
        self.assertEqual((again,choices),(values,explicit))

    def test_arm_modes_do_not_silently_choose_x86(self):
        x=('x86',dict(target='desktop',arch='x86_64'))
        arm=('arm',dict(target='desktop',arch='aarch64'))
        phone=('phone',dict(target='mobile',arch='aarch64'))
        for mode in ('tv','smart-device'):
            self.assertIsNone(experiences.select(mode,[x,phone],'x86_64'))
            self.assertEqual(experiences.select(mode,[x,arm,phone],'x86_64'),'arm')
        self.assertEqual(experiences.select('desktop',[x,arm],'aarch64'),'arm')
        self.assertEqual(experiences.select('tablet',[x,arm],'x86_64'),'x86')
        self.assertEqual(experiences.select('tablet',[x,arm],'aarch64'),'arm')
        self.assertIsNone(experiences.select('mobile',[arm,x],'aarch64'))
        self.assertEqual(experiences.select('mobile',[phone,x],'x86_64'),'phone')

    def test_explicit_architecture_overrides_policy_without_target_fallback(self):
        images=[('desktop-x86',dict(target='desktop',arch='x86_64')),
                ('phone-x86',dict(target='mobile',arch='x86_64')),
                ('desktop-arm',dict(target='desktop',arch='aarch64'))]
        self.assertEqual(experiences.select('mobile',images,'x86_64','x86_64'),'phone-x86')
        self.assertIsNone(experiences.select('mobile',images,'x86_64','aarch64'))
        for mode in ('tablet','tv','smart-device'):
            self.assertEqual(experiences.select(mode,images,'x86_64','x86_64'),'desktop-x86')
            self.assertEqual(experiences.select(mode,images,'x86_64','auto'),'desktop-x86' if mode=='tablet' else 'desktop-arm')
        self.assertEqual(experiences.select('desktop',images,'aarch64','x86_64'),'desktop-x86')
        with self.assertRaises(TesterError):
            experiences.select('mobile',images,'x86_64','invalid')

    def test_iso_disk_firmware_presets_and_guest_controls(self):
        if not all(shutil.which(x) for x in ('qemu-img','xorriso','ssh-keygen')):self.skipTest('QEMU tools required')
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);runtime=root/'run';runtime.mkdir(mode=0o700)
            tree=root/'tree';tree.mkdir();(tree/'README').write_text('Unbootable media fixture, not an OS.')
            image=root/'fixture.iso'
            subprocess.run(['xorriso','-as','mkisofs','-quiet','-o',str(image),str(tree)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            manifest=experiences.import_iso(image,root/'state','aarch64')
            with patch.dict(os.environ,XDG_RUNTIME_DIR=str(runtime)):
                session=Session(manifest,root/'state')
                vm.save(session.root,dict(vm.load(session.root),disk_gib=8,audio=False))
                # This disk/argument test does not execute the ARM emulator.
                tools=session.tools();tools['qemu-system-aarch64']='argument-only-test'
                with patch.object(session,'tools',return_value=tools):session.prepare(lambda _:None)
                info=json.loads(subprocess.check_output(['qemu-img','info','--output=json',str(session.disk)]))
                self.assertNotIn('backing-filename',info)
                self.assertEqual(info['virtual-size'],8*1024**3)
                self.assertFalse(session.key.exists())
                self.assertFalse((session.directory/'seed.iso').exists())
                firmware=root/'firmware';firmware.mkdir()
                for file in ('QEMU_EFI.fd','QEMU_EFI-pflash.raw','vars-template-pflash.raw'):(firmware/file).write_bytes(b'test-only-not-firmware')
                with patch.object(session,'firmware',return_value=str(firmware/'QEMU_EFI.fd')):
                    mobile=session.command(experience='mobile',graphics='software',embedded=True)
                    self.assertIn('name=opt/luma/experience,string=mobile',mobile)
                    self.assertTrue(any('xres=864,yres=1920' in arg for arg in mobile))
                    command=session.command(experience='tv',graphics='software',embedded=True)
                    self.assertIn('name=opt/luma/experience,string=tv',command)
                    self.assertIn('scsi-cd,drive=installer,bootindex=1',command)
                    variables=session.directory/'firmware-vars.raw';variables.write_bytes(b'saved-variable-state')
                    vm.save(session.root,dict(vm.load(session.root),boot_iso=False))
                    command=session.command(experience='smart-device',graphics='software')
                    self.assertIn('name=opt/luma/experience,string=home',command)
                    self.assertIn('virtio-blk-pci,drive=os,bootindex=1',command)
                    self.assertEqual(variables.read_bytes(),b'saved-variable-state')
                with patch.object(session,'status',return_value='running'),patch.object(session,'qmp') as control:
                    session.shutdown();control.assert_called_once_with('system_powerdown')
                items=presets.save(root/'presets.json','TV',manifest,'develop',experience='tv')
                self.assertEqual(items[0]['target'],'tv')
                self.assertEqual(presets.resolve(items[0]),manifest)
                self.assertEqual(presets.load(root/'presets.json')[0]['target'],'tv')
