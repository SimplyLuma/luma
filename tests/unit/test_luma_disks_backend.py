# SPDX-License-Identifier: MPL-2.0
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from dataclasses import replace
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-disks'))
from luma_disks import backend as d

B = d.BUS
P = d.ROOT + '/block_devices/sda1'
DRIVE = d.ROOT + '/drives/test'
DISK = d.ROOT + '/block_devices/sda'


def payload(mount='/media/test'):
    return {
        DRIVE: {B+'.Drive': {'Model':'Test drive', 'ConnectionBus':'usb'}, B+'.Drive.Ata': {'SmartSupported':True, 'SmartUpdated':10, 'SmartNumBadSectors':0}},
        DISK: {B+'.Block': {'Size':1000000000,'Device':list(b'/dev/sda\0'),'Drive':DRIVE}, B+'.PartitionTable':{'Type':'gpt'}},
        P: {B+'.Block': {'Size':900000000, 'Device':list(b'/dev/sda1\0'), 'DeviceNumber':2049,'IdLabel':'Archive','IdUUID':'test','IdUsage':'filesystem','IdType':'ext4','Drive':DRIVE}, B+'.Partition':{'Table':DISK, 'Offset':1048576}, B+'.Filesystem':{'MountPoints':[list(mount.encode()+b'\0')] if mount else []}}
    }


def parse(o):
    return d.parse_disks(o, stat=lambda _: SimpleNamespace(f_blocks=100000, f_bfree=60000, f_bavail=59000, f_frsize=4096))


class DataTests(unittest.TestCase):
    def test_recorded_nvme_snapshot(self):
        path=Path(__file__).resolve().parents[1]/'fixtures/disks/udisks-2.11.2-nvme.json'
        objects=json.loads(path.read_text())
        disks=parse(objects)
        self.assertEqual(len(disks),1)
        self.assertEqual(len(disks[0].volumes),3)
        self.assertEqual(disks[0].size,512110190592)
        self.assertTrue(all(v.protected for v in disks[0].volumes))

    def test_owned_loop_is_not_system_but_root_mount_still_is(self):
        o=payload('')
        o[DISK][B+'.Loop']={'SetupByUID':os.getuid(),'BackingFile':b'/tmp/test.img\0'}
        for path in (P,DISK): o[path][B+'.Block']['HintSystem']=True
        self.assertNotIn(DISK,d.protected_paths(o))
        o[P][B+'.Filesystem']['MountPoints']=[b'/\0']
        self.assertIn(DISK,d.protected_paths(o))
        o[P][B+'.Filesystem']['MountPoints']=[]
        o[DISK][B+'.Loop']['SetupByUID']=os.getuid()+1
        self.assertIn(DISK,d.protected_paths(o))

    def test_decimal_sizes(self):
        self.assertEqual(d.size_text(1_400_000_000_000), '1.4 TB')
        self.assertEqual(d.size_text(509_000_000_000), '509 GB')
        self.assertEqual(d.size_text(629_000_000), '629 MB')
        self.assertEqual(d.size_text(None), 'Not measured')

    def test_mounted_and_unmounted_space_is_honest(self):
        disk = parse(payload())[0]
        self.assertEqual(disk.volumes[0].used, 40000*4096)
        self.assertEqual(disk.volumes[0].free, 59000*4096)
        self.assertIsNone(parse(payload(''))[0].free)
        self.assertEqual(disk.unused,100000000)

    def test_root_and_parent_protected(self):
        o = payload('/')
        self.assertEqual(d.protected_paths(o), {P,DISK})
        self.assertTrue(parse(o)[0].volumes[0].erase_reason)

    def test_nested_root_protects_crypto_backing(self):
        o=payload('')
        o['/clear']={B+'.Block':{'CryptoBackingDevice':P}, B+'.Filesystem':{'MountPoints':[b'/\0']}}
        self.assertTrue({P,DISK,'/clear'} <= d.protected_paths(o))

    def test_no_smart_is_not_failure(self):
        self.assertEqual(d.health_report({}).state,'none')
        self.assertEqual(d.health_report({B+'.Drive.Ata':{'SmartSupported':True}}).title,'Health report unavailable')

    def test_btrfs_write_policy_requires_single_device_evidence(self):
        o=payload('')
        o[P][B+'.Block']['IdType']='btrfs'
        self.assertTrue(parse(o)[0].volumes[0].erase_reason)
        o[P][B+'.Filesystem.BTRFS']={'NumDevices':2}
        self.assertTrue(parse(o)[0].volumes[0].erase_reason)
        o[P][B+'.Filesystem.BTRFS']['NumDevices']=1
        self.assertFalse(parse(o)[0].volumes[0].erase_reason)

    def test_bad_sectors_and_failure(self):
        p = {B+'.Drive.Ata':{'SmartSupported':True,'SmartUpdated':1,'SmartNumBadSectors':3}}
        self.assertEqual(d.health_report(p).state,'warn')
        p[B+'.Drive.Ata']['SmartFailing']=True
        self.assertEqual(d.health_report(p).state,'fail')

    def test_nvme_reliability_warning_is_failing(self):
        self.assertEqual(d.health_report({B+'.NVMe.Controller':{'SmartUpdated':1,'SmartCriticalWarning':['degraded']}}).state,'fail')

    def test_tiny_arcs_do_not_overlap(self):
        volumes=[SimpleNamespace(size=999999,used=0),SimpleNamespace(size=1,used=1)]
        segments=d.ring_segments(volumes,1000000)
        self.assertAlmostEqual(sum(s[1] for s in segments),1)
        self.assertGreater(segments[1][1],.019)


