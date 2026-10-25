# SPDX-License-Identifier: Apache-2.0
"""System battery discovery must not depend on an ACPI device name."""
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from luma_vitals import power, sampler

class SystemSupplies(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(power,'POWER_SUPPLY',self.root).start()
    def pack(self,name='qcom-battmgr-bat',**kw):
        p=self.root/name;p.mkdir()
        for k,v in dict(type='Battery',status='Discharging',present=1,**kw).items():
            (p/k).write_text(str(v))
        return p
    def test_non_acpi_battery_with_signed_power(self):
        self.pack(power_now=-5000000,energy_now=30000000,energy_full=40000000,capacity=75)
        d=power.draw();self.assertTrue(d.on_battery);self.assertEqual(d.watts,5)
        self.assertEqual(d.seconds_to_empty,21600)
    def test_peripheral_and_absent_packs_are_not_system_batteries(self):
        self.pack('00-stylus',scope='Device',power_now=100000)
        p=self.pack('01-absent');(p/'present').write_text('0')
        self.pack('qcom-battmgr-bat',power_now=5000000)
        self.assertEqual(power.draw().watts,5)
    def test_only_peripheral_is_not_a_battery_powered_computer(self):
        self.pack('BAT-pen',scope='Device')
        self.assertFalse(power.draw().on_battery)
    def test_energy_is_preserved_without_power_reading(self):
        self.pack(energy_now=30000000,energy_full=40000000)
        d=power.draw();self.assertEqual(d.energy_wh,30);self.assertEqual(d.energy_full_wh,40)
        self.assertIsNone(d.watts);self.assertIsNone(d.seconds_to_empty)
    def test_negative_current_fallback_preserves_native_energy(self):
        self.pack(current_now=-500000,voltage_now=10000000,energy_now=30000000,energy_full=40000000)
        d=power.draw();self.assertEqual(d.watts,5);self.assertEqual(d.energy_wh,30)
        self.assertEqual(d.energy_full_wh,40)
    def test_zero_percent_is_not_missing(self):
        self.pack(capacity=0,power_now=1000000)
        self.assertEqual(power.draw().percent,0)
    def test_sampler_uses_same_signed_system_battery_reading(self):
        self.pack(power_now=-5000000)
        self.assertEqual(sampler._battery_watts(),5)
    def test_energy_collector_uses_device_type_and_scope(self):
        # The standalone energy subpackage must stay independent of the session package.
        source=Path(__file__).resolve().parents[2]/'src/luma-vitals/energy/luma-vitals-energy'
        if not source.exists():source=Path(__file__).resolve().parents[1]/'energy/luma-vitals-energy'
        loader=importlib.machinery.SourceFileLoader('energy_supply_check',str(source))
        spec=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(spec);loader.exec_module(m)
        m.POWER_SUPPLY=self.root
        self.pack('00-stylus',scope='Device');self.pack()
        self.assertEqual([p.name for p in m.batteries()],['qcom-battmgr-bat'])

    def test_platform_display_runtime_is_not_gpu_idle(self):
        device=self.root/'card1/device/power';device.mkdir(parents=True)
        (device/'runtime_suspended_time').write_text('1234')
        with patch.object(power,'DRM',self.root):
            self.assertEqual(power._graphics(),(0,None))

    def test_pci_gpu_runtime_counter_remains_available(self):
        device=self.root/'card0/device';(device/'power').mkdir(parents=True)
        (device/'subsystem').symlink_to(self.root/'pci')
        (device/'power/runtime_suspended_time').write_text('1234')
        with patch.object(power,'DRM',self.root):
            self.assertEqual(power._graphics(),(1234000,None))

    def test_connectors_are_not_separate_gpu_devices(self):
        device=self.root/'card0-eDP-1/device';(device/'power').mkdir(parents=True)
        (device/'subsystem').symlink_to(self.root/'pci')
        (device/'power/runtime_suspended_time').write_text('1234')
        with patch.object(power,'DRM',self.root):
            self.assertEqual(power._graphics(),(0,None))

    def test_default_and_powersupersave_aspm_are_not_reported_as_disabled(self):
        for policy in ('[default] performance powersave powersupersave',
                       'default performance powersave [powersupersave]',
                       'default performance [powersave] powersupersave'):
            with self.subTest(policy=policy), patch.object(power,'_profile',return_value=''), \
                    patch.object(power,'_read',side_effect=lambda p: policy if 'pcie_aspm' in str(p) else ''):
                self.assertEqual(power._blockers(power.Draw(on_battery=True),None,None,{},[]),[])
        with patch.object(power,'_profile',return_value=''), \
                patch.object(power,'_read',side_effect=lambda p: '[performance]' if 'pcie_aspm' in str(p) else ''):
            self.assertEqual(power._blockers(power.Draw(on_battery=True),None,None,{},[]),
                             ['PCI Express power policy prioritizes performance'])
