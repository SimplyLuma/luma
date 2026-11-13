# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest
from pathlib import Path

from luma_monitor.gpu import GpuSampler, app_share, parse_fdinfo

# fdinfo as each driver prints it (Linux 6.12-6.17), two readings one second
# apart. Only the counters differ between the two readings of a client.
I915 = """pos:	0
flags:	02100002
mnt_id:	26
ino:	1072
drm-driver:	i915
drm-client-id:	{client}
drm-pdev:	0000:00:02.0
drm-total-system0:	72 MiB
drm-shared-system0:	0
drm-active-system0:	0
drm-resident-system0:	72 MiB
drm-purgeable-system0:	1012 KiB
drm-engine-render:	{render} ns
drm-engine-copy:	0 ns
drm-engine-video:	{video} ns
drm-engine-capacity-video:	2
drm-engine-video-enhance:	0 ns
"""
XE = """pos:	0
flags:	02100002
mnt_id:	26
ino:	1085
drm-driver:	xe
drm-client-id:	{client}
drm-pdev:	0000:00:02.0
drm-total-gtt:	48 MiB
drm-shared-gtt:	0
drm-active-gtt:	0
drm-resident-gtt:	48 MiB
drm-cycles-rcs:	{rcs}
drm-total-cycles-rcs:	{total}
drm-cycles-bcs:	0
drm-total-cycles-bcs:	{total}
drm-cycles-vcs:	0
drm-total-cycles-vcs:	{total}
drm-engine-capacity-vcs:	2
drm-cycles-ccs:	0
drm-total-cycles-ccs:	{total}
"""
AMDGPU = """pos:	0
flags:	02100002
mnt_id:	26
ino:	1101
drm-driver:	amdgpu
drm-client-id:	{client}
drm-pdev:	0000:03:00.0
pasid:	32770
drm-total-vram:	221184 KiB
drm-shared-vram:	0
drm-resident-vram:	221184 KiB
drm-total-gtt:	8192 KiB
drm-resident-gtt:	8192 KiB
amd-evicted-vram:	0 KiB
drm-engine-gfx:	{gfx} ns
drm-engine-compute:	0 ns
drm-engine-dma:	0 ns
drm-engine-dec:	{dec} ns
drm-engine-enc:	0 ns
"""
MSM = """pos:	0
flags:	02100002
mnt_id:	24
ino:	612
drm-driver:	msm
drm-client-id:	{client}
drm-engine-gpu:	{gpu} ns
drm-cycles-gpu:	{cycles}
drm-maxfreq-gpu:	900000000 Hz
drm-curfreq-gpu:	{freq} Hz
drm-total-memory:	96 MiB
drm-shared-memory:	0
drm-active-memory:	0
drm-resident-memory:	96 MiB
drm-purgeable-memory:	0
"""
# virtio-gpu has no fdinfo hook: only the file's generic lines.
VIRTIO = """pos:	0
flags:	02100002
mnt_id:	26
ino:	735
"""


class Machine:
    """A /proc and /sys with one GPU and processes holding its nodes."""

    def __init__(self, driver, device='pci0000:00/0000:00:02.0', amd=None):
        self.root = tempfile.TemporaryDirectory()
        root = Path(self.root.name)
        self.proc, self.sys = root / 'proc', root / 'sys'
        dev = self.sys / 'devices' / device
        dev.mkdir(parents=True)
        (self.sys / 'bus/drivers' / driver).mkdir(parents=True)
        os.symlink(self.sys / 'bus/drivers' / driver, dev / 'driver')
        for node in ('card0', 'renderD128'):
            (self.sys / 'class/drm' / node).mkdir(parents=True)
            os.symlink(dev, self.sys / 'class/drm' / node / 'device')
        for name, value in (amd or {}).items():
            (dev / name).write_text(f'{value}\n')
        self.dev = dev

    def hold(self, pid, fd, text, node='renderD128'):
        directory = self.proc / str(pid)
        (directory / 'fd').mkdir(parents=True, exist_ok=True)
        (directory / 'fdinfo').mkdir(exist_ok=True)
        link = directory / 'fd' / str(fd)
        if link.is_symlink():
            link.unlink()
        os.symlink('/dev/dri/' + node, link)
        (directory / 'fdinfo' / str(fd)).write_text(text)

    def sampler(self):
        return GpuSampler(self.proc, self.sys)


