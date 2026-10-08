# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
from dataclasses import replace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-disks'))
from luma_disks.model import display_size, fixture_drives, live_drives, volume_rows
from test_luma_disks_backend import parse, payload


class ModelTests(unittest.TestCase):
    def test_fixture_is_exact_v70_sample_and_in_memory(self):
        path = Path(__file__).resolve().parents[1] / 'fixtures/disks-v70.json'
        drives = fixture_drives(path)
        self.assertEqual([d.name for d in drives], ['Luma SSD', 'Archive HD', 'USB stick'])
        self.assertEqual([len(d.volumes) for d in drives], [3, 3, 1])
        self.assertEqual(drives[0].volumes[2].free, 295_000_000_000)
        self.assertEqual(drives[1].health_state, 'warn')
        self.assertEqual(drives[1].health_rows[0], ('Bad sectors', '3', True))
        self.assertIsNone(drives[0].source)
        self.assertIsNone(drives[0].volumes[0].source)

    def test_live_view_does_not_invent_speed_or_health_facts(self):
        drives = live_drives(parse(payload('')))
        self.assertEqual(len(drives), 1)
        self.assertIsNone(drives[0].speed_read)
        self.assertIsNone(drives[0].volumes[0].used)
        self.assertEqual(drives[0].volumes[0].name, 'Archive')

    def test_free_space_row_uses_unallocated_capacity(self):
        path = Path(__file__).resolve().parents[1] / 'fixtures/disks-v70.json'
        drives = fixture_drives(path)
        self.assertEqual(len(volume_rows(drives[0])), 3)  # 270 MB: below v70 threshold
        self.assertEqual(volume_rows(drives[1])[-1].size, 20_000_000_000)
        self.assertEqual(volume_rows(drives[2])[-1].size, 57_800_000_000)

    def test_v70_sizes_use_coarse_disk_labels(self):
        self.assertEqual(display_size(630_000_000), '630 MB')
        self.assertEqual(display_size(2_100_000_000), '2 GB')
        self.assertEqual(display_size(6_200_000_000), '6 GB')
        self.assertEqual(display_size(2_000_000_000_000), '2 TB')

    def test_live_fragmented_space_has_distinct_selected_offsets(self):
        disk = parse(payload(''))[0]
        first = replace(disk.volumes[0], offset=1024 * 1024, size=1_000_000_000)
        second = replace(first, path='/second', name='Second', offset=3_000_000_000)
        view = live_drives((replace(disk, size=8_000_000_000,
                                    volumes=(first, second)),))[0]
        gaps = [volume for volume in volume_rows(view) if volume.tone == 'free']
        self.assertGreaterEqual(len(gaps), 2)
        self.assertNotEqual(gaps[0].free_offset, gaps[1].free_offset)
        self.assertNotEqual(gaps[0].key, gaps[1].key)


if __name__ == '__main__':
    unittest.main()
