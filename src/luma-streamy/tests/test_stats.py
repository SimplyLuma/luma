# SPDX-License-Identifier: Apache-2.0
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from luma_streamy import stats  # noqa: E402

I915 = """pos:\t0
flags:\t02100002
drm-driver:\ti915
drm-client-id:\t{id}
drm-engine-render:\t{render} ns
drm-engine-copy:\t0 ns
drm-engine-video:\t{video} ns
drm-engine-capacity-video:\t2
drm-engine-video-enhance:\t0 ns
"""
XE = """drm-driver:\txe
drm-client-id:\t7
drm-cycles-rcs:\t{busy}
drm-total-cycles-rcs:\t{total}
drm-cycles-vcs:\t{vbusy}
drm-total-cycles-vcs:\t{total}
"""


class StatsTest(unittest.TestCase):
    def test_cpu_and_memory(self):
        busy, total = stats.parse_cpu("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3")
        self.assertEqual((busy, total), (200, 1000))
        used, total = stats.parse_meminfo("MemTotal:       16000000 kB\nMemFree: 1 kB\nMemAvailable:    4000000 kB\n")
        self.assertEqual((used, total), (12000000 * 1024, 16000000 * 1024))

    def test_diskstats(self):
        text = "   8       0 sda 1 2 3 4 5 6 2048 8 0 9 10\n 259       0 nvme0n1 1 2 3 4 5 6 4096 8 0 9 10"
        self.assertEqual(stats.parse_diskstats(text, "nvme0n1"), 4096 * 512)
        self.assertIsNone(stats.parse_diskstats(text, "sdb"))

    def test_i915_engine_busy_counts_each_client_once_and_capacity(self):
        before = {("i915", "1"): stats.parse_fdinfo(I915.format(id=1, render=0, video=0))}
        after = {("i915", "1"): stats.parse_fdinfo(I915.format(id=1, render=250_000_000, video=1_000_000_000))}
        busy = stats.engine_busy(before, after, 1_000_000_000)
        self.assertAlmostEqual(busy["render"], 0.25)
        self.assertAlmostEqual(busy["video"], 0.5)  # two video engines
        self.assertIsNone(stats.parse_fdinfo("pos: 0\nflags: 0"))

    def test_xe_cycles(self):
        before = {("xe", "7"): stats.parse_fdinfo(XE.format(busy=0, vbusy=0, total=1000))}
        after = {("xe", "7"): stats.parse_fdinfo(XE.format(busy=300, vbusy=900, total=2000))}
        busy = stats.engine_busy(before, after, 1_000_000_000)
        self.assertAlmostEqual(busy["rcs"], 0.3)
        self.assertAlmostEqual(busy["vcs"], 0.9)

    def test_sampler_on_a_fake_proc(self):
        root = Path(tempfile.mkdtemp())
        proc = root / "proc"
        (proc / "42/fd").mkdir(parents=True)
        (proc / "42/fdinfo").mkdir()
        os.symlink("/dev/dri/renderD128", proc / "42/fd/5")
        (proc / "42/fdinfo/5").write_text(I915.format(id=3, render=0, video=0))
        (proc / "stat").write_text("cpu  100 0 100 700 100 0 0 0\n")
        (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
        (proc / "diskstats").write_text("")
        sampler = stats.Sampler(root, proc=str(proc), sys=str(root / "sys"))
        first = sampler.sample()
        self.assertIsNone(first.cpu)
        self.assertEqual(first.ram_used, 750 * 1024)
        (proc / "stat").write_text("cpu  150 0 150 750 150 0 0 0\n")
        (proc / "42/fdinfo/5").write_text(I915.format(id=3, render=10**12, video=10**12))
        second = sampler.sample()
        self.assertAlmostEqual(second.cpu, 0.5)
        self.assertEqual(second.gpu, 1.0)
        self.assertEqual(second.encoder, 1.0)
        self.assertIsNotNone(second.disk_free)

    def test_words(self):
        self.assertEqual(stats.format_bytes(2_500_000_000), "2.5 GB")
        self.assertEqual(stats.percent(0.234), "23 %")
        self.assertEqual(stats.percent(None), "—")


if __name__ == "__main__":
    unittest.main()
