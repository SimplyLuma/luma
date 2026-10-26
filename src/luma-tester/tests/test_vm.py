# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path
import shutil
import shlex
from types import SimpleNamespace
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from luma_tester import vm
from luma_tester.core import Session, TesterError, digest


class VMTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        runtime=self.root/'runtime';runtime.mkdir(mode=0o700)
        p=patch.dict(os.environ, XDG_RUNTIME_DIR=str(runtime));p.start();self.addCleanup(p.stop)

    def test_resources_persist_and_reach_qemu(self):
        cfg=vm.load(self.root)
        cfg.update(memory=8192, cpus=6, disk_gib=64, graphics='software')
        vm.save(self.root,cfg)
        self.assertEqual(vm.load(self.root),cfg)
        self.assertEqual((self.root/'vm-settings.json').stat().st_mode & 0o777,0o600)

    def test_shared_folder_is_explicit_and_comma_escaped(self):
        directory=self.root/'a,b';directory.mkdir()
        cfg=dict(vm.load(self.root),folders=[dict(path=str(directory),writable=False)])
        args=vm.devices(cfg)
        self.assertIn('path='+str(directory).replace(',',',,'),args[args.index('-fsdev')+1])
        self.assertIn('readonly=on',args[args.index('-fsdev')+1])
        cfg['folders'][0]['path']=str(Path.home())
        with self.assertRaises(TesterError):vm.save(self.root,cfg)

    def test_missing_share_is_preserved_and_requires_reconnection(self):
        cfg=dict(vm.load(self.root),memory=8192,folders=[dict(path=str(self.root/'unplugged'),writable=False)])
        vm.save(self.root,cfg)
        self.assertEqual(vm.load(self.root)['memory'],8192)
        with self.assertRaisesRegex(TesterError,'no longer available'):vm.devices(vm.load(self.root))

    def test_no_microphone_or_camera_by_default(self):
        args=vm.devices(vm.load(self.root))
        self.assertIn('hda-output,audiodev=sound0',args)
        self.assertFalse(any('hda-duplex' in arg or 'usb-host' in arg for arg in args))

    def test_microphone_uses_a_supported_codec_and_enables_audio(self):
        settings=vm.save(self.root,dict(vm.load(self.root),audio=False,microphone=True))
        self.assertTrue(settings['audio'])
        args=vm.devices(settings)
        self.assertIn('hda-duplex,audiodev=sound0',args)
        if shutil.which('qemu-system-x86_64'):
            available=subprocess.check_output(['qemu-system-x86_64','-device','help'],text=True)
            self.assertIn('name \"hda-duplex\"',available)

    def test_connection_command_uses_actual_identity_and_quoted_key(self):
        directory=self.root/'VM with spaces';directory.mkdir()
        session=SimpleNamespace(root=self.root,directory=directory,
            manifest=dict(user='developer',target='mobile',build='Test build',arch='x86_64'))
        (directory/'connection.json').write_text(json.dumps(dict(port=2299)))
        (directory/'agent-key').write_text('private-key-fixture')
        (directory/'agent-key').chmod(0o640)
        with self.assertRaises(TesterError):vm.ssh_connection(session)
        vm.save(self.root,dict(vm.load(self.root),agent_ssh=True))
        details=vm.ssh_connection(session)
        self.assertEqual((details['host'],details['port'],details['user']),('127.0.0.1',2299,'developer'))
        args=shlex.split(details['command'])
        self.assertEqual(args[args.index('-i')+1],str(directory/'agent-key'))
        self.assertEqual(args[-1],'developer@127.0.0.1')
        self.assertIn('StrictHostKeyChecking=yes',args)
        self.assertNotIn('private-key-fixture',details['command'])
        self.assertEqual((directory/'agent-key').stat().st_mode & 0o777,0o600)
        self.assertIn(details['command'],vm.agent_prompt(session,self.root/'manifest.json'))

    def test_old_settings_gain_iso_boot_default(self):
        settings=vm.load(self.root);del settings['boot_iso']
        vm.save(self.root,settings)
        self.assertTrue(vm.load(self.root)['boot_iso'])

    def test_invalid_resources_rejected(self):
        for key,value in [('memory',128),('cpus',0),('disk_gib',1),('agent_ssh','yes')]:
            with self.subTest(key=key),self.assertRaises(TesterError):
                vm.save(self.root,dict(vm.load(self.root),**{key:value}))

    def test_live_snapshot_keeps_process_running_and_restores_disk(self):
        if not all(shutil.which(x) for x in ('qemu-system-x86_64','qemu-img','qemu-io')):
            self.skipTest('QEMU required')
        source=self.root/'source.qcow2';subprocess.run(['qemu-img','create','-q','-f','qcow2',str(source),'32M'],check=True)
        path=self.root/'manifest.json'
        path.write_text(json.dumps(dict(schema=1,id='live-snapshot',target='desktop',arch='x86_64',boot='bios',cloud_init=True,user='luma',build='No OS fixture',image=dict(url=source.as_uri(),sha256=digest(source),bytes=source.stat().st_size))))
        session=Session(path,self.root/'state');session.directory.mkdir(parents=True)
        shutil.copy2(source,session.disk)
        def io(command):subprocess.run(['qemu-io','-f','qcow2','-c',command,str(session.disk)],check=True,stdout=subprocess.DEVNULL)
        io('write -P 0x11 0 4096')
        child=subprocess.Popen(['qemu-system-x86_64','-machine','q35','-accel','tcg','-m','64','-nodefaults','-display','none',
            '-blockdev',json.dumps({'driver':'qcow2','node-name':'os','file':{'driver':'file','filename':str(session.disk)}}),
            '-device','virtio-blk-pci,drive=os','-qmp','unix:'+str(session.qmp_path)+',server=on,wait=off'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10
            while session.status()=='stopped' and time.monotonic()<deadline:time.sleep(.05)
            self.assertEqual(session.status(),'running')
            self.assertIn('While running',[x['name'] for x in vm.checkpoint(session,'While running')])
            self.assertIsNone(child.poll());self.assertEqual(session.status(),'running')
            session.qmp('stop');vm.checkpoint(session,'While paused');self.assertEqual(session.status(),'paused')
            with self.assertRaisesRegex(TesterError,'already exists'):vm.checkpoint(session,'While running')
            self.assertEqual(session.status(),'paused')
            session.qmp('quit');self.assertEqual(child.wait(timeout=10),0)
        finally:
            if child.poll() is None:child.terminate();child.wait(timeout=10)
            child.stderr.close()
        io('write -P 0x22 0 4096');vm.restore(session,'While running');io('read -P 0x11 0 4096')

    def test_offline_snapshot_really_restores_disk_bytes(self):
        if not all(shutil.which(x) for x in ('qemu-img','qemu-io','xorriso','ssh-keygen')):
            self.skipTest('QEMU disk tools required')
        source=self.root/'base.qcow2'
        subprocess.run(['qemu-img','create','-q','-f','qcow2',str(source),'32M'],check=True)
        manifest=self.root/'image.json'
        manifest.write_text(json.dumps(dict(schema=1,id='snapshot-test',target='desktop',arch='x86_64',
            boot='bios',cloud_init=True,user='luma',build='No OS disk fixture',
            image=dict(url=source.as_uri(),sha256=digest(source),bytes=source.stat().st_size))))
        session=Session(manifest,self.root/'state')
        cfg=vm.load(session.root);cfg.update(memory=8192,cpus=6,disk_gib=40)
        vm.save(session.root,cfg)
        session.prepare(lambda _:None)
        self.assertEqual(session.key.stat().st_mode & 0o777,0o600)
        session.key.chmod(0o640)
        session.prepare(lambda _:None)
        self.assertEqual(session.key.stat().st_mode & 0o777,0o600)
        command=session.command(graphics='software')
        self.assertEqual(command[command.index('-m')+1],'8192')
        self.assertEqual(command[command.index('-smp')+1],'6')
        info=json.loads(subprocess.check_output(['qemu-img','info','--output=json',str(session.disk)]))
        self.assertEqual(info['virtual-size'],40*1024**3)
        def io(command):
            subprocess.run(['qemu-io','-f','qcow2','-c',command,str(session.disk)],check=True,stdout=subprocess.DEVNULL)
        io('write -P 0x11 0 4096')
        self.assertEqual(vm.checkpoint(session,'Before change')[0]['name'],'Before change')
        io('write -P 0x22 0 4096')
        vm.restore(session,'Before change')
        io('read -P 0x11 0 4096')
        with self.assertRaisesRegex(TesterError,'already exists'):vm.checkpoint(session,'Before change')
        with patch.object(session,'status',return_value='running'),self.assertRaises(TesterError):
            vm.restore(session,'Before change')


if __name__=='__main__':unittest.main()
