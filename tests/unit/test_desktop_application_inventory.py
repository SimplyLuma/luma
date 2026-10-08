# SPDX-License-Identifier: Apache-2.0
"""Creator default inventory and native role convergence contracts."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = {'gnome-disk-utility', 'gnome-calculator', 'ptyxis', 'gnome-terminal',
             'gnome-console', 'yelp', 'firefox', 'gnome-extensions-app',
             'openemu-linux', 'luma-imager'}
REPLACEMENTS = {'luma-calculator', 'luma-terminal', 'luma-disks', 'luma-tide'}


def lines(path):
    return [line.split('#', 1)[0].strip() for line in (ROOT / path).read_text().splitlines()
            if line.split('#', 1)[0].strip()]


def assert_inventory(pins, removed):
    # NEVRA names are the portion before version-release.arch; no filename proof.
    names = {re.match(r'(.+)-[0-9][^-]*-.+\.[^.]+$', pin).group(1) for pin in pins}
    if FORBIDDEN & names:
        raise ValueError(f'forbidden image pins: {sorted(FORBIDDEN & names)}')
    if FORBIDDEN - set(removed):
        raise ValueError(f'missing exclusions: {sorted(FORBIDDEN - set(removed))}')
    if REPLACEMENTS - names:
        raise ValueError(f'missing native roles: {sorted(REPLACEMENTS - names)}')


class DesktopApplicationInventoryTests(unittest.TestCase):
    def test_canonical_image_pins_and_exclusions_agree(self):
        assert_inventory(lines('config/desktop/packages.txt'), lines('config/os/removed-packages.txt'))

    def test_each_forbidden_pin_and_missing_exclusion_is_rejected(self):
        pins, removed = lines('config/desktop/packages.txt'), lines('config/os/removed-packages.txt')
        for name in sorted(FORBIDDEN):
            with self.subTest(package=name):
                with self.assertRaisesRegex(ValueError, 'forbidden image pins'):
                    assert_inventory(pins + [name + '-1.0-1.fc44.noarch'], removed)
                with self.assertRaisesRegex(ValueError, 'missing exclusions'):
                    assert_inventory(pins, [value for value in removed if value != name])

    def test_shared_native_apps_keep_exact_noarch_inputs_on_both_images(self):
        roles = lines('config/shared/application-packages.txt')
        self.assertTrue({'luma-calculator', 'luma-terminal', 'luma-tide'} <= set(roles))
        self.assertFalse(FORBIDDEN & set(roles))
        inputs = dict(line.split('=', 1) for line in lines('config/desktop/inputs.env') if '=' in line)
        pins = lines('config/desktop/packages.txt')
        mobile = (ROOT / 'scripts/mobile/compose-fp6-rootfs.sh').read_text()
        for name in ('CALCULATOR', 'TERMINAL', 'TIDE'):
            alias = 'LUMA_' + name + '_NEVRA'
            self.assertIn(inputs[alias], pins)
            self.assertTrue(inputs[alias].endswith('.noarch'))
            self.assertIn('$' + alias + '.rpm', mobile)
        self.assertNotIn('$PTYXIS_AARCH64_NEVRA.rpm', mobile)

    def test_dock_defaults_match_and_only_pin_baseline_apps(self):
        docks = []
        for path in ('config/desktop/dconf/db/luma.d/00-luma-desktop',
                     'src/luma-desktop-launcher-policy/dconf/00-luma-desktop'):
            value = next(line for line in lines(path) if line.startswith('favorite-apps='))
            docks.append(ast.literal_eval(value.split('=', 1)[1]))
        self.assertEqual(docks[0], docks[1])
        dock = docks[0]
        self.assertEqual(dock[:2], ['org.gnome.Nautilus.desktop', 'viola-browser.desktop'])
        self.assertEqual(dock[-2:], ['org.projectluma.Viewer.desktop', 'org.gnome.Settings.desktop'])
        self.assertEqual(len(dock), len(set(dock)))
        self.assertIn('org.projectluma.Terminal.desktop', dock)
        self.assertNotIn('org.gnome.Ptyxis.desktop', dock)
        optional = {'org.projectluma.Write.desktop', 'io.luma.Grid.desktop', 'io.luma.Stage.desktop',
                    'org.projectluma.Canvas.desktop'}
        self.assertFalse(optional & set(dock))

    def test_touchpad_defaults_match_both_requested_right_clicks(self):
        owners = ('config/desktop/dconf/db/luma.d/00-luma-desktop',
                  'src/luma-desktop-launcher-policy/dconf/00-luma-desktop')
        defaults = []
        for owner in owners:
            section = (ROOT / owner).read_text().split(
                '[org/gnome/desktop/peripherals/touchpad]', 1)[1].split('\n[', 1)[0]
            values = dict(line.split('=', 1) for line in section.splitlines()
                          if '=' in line and not line.startswith('#'))
            defaults.append(values)
        self.assertEqual(defaults[0], defaults[1])
        self.assertEqual(defaults[0]['tap-to-click'], 'true')
        self.assertEqual(defaults[0]['tap-button-map'], "'lrm'")
        self.assertEqual(defaults[0]['click-method'], "'areas'")
        self.assertFalse(list((ROOT / 'src/luma-desktop-launcher-policy/dconf').glob('**/locks/*')))

    def test_default_tide_cannot_be_omitted_or_promised_as_a_download(self):
        import json
        pins = lines('config/desktop/packages.txt')
        with self.assertRaisesRegex(ValueError, 'missing native roles'):
            assert_inventory([pin for pin in pins if not pin.startswith('luma-tide-')],
                             lines('config/os/removed-packages.txt'))
        self.assertNotIn('luma-tide', lines('config/os/removed-packages.txt'))
        for path in ('src/luma-installer-atlas/src/data/app-collections.json',
                     'src/luma-installer/data/depot-first-party.json'):
            catalog = json.loads((ROOT / path).read_text())
            self.assertTrue(all('tide' not in collection['applications'] for collection in catalog['collections']))
            if 'atlas' not in path:
                self.assertTrue(any(app['id'] == 'tide' and app['preinstalled_on_luma']
                                    for app in catalog['applications']))

    def test_tide_image_gate_rejects_missing_or_hidden_launcher(self):
        smoke = (ROOT / 'tests/smoke/desktop.sh').read_text()
        excluded = smoke.split('for launcher in org.projectluma.Write', 1)[1].split('done', 1)[0]
        self.assertNotIn('org.projectluma.Tide', excluded)
        self.assertIn('default Tide owns a valid installed launcher', smoke)
        self.assertIn('rpm -qf --qf "%{NAME}" /usr/share/applications/org.projectluma.Tide.desktop', smoke)
        visible = smoke.split("for desktop in ('org.projectluma.Calculator'", 1)[1].split('PYGIO', 1)[0]
        self.assertIn('org.projectluma.Tide', visible)
        self.assertIn('lib.g_app_info_should_show(app)', visible)

    def test_system_flatpaks_map_to_present_native_providers(self):
        mapping = dict(line.split() for line in lines('config/desktop/system-flatpak-replacements.txt'))
        self.assertEqual(mapping['org.gnome.Calculator'], 'luma-calculator')
        self.assertEqual(mapping['org.gnome.Ptyxis'], 'luma-terminal')
        self.assertEqual(mapping['org.gnome.DiskUtility'], 'luma-disks')
        self.assertEqual(mapping['org.mozilla.firefox'], 'viola-browser-stable')
        self.assertEqual(mapping['org.gnome.Extensions'], 'gnome-shell')

class GateUsbIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'tests/os/gate/admin-prompt.sh').read_text()
        marker = '  cat >"$state/udisks.py" <<\'PY\'\n'
        if source.count(marker) != 1:
            raise AssertionError('exactly one gate USB helper must be exercised')
        helper = source.split(marker, 1)[1].split('\nPY\n', 1)[0]
        namespace = {'__name__': 'gate_fixture_test'}
        exec(compile(helper, 'embedded-gate-usb-helper', 'exec'), namespace)
        cls.select = staticmethod(namespace['select_gate_block'])
        cls.bus = namespace['BUS']

    def fixture(self):
        return {'/gate/block': {self.bus + '.Block': {
                    'Drive': '/gate/drive', 'Size': 64 * 1024 * 1024,
                    'PreferredDevice': list(b'/dev/sdz\0')}},
                '/gate/drive': {self.bus + '.Drive': {
                    'Serial': 'LUMAGATEUSB', 'ConnectionBus': 'usb'}}}

    def test_only_owned_gate_usb_is_selected(self):
        self.assertEqual(self.select(self.fixture()), ('/gate/block', '/dev/sdz'))
        self.assertIsNone(self.select({}))
        objects = self.fixture()
        objects['/gate/drive'][self.bus + '.Drive']['Serial'] = 'PERSONALUSB'
        self.assertIsNone(self.select(objects))

    def test_changed_identity_or_eligibility_is_rejected(self):
        for change in ('size', 'bus', 'readonly', 'ignore', 'mounted', 'device'):
            objects = self.fixture()
            block = objects['/gate/block'][self.bus + '.Block']
            if change == 'size': block['Size'] += 1
            elif change == 'bus': objects['/gate/drive'][self.bus + '.Drive']['ConnectionBus'] = 'ata'
            elif change == 'readonly': block['ReadOnly'] = True
            elif change == 'ignore': block['HintIgnore'] = True
            elif change == 'mounted': objects['/gate/block'][self.bus + '.Filesystem'] = {'MountPoints': [list(b'/mnt\0')]}
            elif change == 'device': block['PreferredDevice'] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.select(objects)

    def test_ambiguous_gate_usb_is_rejected_and_partitions_are_not_targets(self):
        import copy
        objects = self.fixture()
        objects['/gate/duplicate'] = copy.deepcopy(objects['/gate/block'])
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.select(objects)
        objects['/gate/duplicate'][self.bus + '.Partition'] = {'Table': '/gate/block'}
        self.assertEqual(self.select(objects), ('/gate/block', '/dev/sdz'))


if __name__ == '__main__':
    unittest.main()
