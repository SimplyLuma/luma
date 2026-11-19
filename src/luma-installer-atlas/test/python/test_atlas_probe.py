# SPDX-License-Identifier: LGPL-2.1-or-later
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

source = Path(os.environ.get('ATLAS_PROBE_SOURCE', Path(__file__).resolve().parents[2] / 'libexec/atlas-probe'))
loader = importlib.machinery.SourceFileLoader('tested_atlas_probe', str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
probe = importlib.util.module_from_spec(spec)
loader.exec_module(probe)


def pci(address, vendor='0x8086', device='0x0000', cls='0x010601', driver=None, remapped=0):
    return {'address': address, 'class': cls, 'device': device, 'driver': driver,
            'remappedNvme': remapped, 'vendor': vendor}


SATA = pci('0000:00:17.0', device='0x02d3', driver='ahci')
RST = pci('0000:00:17.0', device='0x282a', cls='0x010400', driver='ahci')
VMD = pci('0000:00:0e.0', device='0x9a0b', cls='0x010400', driver='vmd')
USB_STICK = {'sda': '/sys/devices/pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host0/target0:0:0/0:0:0:0/block/sda'}
NVME = {'nvme0n1': '/sys/devices/pci0000:00/0000:00:1d.0/0000:3b:00.0/nvme/nvme0/nvme0n1'}
NVME_BEHIND_VMD = {'nvme0n1': '/sys/devices/pci0000:00/0000:00:0e.0/pci10000:e0/10000:e0:06.0/10000:e1:00.0/nvme/nvme0/nvme0n1'}


class FirmwareHiddenStorage(unittest.TestCase):
    def hidden(self, devices, block, log=''):
        return probe.firmware_hidden_storage(pci=devices, block=block, log=log)

    def test_rst_remapping(self):
        self.assertEqual(self.hidden([{**SATA, 'remappedNvme': 1}], USB_STICK), 'rst-raid')
        self.assertEqual(self.hidden([RST], USB_STICK), 'rst-raid')
        log = 'ahci 0000:00:17.0: Found 1 remapped NVMe devices.\nahci 0000:00:17.0: Switch your BIOS from RAID to AHCI mode to use them.\n'
        self.assertEqual(self.hidden([SATA], USB_STICK, log), 'rst-raid')
        self.assertIsNone(self.hidden([SATA], USB_STICK, 'ahci 0000:00:17.0: Found 0 remapped NVMe devices.'))

    def test_visible_nvme_or_plain_ahci_is_not_hidden(self):
        self.assertIsNone(self.hidden([SATA], USB_STICK))
        self.assertIsNone(self.hidden([RST], {**USB_STICK, **NVME}))
        self.assertIsNone(self.hidden([pci('0000:00:02.0', vendor='0x1022', cls='0x010400', driver='ahci')], USB_STICK))

    def test_vmd(self):
        self.assertEqual(self.hidden([SATA, VMD], USB_STICK), 'vmd')
        self.assertEqual(self.hidden([SATA, {**VMD, 'driver': None}], USB_STICK), 'vmd', 'vmd driver missing')
        self.assertIsNone(self.hidden([SATA, VMD], {**USB_STICK, **NVME_BEHIND_VMD}))

    def test_reads_sysfs(self):
        with tempfile.TemporaryDirectory() as root:
            device = Path(root, 'devices', '0000:00:17.0')
            device.mkdir(parents=True)
            Path(root, 'drivers', 'ahci').mkdir(parents=True)
            for name, value in (('vendor', '0x8086'), ('device', '0x02D3'), ('class', '0x010601'), ('remapped_nvme', '2')):
                (device / name).write_text(value + '\n')
            (device / 'driver').symlink_to(Path(root, 'drivers', 'ahci'))
            self.assertEqual(probe.pci_devices(str(Path(root, 'devices'))),
                             [{'address': '0000:00:17.0', 'class': '0x010601', 'device': '0x02d3', 'driver': 'ahci',
                               'remappedNvme': 2, 'vendor': '0x8086'}])


if __name__ == '__main__':
    unittest.main()
