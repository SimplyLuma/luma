# SPDX-License-Identifier: Apache-2.0
"""Production identity, launcher and compatibility for an Ari app update."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT/'packaging/flatpak/apps/org.projectluma.Ari'


class AriDelivery(unittest.TestCase):
    def test_recipe_keeps_identity_and_declares_the_required_host(self):
        recipe = json.loads((RECIPE/'org.projectluma.Ari.yml').read_text())
        self.assertEqual(recipe['id'], 'org.projectluma.Ari')
        self.assertEqual(recipe['runtime'], 'org.projectluma.Platform')
        self.assertNotIn('Ari2', json.dumps(recipe))
        registry = json.loads((ROOT/'packaging/flatpak/first-party-updates.json').read_text())
        entry = next(row for row in registry['applications'] if row['id'] == recipe['id'])
        self.assertIn('--metadata=X-Luma=min-host-ari='+str(entry['minimum_host_packages']['luma-ari']), recipe['finish-args'])
        # These are the two existing, published bus permissions. An update
        # must neither add broad access nor drop the shared window integration.
        self.assertEqual({arg for arg in recipe['finish-args'] if arg.startswith('--talk-name=')},
                         {'--talk-name=org.projectluma.Ari1', '--talk-name=org.project_luma.ShellState1'})

    def test_normal_recipe_generates_executable_launcher_and_activation_text(self):
        recipe = json.loads((RECIPE/'org.projectluma.Ari.yml').read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'bin').mkdir(); (root/'share/dbus-1/services').mkdir(parents=True)
            for command in recipe['modules'][0]['build-commands']:
                if command.startswith('printf '):
                    subprocess.run(command.replace('/app/', str(root)+'/'), shell=True, check=True)
            launcher = root/'bin/org.projectluma.Ari'
            self.assertEqual(launcher.read_text(), '#!/bin/sh\nexec python3 -m ari_ui.app "$@"\n')
            subprocess.run(['sh', '-n', str(launcher)], check=True)
            activation = (root/'share/dbus-1/services/org.projectluma.Ari.service').read_text()
            self.assertEqual(activation.splitlines(), ['[D-BUS Service]', 'Name=org.projectluma.Ari',
                'Exec='+str(root)+'/bin/org.projectluma.Ari'])

    def test_new_app_metadata_discloses_connected_models_and_has_a_concrete_release(self):
        for path in (RECIPE/'org.projectluma.Ari.metainfo.xml', ROOT/'src/luma-ari/data/org.projectluma.Ari.metainfo.xml'):
            component = ET.parse(path).getroot()
            self.assertEqual(component.findtext('id'), 'org.projectluma.Ari')
            description = ' '.join(component.find('description').itertext())
            self.assertIn('Connected models receive the conversation and tool results', description)
            release = component.find('releases/release')
            self.assertEqual(release.attrib['version'], '0.2.0')
            self.assertIn('ChatGPT sign-in', ''.join(release.itertext()))

    def test_source_seal_uses_the_same_ari_icon_as_the_native_app(self):
        path = ROOT/'scripts/depot/seal-first-party-source.py'
        spec = importlib.util.spec_from_file_location('ari_source_sealer', path)
        sealer = importlib.util.module_from_spec(spec); spec.loader.exec_module(sealer)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/'sealed'
            sealer.seal(ROOT, 'Ari', out)
            manifest = json.loads((out/'SOURCE-MANIFEST.json').read_text())
            import hashlib
            expected = hashlib.sha256((ROOT/'src/luma-ari/data/org.projectluma.Ari.svg').read_bytes()).hexdigest()
            self.assertEqual(manifest['materials']['depot-icon.svg']['sha256'], expected)


if __name__ == '__main__': unittest.main()
