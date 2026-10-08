# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-disks'))
from luma_disks.model import VolumeView, map_weights


class MapTests(unittest.TestCase):
    def test_tiny_partition_remains_visible_and_track_sums_to_one(self):
        rows = tuple(VolumeView(str(i), str(i), 'blue', size, 0, size, 'ext4', None,
                                '', '', '') for i, size in enumerate((629_000_000, 509_000_000_000)))
        widths = map_weights(rows, 512_000_000_000)
        self.assertAlmostEqual(sum(widths), 1)
        self.assertGreaterEqual(widths[0], .038)


if __name__ == '__main__':
    unittest.main()