class GuardTests(unittest.TestCase):
    def setUp(self):
        enabled = patch.dict(os.environ, {'LUMA_DISKS_ALLOW_WRITES': '1'})
        enabled.start()
        self.addCleanup(enabled.stop)

    def client(self,o):
        c=d.UDisksClient.__new__(d.UDisksClient)
        c.snapshot=Mock(return_value=o)
        c.call=Mock()
        c.formats=Mock(return_value=['ext4'])
        c.GLib=SimpleNamespace(Variant=lambda kind,value:(kind,value))
        return c

    def test_unavailable_format_fails_before_unmount(self):
        objects = payload()
        client = self.client(objects)
        client.formats.return_value = []
        with self.assertRaisesRegex(d.DiskError, 'unavailable'):
            client.erase(parse(objects)[0].volumes[0], 'Archive', 'ext4', 'New')
        client.call.assert_not_called()

    def test_default_switch_blocks_every_state_changing_entry(self):
        objects = payload('')
        disk = parse(objects)[0]
        volume = disk.volumes[0]
        client = self.client(objects)
        with patch.dict(os.environ, {}, clear=True):
            for operation in (
                lambda: client.mount(volume),
                lambda: client.rename(volume, 'New'),
                lambda: client.check(volume),
                lambda: client.erase(volume, volume.name, 'ext4', 'New'),
                lambda: client.save_image(disk, '/tmp/never-created.img', lambda _: None, threading.Event()),
                lambda: client.attach_image('/tmp/never-opened.img'),
                lambda: client.remove_disk(disk),
                lambda: client.unlock(volume, 'secret'),
                lambda: client.lock(volume),
                lambda: client.change_passphrase(volume, 'old', 'new', '/tmp/never-header.img'),
                lambda: client.save_partition_image(volume, '/tmp/never-part.img',
                                                    lambda _: None, threading.Event()),
                lambda: client.restore_image(disk, '/tmp/never-source.img',
                                             '/tmp/never-backup.img', lambda _: None,
                                             threading.Event()),
                lambda: client.start_selftest(disk),
                lambda: client.set_drive_settings(disk, standby_minutes=10, write_cache=True),
                lambda: client.set_mount_configuration(volume, directory='/mnt/test',
                                                        at_startup=True, read_only=False,
                                                        show_in_filer=True),
                lambda: client.format_disk(disk, '/tmp/never-backup.img', disk.name,
                                           'ext4', 'New', lambda _: None, threading.Event()),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(d.DiskError, 'disabled'):
                        operation()
            client.call.assert_not_called()

    def test_raw_bus_call_is_fail_closed(self):
        client = d.UDisksClient.__new__(d.UDisksClient)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(d.DiskError, 'disabled'):
                client.call(P, 'Block', 'Format')

    def test_restore_cannot_open_target_for_writing_before_complete_backup(self):
        objects = payload('')
        disk = parse(objects)[0]
        client = self.client(objects)
        client.save_image = Mock(side_effect=d.DiskError('Backup failed'))
        client.connection = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'source.img'
            image.write_bytes(b'example')
            with self.assertRaisesRegex(d.DiskError, 'Backup failed'):
                client.restore_image(disk, image, Path(tmp) / 'backup.img',
                                     lambda _: None, threading.Event())
        client.connection.call_with_unix_fd_list_sync.assert_not_called()
        client.call.assert_not_called()

    def test_format_disk_cannot_write_before_complete_backup(self):
        objects = payload('')
        disk = parse(objects)[0]
        client = self.client(objects)
        client._current_drive_objects = Mock(return_value=objects)
        client.save_image = Mock(side_effect=d.DiskError('Backup failed'))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(d.DiskError, 'Backup failed'):
                client.format_disk(disk, Path(tmp) / 'backup.img', disk.name,
                                   'ext4', 'New', lambda _: None, threading.Event())
        client.call.assert_not_called()

    def test_passphrase_change_requires_header_backup_first(self):
        objects = payload('')
        volume = replace(parse(objects)[0].volumes[0], encrypted=True, locked=True)
        client = self.client(objects)
        client._encrypted = Mock(return_value=volume)
        with tempfile.TemporaryDirectory() as tmp:
            client.change_passphrase(volume, 'previous secret', 'replacement secret',
                                     Path(tmp) / 'header.img')
        self.assertEqual([call.args[2] for call in client.call.call_args_list],
                         ['HeaderBackup', 'ChangePassphrase'])

    def test_unlock_mounts_only_verified_cleartext_device(self):
        objects = payload('')
        volume = replace(parse(objects)[0].volumes[0], encrypted=True, locked=True)
        client = self.client(objects)
        client._encrypted = Mock(return_value=volume)
        objects['/clear'] = {B+'.Block': {'CryptoBackingDevice': P}, B+'.Filesystem': {}}
        client.call.side_effect = [('/clear',), ('/run/media/test',)]
        client.unlock(volume, 'secret')
        self.assertEqual([call.args[2] for call in client.call.call_args_list],
                         ['Unlock', 'Mount'])
        client.call.reset_mock(side_effect=True)
        client.call.return_value = ('/foreign',)
        with self.assertRaisesRegex(d.DiskError, 'could not be verified'):
            client.unlock(volume, 'secret')
        self.assertEqual(client.call.call_count, 1)

    def test_root_never_formats(self):
        o=payload('/')
        c=self.client(o)
        with self.assertRaises(d.DiskError): c.erase(parse(o)[0].volumes[0],'Archive','ext4','New')
        c.call.assert_not_called()

    def test_exact_confirmation_no_trim(self):
        o=payload('')
        c=self.client(o)
        with self.assertRaises(d.DiskError): c.erase(parse(o)[0].volumes[0],'Archive ','ext4','New')
        c.call.assert_not_called()

    def test_failed_unmount_never_formats(self):
        o=payload()
        c=self.client(o)
        c.call.side_effect=d.DiskError('Busy')
        with self.assertRaises(d.DiskError): c.erase(parse(o)[0].volumes[0],'Archive','ext4','New')
        self.assertEqual(c.call.call_args.args[2],'Unmount')
        self.assertEqual(c.call.call_count,1)

    def test_successful_erase_revalidates_then_formats(self):
        o=payload()
        c=self.client(o)
        c.snapshot.side_effect=lambda:o
        def unmount(*args):
            if args[2]=='Unmount': o[P][B+'.Filesystem']['MountPoints']=[]
        c.call.side_effect=unmount
        c.erase(parse(o)[0].volumes[0],'Archive','ext4','New')
        self.assertEqual([call.args[2] for call in c.call.call_args_list],['Unmount','Format'])
        opts=c.call.call_args.args[4][1]
        self.assertEqual(opts['no-discard'],('b',True))
        self.assertNotIn('erase',opts)
        self.assertEqual(opts['take-ownership'],('b',True))
        self.assertEqual(opts['update-partition-type'],('b',True))

    def test_disconnected_volume_never_formats(self):
        c=self.client({})
        with self.assertRaises(d.DiskError): c.erase(parse(payload())[0].volumes[0],'Archive','ext4','New')
        c.call.assert_not_called()

    def test_repair_requires_typed_name(self):
        o=payload('')
        c=self.client(o)
        with self.assertRaises(d.DiskError): c.check(parse(o)[0].volumes[0],repair=True)
        c.call.assert_not_called()

    def test_reinserted_clone_requires_new_selection(self):
        o=payload('')
        c=self.client(o)
        c.topology_generation=2
        old=d.replace(parse(o)[0].volumes[0],identity=parse(o)[0].volumes[0].identity+(1,))
        with self.assertRaises(d.DiskError): c.erase(old,'Archive','ext4','New')
        c.call.assert_not_called()

    def test_readonly_filesystem_cannot_be_renamed(self):
        o=payload(''); o[P][B+'.Block']['IdType']='iso9660'
        c=self.client(o)
        with self.assertRaises(d.DiskError): c.rename(parse(o)[0].volumes[0],'Name')
        c.call.assert_not_called()

    def test_loop_hold_restores_autoclear_and_closes_descriptor(self):
        o=payload('')
        o[DISK][B+'.Loop']={'SetupByUID':os.getuid(),'BackingFile':b'/tmp/test.img\0','Autoclear':False}
        c=self.client(o)
        c.Gio=SimpleNamespace(DBusCallFlags=SimpleNamespace(ALLOW_INTERACTIVE_AUTHORIZATION=1))
        fd,writer=os.pipe();os.close(writer)
        c.connection=Mock()
        result=Mock(); result.unpack.return_value=(0,)
        fds=Mock(); fds.get.return_value=fd
        c.connection.call_with_unix_fd_list_sync.return_value=(result,fds)
        with c.hold_loop(P):
            os.fstat(fd)
            o[DISK][B+'.Loop']['Autoclear']=True
        self.assertEqual(c.call.call_args.args[2],'SetAutoclear')
        with self.assertRaises(OSError): os.fstat(fd)

    def test_device_open_is_always_read_only(self):
        c=d.UDisksClient.__new__(d.UDisksClient)
        c.GLib=SimpleNamespace(Variant=lambda kind,value:value)
        c.Gio=SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0,ALLOW_INTERACTIVE_AUTHORIZATION=1))
        c.connection=Mock()
        result=Mock();result.unpack.return_value=(0,)
        fds=Mock();fds.get.return_value=42
        c.connection.call_with_unix_fd_list_sync.return_value=(result,fds)
        self.assertEqual(c.open_read(P,benchmark=True),42)
        args=c.connection.call_with_unix_fd_list_sync.call_args.args
        self.assertEqual(args[4][0],'r')
        self.assertTrue(args[4][1]['flags'] & os.O_DIRECT)

    def test_benchmark_reports_total_bytes_over_total_time(self):
        times=[0,1,1,3]
        for i in range(64): times.extend((10+i,10+i+.01))
        with patch.object(d.os,'preadv',side_effect=lambda fd,views,offset:len(views[0])), patch.object(d.time,'perf_counter',side_effect=times):
            rate,latency=d.read_benchmark(0,2*1024*1024,lambda _:None,threading.Event(),samples=2)
        self.assertAlmostEqual(rate,2*1024*1024/3)
        self.assertAlmostEqual(latency,10)

    def test_read_benchmark_does_not_write(self):
        with tempfile.TemporaryFile() as f:
            original=b'x'*(2*1024*1024)
            f.write(original); f.flush()
            progress=[]
            rate,latency=d.read_benchmark(f.fileno(),len(original),progress.append,threading.Event(),samples=4)
            f.seek(0)
            self.assertEqual(f.read(),original)
            self.assertEqual(len(progress),4)
            self.assertGreater(rate,0)

    def test_image_never_overwrites_existing_file(self):
        o=payload('')
        c=self.client(o)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryFile() as source:
            target=Path(tmp)/'backup.img'
            target.write_bytes(b'keep this backup')
            c.open_read=Mock(return_value=os.dup(source.fileno()))
            with self.assertRaises(FileExistsError):
                c.save_image(parse(o)[0],target,lambda _:None,threading.Event())
            self.assertEqual(target.read_bytes(),b'keep this backup')

    def test_interrupted_image_removes_only_new_partial_file(self):
        o=payload('')
        c=self.client(o)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryFile() as source:
            target=Path(tmp)/'new.img'
            c.open_read=Mock(return_value=os.dup(source.fileno()))
            cancel=threading.Event();cancel.set()
            with self.assertRaises(d.DiskError):
                c.save_image(parse(o)[0],target,lambda _:None,cancel)
            self.assertFalse(target.exists())

    def test_disk_image_is_private_even_with_permissive_umask(self):
        o=payload('')
        o[DISK][B+'.Block']['Size']=64
        c=self.client(o)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryFile() as source:
            source.write(b'x'*64);source.seek(0)
            c.open_read=Mock(return_value=os.dup(source.fileno()))
            target=Path(tmp)/'private.img'
            previous=os.umask(0)
            try: c.save_image(parse(o)[0],target,lambda _:None,threading.Event())
            finally: os.umask(previous)
            self.assertEqual(target.read_bytes(),b'x'*64)
            self.assertEqual(target.stat().st_mode & 0o777,0o600)

    def test_polkit_denial_has_plain_sentence_and_technical_detail(self):
        error=d.explain_error(RuntimeError('org.freedesktop.UDisks2.Error.NotAuthorized: denied'))
        self.assertIn('Permission was not granted',str(error))
        self.assertIn('NotAuthorized',error.detail)

if __name__=='__main__': unittest.main()
