# SPDX-License-Identifier: MPL-2.0
from copy import deepcopy
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
from test_luma_disks_backend import payload, parse, P, DISK, B, d
from luma_disks import partitions as p

class PartitionTests(unittest.TestCase):
    def setUp(self):
        enabled = patch.dict(os.environ, {'LUMA_DISKS_ALLOW_WRITES': '1'})
        enabled.start()
        self.addCleanup(enabled.stop)
        self.objects=payload('')
        self.objects[DISK][B+'.Block']['Size']=512*p.MIB
        self.objects[P][B+'.Block']['Size']=256*p.MIB
        self.objects[P][B+'.Partition']['Size']=256*p.MIB
        self.c=d.UDisksClient.__new__(d.UDisksClient)
        self.c.snapshot=Mock(side_effect=lambda:deepcopy(self.objects))
        self.c.call=Mock(return_value=((True,6,''),))
        self.c.GLib=SimpleNamespace(Variant=lambda kind,value:(kind,value))
        self.c.formats=Mock(return_value=['ext4'])
        self.c.check=Mock(return_value=True)
        self.disk=parse(self.objects)[0]; self.v=self.disk.volumes[0]
        self.plan=p.inspect(self.c,self.disk)

    def test_gaps_reserve_table_and_align(self):
        self.assertEqual(self.plan.gaps,((257*p.MIB,254*p.MIB),))

    def test_default_switch_blocks_partition_writes(self):
        with patch.dict(os.environ, {}, clear=True):
            for operation in (
                lambda: p.create(self.c,self.disk,self.plan,257*p.MIB,128*p.MIB,'ext4','new'),
                lambda: p.delete(self.c,self.disk,self.plan,self.v,self.v.name),
                lambda: p.resize(self.c,self.disk,self.plan,self.v,128*p.MIB),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(d.DiskError, 'disabled'):
                        operation()
            self.c.call.assert_not_called()

    def test_create_never_overlaps_partition(self):
        with self.assertRaises(d.DiskError): p.create(self.c,self.disk,self.plan,p.MIB,10*p.MIB,'ext4','new')
        self.c.call.assert_not_called()

    def test_create_uses_combined_service_operation(self):
        p.create(self.c,self.disk,self.plan,257*p.MIB,128*p.MIB,'ext4','new')
        self.assertEqual(self.c.call.call_args.args[2],'CreatePartitionAndFormat')
        self.assertEqual(self.c.call.call_args.args[4][:2],(257*p.MIB,128*p.MIB))

    def test_encrypted_create_passes_luks_options_only_after_validation(self):
        with self.assertRaisesRegex(d.DiskError, 'eight characters'):
            p.create(self.c,self.disk,self.plan,257*p.MIB,128*p.MIB,'ext4','new',
                     encrypt_passphrase='short')
        self.c.call.assert_not_called()
        p.create(self.c,self.disk,self.plan,257*p.MIB,128*p.MIB,'ext4','new',
                 encrypt_passphrase='long-enough-secret')
        options = self.c.call.call_args.args[4][-1]
        self.assertEqual(options['encrypt.type'], ('s', 'luks2'))
        self.assertEqual(options['encrypt.passphrase'], ('s', 'long-enough-secret'))

    def test_stale_layout_blocks_delete(self):
        self.objects[P][B+'.Partition']['Offset']+=p.MIB
        with self.assertRaises(d.DiskError): p.delete(self.c,self.disk,self.plan,self.v,self.v.name)
        self.c.call.assert_not_called()

    def test_changed_table_scheme_invalidates_plan(self):
        self.objects[DISK][B+'.PartitionTable']['Type']='dos'
        with self.assertRaises(d.DiskError): p.delete(self.c,self.disk,self.plan,self.v,self.v.name)
        self.c.call.assert_not_called()

    def test_overlapping_partitions_at_disk_start_are_refused(self):
        self.objects[P][B+'.Partition']['Offset']=0
        self.objects[P][B+'.Block']['Size']=p.MIB
        self.objects[P+'other']=deepcopy(self.objects[P])
        with self.assertRaises(d.DiskError): p.inspect(self.c,self.disk)

    def test_locked_encrypted_sibling_keeps_other_partition_editable(self):
        locked = P + 'vault'
        self.objects[locked] = deepcopy(self.objects[P])
        self.objects[locked][B+'.Partition']['Offset'] = 350 * p.MIB
        self.objects[locked][B+'.Partition']['Size'] = 100 * p.MIB
        self.objects[locked][B+'.Block']['Size'] = 100 * p.MIB
        self.objects[locked][B+'.Block']['IdUsage'] = 'crypto'
        self.objects[locked][B+'.Encrypted'] = {'CleartextDevice': '/'}
        plan = p.inspect(self.c, self.disk)
        self.assertEqual(len(plan.volumes), 2)
        self.objects['/clear'] = {B+'.Block': {'CryptoBackingDevice': locked}}
        with self.assertRaisesRegex(d.DiskError, 'Lock encrypted'):
            p.inspect(self.c, self.disk)

    def test_reinsertion_blocks_create(self):
        self.c.topology_generation=1
        with self.assertRaises(d.DiskError): p.create(self.c,self.disk,self.plan,257*p.MIB,128*p.MIB,'ext4','new')
        self.c.call.assert_not_called()

    def test_delete_requires_exact_name(self):
        with self.assertRaises(d.DiskError): p.delete(self.c,self.disk,self.plan,self.v,self.v.name+' ')
        self.c.call.assert_not_called()
        p.delete(self.c,self.disk,self.plan,self.v,self.v.name)
        self.assertEqual(self.c.call.call_args.args[2],'Delete')

    def test_system_and_mounted_and_readonly_disks_refused(self):
        for field,value in [('MountPoints',[b'/\0']),('MountPoints',[b'/media/test\0'])]:
            self.objects[P][B+'.Filesystem'][field]=value
            with self.assertRaises(d.DiskError): p.inspect(self.c,self.disk)
        self.objects[P][B+'.Filesystem']['MountPoints']=[]
        self.objects[DISK][B+'.Block']['ReadOnly']=True
        with self.assertRaises(d.DiskError): p.inspect(self.c,self.disk)

    def test_active_swap_protects_partition_table(self):
        self.objects[P][B+'.Swapspace']={'Active':True}
        with self.assertRaises(d.DiskError): p.inspect(self.c,self.disk)
        self.c.call.assert_not_called()

    def test_extended_partition_refused(self):
        self.objects[P][B+'.Partition']['IsContainer']=True
        with self.assertRaises(d.DiskError): p.inspect(self.c,self.disk)

    def test_shrink_filesystem_before_partition(self):
        p.resize(self.c,self.disk,self.plan,self.v,128*p.MIB)
        self.assertEqual([(x.args[1],x.args[2]) for x in self.c.call.call_args_list],
                         [('Manager','CanResize'),('Filesystem','Resize'),('Partition','Resize')])

    def test_resize_keeps_original_identity_for_check(self):
        p.resize(self.c,self.disk,self.plan,self.v,128*p.MIB)
        self.c.check.assert_called_once_with(self.v)

    def test_failed_filesystem_shrink_never_shrinks_partition(self):
        self.c.call.side_effect=[((True,6,''),),d.DiskError('too small')]
        with self.assertRaises(d.DiskError): p.resize(self.c,self.disk,self.plan,self.v,128*p.MIB)
        self.assertFalse(any(x.args[1]=='Partition' for x in self.c.call.call_args_list))

    def test_failed_check_never_resizes(self):
        self.c.check.return_value=False
        with self.assertRaises(d.DiskError): p.resize(self.c,self.disk,self.plan,self.v,128*p.MIB)
        self.assertEqual(self.c.call.call_count,1)

    def test_grow_partition_before_filesystem(self):
        def call(path,interface,method,*args):
            if method=='CanResize': return ((True,6,''),)
            if interface=='Partition':
                self.objects[P][B+'.Block']['Size']=384*p.MIB
                self.objects[P][B+'.Partition']['Size']=384*p.MIB
        self.c.call.side_effect=call
        p.resize(self.c,self.disk,self.plan,self.v,384*p.MIB)
        self.assertEqual([(x.args[1],x.args[2]) for x in self.c.call.call_args_list],
                         [('Manager','CanResize'),('Partition','Resize'),('Filesystem','Resize')])

    def test_failed_partition_shrink_reports_safe_partial_state(self):
        self.c.call.side_effect=[((True,6,''),),None,d.DiskError('busy')]
        with self.assertRaisesRegex(d.DiskError,'filesystem was shrunk'):
            p.resize(self.c,self.disk,self.plan,self.v,128*p.MIB)

    def test_layout_change_during_grow_stops_filesystem_write(self):
        def call(path,interface,method,*args):
            if method=='CanResize': return ((True,6,''),)
            if interface=='Partition':
                self.objects[P][B+'.Block']['Size']=384*p.MIB
                self.objects[P][B+'.Block']['IdUUID']='replacement'
        self.c.call.side_effect=call
        with self.assertRaises(d.DiskError): p.resize(self.c,self.disk,self.plan,self.v,384*p.MIB)
        self.assertFalse(any(x.args[1]=='Filesystem' for x in self.c.call.call_args_list))

    def test_replaced_disk_is_refused_before_opening_editor(self):
        self.objects[DISK][B+'.Block']['IdUUID']='replacement'
        with self.assertRaises(d.DiskError): p.inspect(self.c,self.disk)

    def test_unsupported_direction_never_resizes(self):
        self.c.call.return_value=((True,4,''),)
        with self.assertRaises(d.DiskError): p.resize(self.c,self.disk,self.plan,self.v,128*p.MIB)
        self.assertEqual(self.c.call.call_count,1)

    def test_resize_cannot_enter_next_partition(self):
        with self.assertRaises(d.DiskError): p.resize(self.c,self.disk,self.plan,self.v,512*p.MIB)
        self.assertEqual(self.c.call.call_count,1)

if __name__=='__main__': unittest.main()
