# SPDX-License-Identifier: Apache-2.0
"""Actual sealed source/path controls; these do not qualify creative engines."""
import configparser
import contextlib
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
APPS = ('Write', 'Grid', 'Stage', 'Session', 'Reel')


class CreativeRecipes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='creative-source-')
        cls.work = Path(cls.temporary.name)
        spec = importlib.util.spec_from_file_location(
            'creative_source_sealer', ROOT / 'scripts/depot/seal-first-party-source.py')
        sealer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sealer)
        cls.sealer = sealer
        cls.packets = {}
        for app in APPS:
            output = cls.work / app
            with contextlib.redirect_stdout(io.StringIO()):
                sealer.seal(ROOT, app, output)
            recipe = yaml.safe_load((output / f'org.projectluma.{app}.yml').read_text())
            archive = output / recipe['modules'][0]['sources'][0]['path']
            with tarfile.open(archive) as tf:
                tf.extractall(output / 'unpacked', filter='data')
            # Flatpak resolves additional file sources independently of the
            # first source archive; model those real recipe inputs as well.
            recipe_dir = ROOT / 'packaging/flatpak/apps' / recipe['id']
            original = yaml.safe_load((recipe_dir / (recipe['id'] + '.yml')).read_text())
            for item in original['modules'][0]['sources'][1:]:
                if item.get('type') == 'file':
                    origin = recipe_dir / item['path']
                    destination = output / 'unpacked/source' / item.get('dest-filename', origin.name)
                    shutil.copyfile(origin, destination)
            cls.packets[app] = (recipe, output / 'unpacked/source')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def source_directory(self, app, module, root):
        expected = 'src/luma-reel' if app == 'Reel' else f'src/external/{app.lower()}'
        self.assertEqual(module.get('subdir'), expected,
                         'the sealed archive retains the maintained nested source path')
        directory = root / module['subdir']
        self.assertTrue(directory.is_dir())
        if module['buildsystem'] == 'meson':
            self.assertTrue((directory / 'meson.build').is_file())
            self.assertIn('-Ddesktop=enabled', module['config-opts'])
        return directory

    def install_metadata(self, app, recipe, root, target):
        module = recipe['modules'][0]
        cwd = self.source_directory(app, module, root)
        if app == 'Grid':
            # Supply the exact configured schema Meson installs. This fixture
            # exercises the recipe's real post-install compiler, not its engine.
            identity = configparser.ConfigParser()
            identity.read_string('[identity]\n' + (cwd / 'identity/product.ini').read_text())
            schema = (cwd / 'data/io.luma.Grid.gschema.xml.in').read_text()
            for key in ('application_id', 'settings_path'):
                schema = schema.replace('@' + key.upper() + '@', identity['identity'][key])
            self.assertNotIn('@', schema)
            schema_dir = target / 'share/glib-2.0/schemas'
            schema_dir.mkdir(parents=True, exist_ok=True)
            (schema_dir / (recipe['id'] + '.gschema.xml')).write_text(schema)
        commands = module.get('post-install', []) if app != 'Reel' else module['build-commands']
        installs = 0
        for command in commands:
            if not command.startswith('install ') and not command.startswith('glib-compile-schemas '):
                continue
            args = shlex.split(command.replace('$FLATPAK_BUILDER_BUILDDIR', str(root)))
            self.assertTrue(args[-1].startswith('/app/'))
            args[-1] = str(target / args[-1].removeprefix('/app/'))
            subprocess.run(args, cwd=cwd, check=True, capture_output=True, timeout=15)
            installs += 1
        self.assertGreater(installs, 2)
        metadata = target / f'share/metainfo/org.projectluma.{app}.metainfo.xml'
        self.assertEqual(metadata.read_bytes(), (root / 'depot.metainfo.xml').read_bytes())
        self.assertTrue((target / f'share/licenses/org.projectluma.{app}/LICENSE.md').is_file())

    def test_actual_five_archives_resolve_and_install_metadata(self):
        for app in APPS:
            with self.subTest(app=app):
                recipe, root = self.packets[app]
                manifest = json.loads((root / 'SOURCE-MANIFEST.json').read_text())
                self.assertGreater(len(manifest['materials']), 10)
                for name, record in manifest['materials'].items():
                    path = root / name
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record['sha256'])
                    self.assertEqual(path.stat().st_mode & 0o777, record['mode'])
                target = self.work / f'installed-{app}'
                self.install_metadata(app, recipe, root, target)
                if app == 'Stage':
                    self.assertEqual((target / 'share/licenses/org.projectluma.Stage/Lucide.txt').read_bytes(),
                                     (root / 'src/external/stage/LICENSES/Lucide.txt').read_bytes())

    def test_old_root_meson_directory_is_rejected_for_each_nested_app(self):
        for app in APPS[:-1]:
            recipe, root = self.packets[app]
            module = dict(recipe['modules'][0])
            module.pop('subdir')
            with self.subTest(app=app), self.assertRaises(AssertionError):
                self.source_directory(app, module, root)
            self.assertFalse((root / 'meson.build').exists())

    def test_old_top_level_third_party_notice_does_not_install(self):
        recipe, root = self.packets['Write']
        destination = self.work / 'old-notice.txt'
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(['install', '-Dm644', str(root / 'THIRD_PARTY_NOTICES.md'), str(destination)],
                           check=True, capture_output=True, timeout=15)
        self.assertFalse(destination.exists())

    def test_reel_compiled_schema_is_usable_and_missing_compile_is_rejected(self):
        recipe, root = self.packets['Reel']
        target = self.work / 'reel-schema-check'
        self.install_metadata('Reel', recipe, root, target)
        schema = target / 'share/glib-2.0/schemas'
        self.assertTrue((schema / 'gschemas.compiled').is_file())
        env = dict(os.environ, GSETTINGS_SCHEMA_DIR=str(schema), GSETTINGS_BACKEND='memory')
        command = ['gsettings', 'get', 'org.projectluma.Reel', 'show-welcome']
        result = subprocess.run(command, env=env, check=True, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.stdout.strip(), 'true')
        (schema / 'gschemas.compiled').unlink()
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(command, env=env, check=True, capture_output=True, timeout=15)

    def test_grid_recipe_compiles_real_configured_schema_and_missing_step_is_rejected(self):
        recipe, root = self.packets['Grid']
        target = self.work / 'grid-schema-check'
        self.install_metadata('Grid', recipe, root, target)
        schema = target / 'share/glib-2.0/schemas'
        self.assertTrue((schema / 'gschemas.compiled').is_file())
        empty_data = self.work / 'empty-grid-data-source'
        empty_data.mkdir()
        env = dict(os.environ, GSETTINGS_SCHEMA_DIR=str(schema),
                   GSETTINGS_BACKEND='memory', XDG_DATA_DIRS=str(empty_data))
        command = ['gsettings', 'get', recipe['id'], 'show-welcome']
        result = subprocess.run(command, env=env, check=True,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.stdout.strip(), 'true')
        (schema / 'gschemas.compiled').unlink()
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(command, env=env, check=True, capture_output=True, timeout=15)

    def test_grid_and_stage_identity_alias_and_sealed_icon_match_installed_artwork(self):
        for app in ('Grid', 'Stage'):
            recipe, root = self.packets[app]
            source = root / f'src/external/{app.lower()}'
            if app == 'Grid':
                identity = configparser.ConfigParser()
                identity.read_string('[identity]\n' + (source / 'identity/product.ini').read_text())
                self.assertEqual(identity['identity']['application_id'], recipe['id'])
                self.assertEqual(identity['identity']['icon_name'], recipe['id'])
                self.assertEqual((source / 'lumaui/scenarios/grid.json').read_bytes(),
                                 (source / 'tools/lumaui-conform/scenarios/grid.json').read_bytes())
                self.assertFalse((source / 'lumaui/scenarios/grid.json').is_symlink())
            else:
                self.assertIn("luma_app_id = '" + recipe['id'] + "'",
                              (source / 'identity/meson.build').read_text())
                self.assertIn('Icon=@APP_ID@', (source / 'data/io.luma.Stage.desktop.in').read_text())
                self.assertIn('<id>' + recipe['id'] + '</id>',
                              (source / 'data/io.luma.Stage.metainfo.xml').read_text())
            alias = configparser.ConfigParser(interpolation=None)
            alias.read(source / f'data/io.luma.{app}.desktop')
            self.assertTrue(alias['Desktop Entry'].getboolean('NoDisplay'))
            self.assertEqual(alias['Desktop Entry']['StartupWMClass'], recipe['id'])
            self.assertEqual((root / 'depot-icon.svg').read_bytes(),
                             (source / f'data/io.luma.{app}.svg').read_bytes())

    def test_sealer_still_rejects_the_former_in_tree_symlink(self):
        _recipe, sealed = self.packets['Grid']
        fixture = self.work / 'former-symlink-repository'
        source = fixture / 'src/external/grid'
        shutil.copytree(sealed / 'src/external/grid', source)
        shutil.copyfile(ROOT / 'LICENSE.md', fixture / 'LICENSE.md')
        shutil.copytree(ROOT / 'packaging/flatpak/apps/org.projectluma.Grid',
                        fixture / 'packaging/flatpak/apps/org.projectluma.Grid')
        link = source / 'lumaui/scenarios/grid.json'
        link.unlink()
        link.symlink_to('../../tools/lumaui-conform/scenarios/grid.json')
        with self.assertRaisesRegex(ValueError, 'symlink input is not admitted'):
            self.sealer.seal(fixture, 'Grid', self.work / 'rejected-symlink-source')

    def test_office_apps_require_real_extension_and_engine_not_ui_only(self):
        for app in ('Write', 'Grid', 'Stage'):
            recipe, _root = self.packets[app]
            self.assertIn('-Dlibreofficekit=enabled', recipe['modules'][0]['config-opts'])
            extension = recipe['add-extensions']['org.projectluma.Platform.Office']
            self.assertEqual(extension['directory'], 'lib/office')
            self.assertFalse(extension['no-autodownload'])
            self.assertIn('--env=LUMA_LIBREOFFICE_PROGRAM=/app/lib/office/lib64/libreoffice/program',
                          recipe['finish-args'])

    def test_real_wrapper_install_preserves_binary_and_seals_only_preference_helper(self):
        schemas = {'Write': 'write-preferences-v1', 'Grid': 'grid-documents-preferences-v1',
                   'Stage': 'stage-preferences-v1', 'Session': 'session-instruments-preferences-v1',
                   'Reel': 'reel-profile-preferences-v1'}
        for app in APPS:
            recipe, root = self.packets[app]
            module = recipe['modules'][0]
            cwd = self.source_directory(app, module, root)
            target = self.work / ('wrapper-' + app)
            (target / 'bin').mkdir(parents=True)
            binary = 'luma-' + app.lower()
            original = b'owned packaging fixture; not a compiled application\n'
            (target / 'bin' / binary).write_bytes(original)
            for command in module['post-install']:
                if command.startswith(('mv ', 'printf ', 'chmod ')) or 'luma_app_preferences.py' in command:
                    translated = command.replace('/app/', str(target) + '/')
                    subprocess.run(['sh', '-c', translated], cwd=cwd,
                                   env=dict(os.environ, FLATPAK_BUILDER_BUILDDIR=str(root)),
                                   check=True, capture_output=True, timeout=15)
            self.assertEqual((target / 'bin' / (binary + '-real')).read_bytes(), original)
            launcher = target / 'bin' / binary
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
            subprocess.run(['sh', '-n', str(launcher)], check=True,
                           capture_output=True, timeout=15)
            self.assertEqual(launcher.read_text(), '#!/bin/sh\nexec python3 -m luma_appkit.migration_startup '
                             'python3 -m luma_app_preferences ' + binary + '-real "$@"\n')
            helper = target / 'lib/python3.14/site-packages/luma_app_preferences.py'
            sealed_helper = root / 'src/luma-installer/luma_installer/app_preferences.py'
            self.assertEqual(helper.read_bytes(), sealed_helper.read_bytes())
            ast.parse(helper.read_text())
            manifest = json.loads((root / 'SOURCE-MANIFEST.json').read_text())
            self.assertEqual([name for name in manifest['materials'] if name.startswith('src/luma-installer/')],
                             ['src/luma-installer/luma_installer/app_preferences.py'])
            minimum = 64 if app in {'Write', 'Grid', 'Stage'} else 63
            self.assertIn('--metadata=X-Luma=min-host-installer=' + str(minimum), recipe['finish-args'])
            self.assertIn('--metadata=X-Luma=app-data-schema=' + schemas[app], recipe['finish-args'])
            self.assertIn('--talk-name=org.projectluma.AppData1', recipe['finish-args'])
            self.assertIn('--env=GSETTINGS_BACKEND=keyfile', recipe['finish-args'])


if __name__ == '__main__':
    unittest.main()