S = 1_000_000_000


class ParseTest(unittest.TestCase):
    def test_drivers(self):
        i915 = parse_fdinfo(I915.format(client=7, render=10, video=4))
        self.assertEqual((i915['driver'], i915['id'], i915['pdev']), ('i915', 7, '0000:00:02.0'))
        self.assertEqual(i915['busy'], {'render': 10, 'copy': 0, 'video': 4, 'video-enhance': 0})
        self.assertEqual(i915['capacity'], {'video': 2})
        self.assertEqual(i915['memory'], 72 * 1024 ** 2)
        xe = parse_fdinfo(XE.format(client=3, rcs=5, total=9))
        self.assertEqual(xe['cycles']['rcs'], 5)
        self.assertEqual(xe['total_cycles']['ccs'], 9)
        self.assertEqual(parse_fdinfo(AMDGPU.format(client=1, gfx=1, dec=0))['memory'], (221184 + 8192) * 1024)
        msm = parse_fdinfo(MSM.format(client=2, gpu=8, cycles=1, freq=1))
        self.assertEqual((msm['driver'], msm['pdev'], msm['busy']), ('msm', None, {'gpu': 8}))
        self.assertEqual(msm['memory'], 96 * 1024 ** 2)
        # Not a DRM client at all.
        self.assertIsNone(parse_fdinfo(VIRTIO))
        self.assertIsNone(parse_fdinfo('pos:\t0\nflags:\t02\n'))


