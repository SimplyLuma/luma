# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from luma_monitor.live_data import process_groups,resource_facts


class LiveDataTests(unittest.TestCase):
    def test_groups_partition_real_processes_without_duplicate_kernel_parent(self):
        def process(pid,name,ppid=1):
            return SimpleNamespace(pid=pid,name=name,ppid=ppid,uid=1000,cpu=None,rss=None,threads=1)
        groups=process_groups([process(2,'kthreadd'),process(3,'kworker',2),process(8,'pipewire'),process(9,'helper'),process(10,'app')],{10},{1000:'nick'})
        self.assertEqual({g['id']:g['total'] for g in groups},{'sys':1,'bg':1,'kern':2})
        self.assertEqual(sorted(p[1] for g in groups for p in g['p']),[2,3,8,9])
        self.assertTrue(all(p[4] is None for g in groups for p in g['p']))

    def test_memory_facts_use_measured_bytes_and_leave_missing_data_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            facts=resource_facts({'memory':{'Cached':1024**3,'SwapTotal':2*1024**3,'SwapFree':1024**3,'Committed_AS':3*1024**3}},proc=temporary,sys=temporary)
        self.assertEqual(dict(facts['mem']),{'Cached':'1.0 GB','Swap':'1.0 GB of 2.0 GB','zram':'—','Committed':'3.0 GB'})
        self.assertTrue(all(value=='—' for key,value in facts['en']))

    def test_root_queue_network_packets_and_compression_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);proc=root/'proc';sys=root/'sys'
            def put(path,text):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
            put(proc/'self/mountinfo','42 1 259:1 / / rw - btrfs /dev/nvme0n1p1 rw\n')
            put(sys/'devices/nvme0n1p1/inflight','1 2\n')
            (sys/'dev/block').mkdir(parents=True);(sys/'dev/block/259:1').symlink_to('../../devices/nvme0n1p1')
            put(proc/'net/route','Iface Destination Gateway Flags\nwlan0 00000000 0100000A 0003\n')
            put(proc/'net/dev',' wlan0: 123 7 0 0 0 0 0 0 456 9 0 0 0 0 0 0\n')
            put(sys/'class/net/wlan0/speed','1200\n');put(sys/'block/zram0/mm_stat',f'{1024**3} {380*1024**2} 0\n')
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            facts=resource_facts({'memory':{},'battery':{'draw':12.8,'health':94.,'cycles':212}},proc=proc,sys=sys)
            self.assertEqual(dict(facts['disk']),{'Device':'nvme0n1p1','Root':'btrfs · /','Queue':'3','Busy':'—'})
            self.assertEqual(dict(facts['net'])['Packets'],'7 in · 9 out')
            self.assertEqual(dict(facts['net'])['Link'],'1.2 Gb/s')
            self.assertEqual(dict(facts['mem'])['zram'],'1.0 GB → 380 MB')
            self.assertEqual(dict(facts['en'])['Draw'],'12.8 W')
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})
