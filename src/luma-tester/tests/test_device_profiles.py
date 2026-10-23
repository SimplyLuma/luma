# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from luma_tester import device_profiles as devices,vm
from luma_tester.core import TesterError

class DeviceTests(unittest.TestCase):
    def test_duo_hinge_and_rotation_keep_two_disjoint_panels(self):
        for rotation in (0,90,180,270):
            state=devices.describe(dict(profile='duo',rotation=rotation))
            regions=state['regions'];hinge=state['hinge']
            self.assertEqual(sum(r['width']*r['height'] for r in regions)+hinge['width']*hinge['height'],1392*900)
            self.assertEqual(len(regions),2)
            for rect in [*regions,hinge]:
                self.assertGreaterEqual(rect['x'],0);self.assertGreaterEqual(rect['y'],0)
                self.assertLessEqual(rect['x']+rect['width'],state['pixels']['width'])
                self.assertLessEqual(rect['y']+rect['height'],state['pixels']['height'])
        closed=devices.describe(dict(profile='duo',posture='folded'))
        self.assertEqual(closed['pixels'],dict(width=675,height=900));self.assertIsNone(closed['hinge'])

    def test_fold_transition_retains_density_and_keyboard_changes(self):
        folded=devices.describe(dict(profile='fold',posture='folded'))
        opened=devices.describe(dict(profile='fold',posture='flat'))
        self.assertEqual(folded['native_pixels'],dict(width=1080,height=2364))
        self.assertEqual(opened['native_pixels'],dict(width=2076,height=2152))
        self.assertEqual(folded['scale'],opened['scale'])
        self.assertEqual(devices.describe(dict(profile='titan'))['keyboard'],'physical')
        self.assertEqual(devices.describe(dict(profile='pixel-xl'))['keyboard'],'touch')

    def test_preview_preserves_ui_size_within_vnc_scanout_limits(self):
        for profile in devices.PROFILES:
            for rotation in (0,90,180,270):
                state=devices.describe(dict(profile=profile,rotation=rotation))
                self.assertLessEqual(state['pixels']['height'],2160)
                self.assertLessEqual(state['pixels']['width'],3840)
                for axis in ('width','height'):
                    self.assertEqual(state['pixels'][axis]/state['scale'],
                                     state['native_pixels'][axis]/state['native_scale'])

    def test_invalid_custom_size_does_not_replace_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);original=vm.save(root,vm.load(root))
            for value in (dict(width=0),dict(height=99999),dict(scale=float('nan')),dict(rotation=45),dict(profile='unknown')):
                with self.assertRaises(TesterError):vm.save(root,dict(original,device=dict(devices.DEFAULT,**value)))
                self.assertEqual(vm.load(root),original)
            config=dict(devices.DEFAULT,profile='duo',posture='book',rotation=90)
            saved=vm.save(root,dict(original,device=config))
            self.assertEqual(vm.load(root),saved)

    def test_failed_guest_transition_keeps_acknowledged_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);previous=dict(profile='phone')
            (root/'device.json').write_text(json.dumps(previous))
            session=SimpleNamespace(directory=root,manifest=dict(target='mobile',cloud_init=True),ssh=lambda args:['ssh',*args])
            with patch('subprocess.run',return_value=SimpleNamespace(returncode=1,stderr='mode rejected')):
                with self.assertRaisesRegex(TesterError,'mode rejected'):
                    devices.apply(session,dict(profile='duo'))
            self.assertEqual(json.loads((root/'device.json').read_text()),previous)

    def test_guest_update_does_not_touch_custom_or_arm_images(self):
        with patch.dict('os.environ',LUMA_TESTER_GUEST_UPDATES='/not-a-real-update'):
            for target,arch,sha in [('desktop','x86_64','unknown'),('mobile','aarch64','unknown'),('mobile','x86_64','custom')]:
                session=SimpleNamespace(manifest=dict(target=target,arch=arch,image=dict(sha256=sha)))
                self.assertFalse(vm.ensure_mobile_display(session))