class SampleTest(unittest.TestCase):
    def machine(self, *args, **kwargs):
        machine = Machine(*args, **kwargs)
        self.addCleanup(machine.root.cleanup)
        return machine

    def test_intel_i915(self):
        machine = self.machine('i915')
        machine.hold(100, 5, I915.format(client=7, render=2 * S, video=0))
        machine.hold(100, 9, I915.format(client=7, render=2 * S, video=0))  # dup of the same client
        machine.hold(200, 4, I915.format(client=8, render=0, video=0))
        sampler = machine.sampler()
        devices, shares = sampler.sample([100, 200], 10 * S)
        self.assertEqual(devices[0]['name'], 'Intel graphics')
        self.assertTrue(devices[0]['available'])
        self.assertIsNone(devices[0]['busy'])  # no interval yet: unmeasured
        self.assertIsNone(shares[100])
        machine.hold(100, 5, I915.format(client=7, render=2 * S + 300_000_000, video=0))
        machine.hold(100, 9, I915.format(client=7, render=2 * S + 300_000_000, video=0))
        machine.hold(200, 4, I915.format(client=8, render=100_000_000, video=S))
        devices, shares = sampler.sample([100, 200], 11 * S)
        gpu = devices[0]
        self.assertAlmostEqual(gpu['busy'], 50.0)  # video: 1 s over two engines
        self.assertAlmostEqual(gpu['engines']['Render'], 40.0)
        self.assertAlmostEqual(gpu['engines']['Video'], 50.0)
        self.assertEqual(gpu['memory'], 2 * 72 * 1024 ** 2)  # two clients, the dup counted once
        self.assertAlmostEqual(app_share(shares, [100]), 30.0)
        self.assertAlmostEqual(app_share(shares, [200]), 50.0)
        self.assertAlmostEqual(app_share(shares, [100, 200]), 50.0)

    def test_intel_xe_cycles(self):
        machine = self.machine('xe')
        machine.hold(100, 5, XE.format(client=3, rcs=1000, total=10_000))
        sampler = machine.sampler()
        sampler.sample([100], 0)
        machine.hold(100, 5, XE.format(client=3, rcs=1250, total=11_000))
        devices, shares = sampler.sample([100], S)
        self.assertAlmostEqual(devices[0]['busy'], 25.0)
        self.assertAlmostEqual(devices[0]['engines']['Render'], 25.0)
        self.assertEqual(devices[0]['engines']['Video'], 0.0)
        self.assertAlmostEqual(app_share(shares, [100]), 25.0)

    def test_amdgpu_prefers_device_counters(self):
        machine = self.machine('amdgpu', 'pci0000:00/0000:00:01.1/0000:03:00.0',
                          amd={'gpu_busy_percent': 63, 'mem_info_vram_used': 3 * 1024 ** 3,
                               'mem_info_vram_total': 8 * 1024 ** 3})
        machine.hold(100, 5, AMDGPU.format(client=1, gfx=0, dec=0))
        sampler = machine.sampler()
        devices, _ = sampler.sample([100], 0)
        self.assertEqual(devices[0]['busy'], 63.0)  # device-wide, readable at once
        self.assertEqual((devices[0]['memory'], devices[0]['memory_total']), (3 * 1024 ** 3, 8 * 1024 ** 3))
        machine.hold(100, 5, AMDGPU.format(client=1, gfx=600_000_000, dec=0))
        devices, shares = sampler.sample([100], S)
        self.assertEqual(devices[0]['name'], 'AMD graphics')
        self.assertAlmostEqual(devices[0]['engines']['Render'], 60.0)
        self.assertAlmostEqual(app_share(shares, [100]), 60.0)

    def test_qualcomm_msm(self):
        # The DRM device is the display subsystem; fdinfo names the driver.
        machine = self.machine('msm_dpu', 'platform/soc@0/ae00000.display-subsystem')
        machine.hold(100, 5, MSM.format(client=2, gpu=5 * S, cycles=1, freq=300000000))
        machine.hold(300, 7, MSM.format(client=4, gpu=0, cycles=0, freq=300000000))
        sampler = machine.sampler()
        sampler.sample([100, 300], 0)
        machine.hold(100, 5, MSM.format(client=2, gpu=5 * S + 200_000_000, cycles=2, freq=800000000))
        machine.hold(300, 7, MSM.format(client=4, gpu=150_000_000, cycles=1, freq=800000000))
        devices, shares = sampler.sample([100, 300], S)
        self.assertEqual((devices[0]['name'], devices[0]['driver']), ('Adreno', 'msm'))
        self.assertAlmostEqual(devices[0]['busy'], 35.0)
        self.assertAlmostEqual(app_share(shares, [300]), 15.0)

    def test_virtio_gpu_is_not_available(self):
        # As in a QEMU VM: the render node's device is the PCI function, bound to virtio-pci.
        machine = self.machine('virtio-pci', 'pci0000:00/0000:00:01.0')
        machine.hold(100, 5, VIRTIO)
        sampler = machine.sampler()
        sampler.sample([100], 0)
        devices, shares = sampler.sample([100], S)
        self.assertEqual(devices[0]['name'], 'Virtual GPU')
        self.assertFalse(devices[0]['available'])
        self.assertIsNone(devices[0]['busy'])
        self.assertEqual(devices[0]['engines'], {})

    def test_no_render_node_no_gpu(self):
        machine = self.machine('bochs-drm')
        os.unlink(machine.sys / 'class/drm/renderD128/device')
        (machine.sys / 'class/drm/renderD128').rmdir()
        devices, _ = machine.sampler().sample([], 0)
        self.assertEqual(devices, [])

    def test_reset_and_closed_clients(self):
        machine = self.machine('i915')
        machine.hold(100, 5, I915.format(client=7, render=5 * S, video=0))
        sampler = machine.sampler()
        sampler.sample([100], 0)
        # A new client and a counter that went backwards have no interval.
        machine.hold(100, 5, I915.format(client=9, render=8 * S, video=0))
        devices, shares = sampler.sample([100], S)
        self.assertEqual(devices[0]['busy'], 0.0)
        self.assertEqual(app_share(shares, [100]), 0.0)
        # An unreadable process leaves its app unmeasured.
        self.assertIsNone(app_share(sampler.sample([100, 999], 2 * S)[1], [100, 999]))


if __name__ == '__main__':
    unittest.main()
