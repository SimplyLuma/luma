# SPDX-License-Identifier: Apache-2.0
"""Observe refusal at the media package and template ownership boundaries."""
import hashlib
import importlib.util
import struct
import subprocess
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value
RPM = module('atlas_boot_rpms', 'scripts/install/atlas-iso/prepare-boot-rpms.py')
LORAX = module('atlas_lorax_boot', 'scripts/install/atlas-iso/prepare-lorax-boot.py')
PRESENTATION = module('atlas_runtime_presentation', 'scripts/install/atlas-iso/prepare-runtime-presentation.py')
FONT = module('luma_grub_font', 'scripts/boot/check-grub-fonts.py')

class InstallerBootInputs(unittest.TestCase):
    def test_exact_manifest_files_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = root / 'pool'; pool.mkdir()
            pins, entries = [], []
            for index, key in enumerate(RPM.KEYS):
                nevra = f'boot{index}-1-1.noarch'
                data = f'package {index}'.encode()
                (pool / f'{nevra}.rpm').write_bytes(data)
                pins.append(f'{key}={nevra}')
                entries.append(f'{nevra} {hashlib.sha256(data).hexdigest()} {index:064x}')
            inputs = root / 'inputs'; inputs.write_text('\n'.join(pins))
            manifest = root / 'manifest'; manifest.write_text('\n'.join(entries))
            output = root / 'good' / 'rpms'
            self.assertEqual(len(RPM.prepare(inputs, manifest, pool, output)), len(RPM.KEYS))
            self.assertEqual(len(list(output.glob('*.rpm'))), len(RPM.KEYS))
            # Every selected package independently rejects altered bytes.
            for index in range(len(RPM.KEYS)):
                path = pool / f'boot{index}-1-1.noarch.rpm'
                original = path.read_bytes(); path.write_bytes(b'wrong package')
                rejected = root / f'rejected{index}' / 'rpms'
                with self.assertRaisesRegex(ValueError, 'digest changed'):
                    RPM.prepare(inputs, manifest, pool, rejected)
                self.assertFalse(rejected.exists())
                path.write_bytes(original)
            manifest.write_text('\n'.join(entries[1:]))
            with self.assertRaises(KeyError):
                RPM.prepare(inputs, manifest, pool, root / 'missing')

    def test_real_menu_choices_survive_and_unknown_layout_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generic = root / 'source/templates.d/99-generic'
            config = generic / 'config_files/x86'; config.mkdir(parents=True)
            menu = '''set default="1"
set timeout=60
search --no-floppy --set=root -l '@ISOLABEL@'
menuentry 'Install' { linux @KERNELPATH@ @ROOT@ quiet }
menuentry 'Test' { linux @KERNELPATH@ @ROOT@ rd.live.check quiet }
menuentry 'Basic graphics' { linux @KERNELPATH@ @ROOT@ nomodeset quiet }
menuentry 'Rescue' { linux @KERNELPATH@ @ROOT@ inst.rescue quiet }
'''
            for kind in ('bios', 'efi'):
                (config / f'grub2-{kind}.cfg').write_text(menu)
            (generic / 'x86.tmpl').write_text(LORAX.ARCH + '\n## make boot.iso\n')
            LORAX.prepare(root / 'source', root / 'good')
            for kind in ('bios', 'efi'):
                result = (root / 'good/templates.d/99-generic/config_files/x86' / f'grub2-{kind}.cfg').read_text()
                self.assertIn('nomodeset quiet', result)
                self.assertIn('inst.rescue quiet', result)
                self.assertIn('rd.live.check rhgb quiet', result)
                self.assertEqual(result.count('menuentry'), menu.count('menuentry'))
                self.assertLess(result.index(LORAX.SEARCH), result.index('loadfont'))
                self.assertIn('terminal_output gfxterm', result)
                self.assertIn('set timeout_style=hidden\nset timeout=1', result)
                self.assertNotIn('set timeout=5', result)
            for kind in ('bios', 'efi'):
                path = config / f'grub2-{kind}.cfg'
                path.write_text(menu.replace('set timeout=60', 'set timeout=unknown'))
                with self.assertRaisesRegex(ValueError, 'unsupported Lorax'):
                    LORAX.prepare(root / 'source', root / f'rejected-{kind}')
                self.assertFalse((root / f'rejected-{kind}').exists())
                path.write_text(menu)

    def test_runtime_handoff_preserves_tui_and_refuses_unknown_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            display = root / 'usr/lib64/python3.14/site-packages/pyanaconda/display.py'
            display.parent.mkdir(parents=True)
            upstream = "import os\n\ndef do_startup_wl_actions(timeout, headless=False, headless_resolution=None):\n    if headless:\n        # headless (remote connection) - stay on VT1\n        return 'remote'\n    util.startProgram([], env_add={'XDG_DATA_DIRS': xdg_data_dirs})\n\ndef setup_display(anaconda):\n    # with Wayland running we can initialize the UI interface\n    anaconda.initialize_interface()\n"
            display.write_text(upstream)
            units = root/'usr/lib/systemd/system'; units.mkdir(parents=True)
            for unit in ('plymouth-quit.service', 'plymouth-quit-wait.service'):
                (units/unit).write_text('[Service]\nExecStart=/usr/bin/plymouth quit\n')
            schemas = root/'usr/share/anaconda/window-manager/glib-2.0/schemas'; schemas.mkdir(parents=True)
            system_schema = root/'usr/share/glib-2.0/schemas'; system_schema.mkdir(parents=True)
            (system_schema/'org.gnome.desktop.background.gschema.xml').write_text("""<schemalist>
<schema id="org.gnome.desktop.background"><key name="picture-options"
enum="org.gnome.desktop.GDesktopBackgroundStyle"><default>'scaled'</default></key></schema></schemalist>""")
            (system_schema/'org.gnome.desktop.enums.xml').write_text(
                '<schemalist><enum id="org.gnome.desktop.GDesktopBackgroundStyle">'
                '<value nick="scaled" value="1"/></enum></schemalist>')
            brand = root/'usr/share/luma/boot/brand'; brand.mkdir(parents=True)
            (brand/'luma-wordmark.svg').write_text('<svg viewBox="0 0 2219 715"/>')
            (root/'usr/share/luma-installer-atlas').mkdir()
            profile = root/'usr/share/dconf/profile/gnomekiosk'; profile.parent.mkdir(parents=True)
            profile.write_text('user-db:user\nfile-db:/usr/share/gnome-kiosk/gnomekiosk.dconf.compiled\n')
            defaults = root/'usr/share/gnome-kiosk/gnomekiosk.dconf.compiled'; defaults.parent.mkdir(parents=True)
            defaults.write_bytes(b'preserved kiosk defaults')
            with patch.object(PRESENTATION.subprocess, 'run') as compile_schemas:
                PRESENTATION.prepare(root)
                self.assertIn("'GSETTINGS_SCHEMA_DIR': datadir + '/window-manager/glib-2.0/schemas'", display.read_text())
                self.assertEqual(compile_schemas.call_args_list[0].args[0][:2], ['glib-compile-schemas', '--strict'])
                self.assertEqual(compile_schemas.call_args_list[1].args[0][:2], ['dconf', 'compile'])
            self.assertEqual(profile.read_text().splitlines(), ['user-db:user',
                'file-db:/usr/share/luma-installer-atlas/kiosk-background.dconf',
                'file-db:/usr/share/gnome-kiosk/gnomekiosk.dconf.compiled'])
            self.assertEqual(defaults.read_bytes(), b'preserved kiosk defaults')
            art = PRESENTATION.ET.fromstring((root/'usr/share/luma-installer-atlas/loader-background.svg').read_bytes())
            self.assertEqual(art.get('viewBox'), '0 0 142 75')
            self.assertEqual(art.get('width'), '142')
            self.assertIn("picture-options='centered'", (schemas/'org.gnome.desktop.background.gschema.override').read_text())
            calls = []
            class Util:
                @staticmethod
                def execWithRedirect(command, args): calls.append((command, args))
            namespace = {'util': Util(), 'log': None}
            exec(compile(display.read_text(), str(display), 'exec'), namespace)
            with patch('os.path.isfile', return_value=True):
                namespace['do_startup_wl_actions'](1, headless=True)
                self.assertEqual(calls, [('plymouth', ['quit'])])
                for tui in (False, True):
                    class Anaconda:
                        tui_mode = tui
                        def initialize_interface(self): calls.append('initialize')
                    calls.clear(); namespace['setup_display'](Anaconda())
                    self.assertEqual(calls, [('plymouth', ['quit']), 'initialize'] if tui else ['initialize'])
            for unit in ('plymouth-quit.service', 'plymouth-quit-wait.service'):
                self.assertIn('ConditionKernelCommandLine=!rhgb', (units/f'{unit}.d/50-luma-atlas-handoff.conf').read_text())
            display.write_text(upstream.replace('anaconda.initialize_interface()', 'anaconda.start_unknown_ui()'))
            with self.assertRaisesRegex(ValueError, 'unsupported Anaconda'):
                PRESENTATION.prepare(root)
            self.assertNotIn('_luma_release_boot_splash', display.read_text())
            display.write_text(upstream)
            profile.write_text('user-db:user\nfile-db:/unexpected-owner\n')
            with self.assertRaisesRegex(ValueError, 'unsupported native GNOME Kiosk'):
                PRESENTATION.prepare(root)
            self.assertEqual(display.read_text(), upstream)
            profile.write_text('user-db:user\nfile-db:/usr/share/gnome-kiosk/gnomekiosk.dconf.compiled\n')
            (system_schema/'org.gnome.desktop.enums.xml').unlink()
            with self.assertRaisesRegex(ValueError, 'missing.*native GNOME schema'):
                PRESENTATION.prepare(root)
            self.assertEqual(display.read_text(), upstream)

    def test_native_background_compiler_and_rooted_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'usr/share/glib-2.0/schemas'; source.mkdir(parents=True)
            schemas = root/'kiosk'; schemas.mkdir()
            background = 'org.gnome.desktop.background.gschema.xml'
            enums = 'org.gnome.desktop.enums.xml'
            (source/background).write_text("""<schemalist><schema id="org.gnome.desktop.background">
<key name="picture-options" enum="org.gnome.desktop.GDesktopBackgroundStyle">
<default>'scaled'</default></key></schema></schemalist>""")
            (source/enums).write_text('<schemalist><enum id="org.gnome.desktop.GDesktopBackgroundStyle">'
                                     '<value nick="scaled" value="1"/></enum></schemalist>')
            # Reproduce the previous helper: background alone, unresolved enum.
            (schemas/background).write_bytes((source/background).read_bytes())
            (schemas/enums).symlink_to('/missing-luma-test-runtime-schema.xml')
            old = subprocess.run(['glib-compile-schemas', '--strict', str(schemas)],
                                 capture_output=True, text=True)
            self.assertNotEqual(old.returncode, 0)
            self.assertIn('GDesktopBackgroundStyle', old.stderr)
            sentinel = root/'outside-schema-sentinel'
            sentinel.write_bytes(b'untouched')
            (schemas/background).unlink(); (schemas/background).symlink_to(sentinel)
            inputs = PRESENTATION.background_schema_inputs(root)
            PRESENTATION.materialize_background_schemas(schemas, inputs)
            self.assertFalse((schemas/background).is_symlink())
            self.assertFalse((schemas/enums).is_symlink())
            self.assertEqual(sentinel.read_bytes(), b'untouched')
            subprocess.run(['glib-compile-schemas', '--strict', str(schemas)], check=True)
            self.assertGreater((schemas/'gschemas.compiled').stat().st_size, 0)
            # Absolute source links are runtime-rooted, not host-rooted.
            actual = source/'actual-enums.xml'; (source/enums).rename(actual)
            (source/enums).symlink_to('/usr/share/glib-2.0/schemas/actual-enums.xml')
            self.assertEqual(PRESENTATION.background_schema_inputs(root), inputs)
            (source/enums).unlink(); (source/enums).symlink_to('../../../../../../etc/passwd')
            with self.assertRaisesRegex(ValueError, 'unrooted'):
                PRESENTATION.background_schema_inputs(root)
            (source/enums).unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                PRESENTATION.background_schema_inputs(root)

    def test_theme_checks_actual_pf2_name_not_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'theme.txt').write_text('title-font: "Prairie Regular 22"\nfont = "Prairie Regular 12"\n')
            def pf2(name):
                data = name.encode() + b'\0'
                return b'FILE' + struct.pack('>I', 4) + b'PFF2' + b'NAME' + struct.pack('>I', len(data)) + data
            (root / 'prairie-22.pf2').write_bytes(pf2('Prairie Regular Regular 16'))
            with self.assertRaisesRegex(ValueError, 'unavailable font names'):
                FONT.check(root)
            (root / 'prairie-22.pf2').write_bytes(pf2('Prairie Regular 22'))
            with self.assertRaises(ValueError):
                FONT.check(root)
            (root / 'prairie-12.pf2').write_bytes(pf2('Prairie Regular 12'))
            self.assertEqual(FONT.check(root), {'Prairie Regular 12', 'Prairie Regular 22'})
            # The old packaged theme passed font checks but GRUB itself stopped
            # with a missing '*' error before normal boot. Exercise that input.
            with (root / 'theme.txt').open('a') as theme:
                theme.write('terminal-box: ""\n')
            with self.assertRaisesRegex(ValueError, 'pixmap pattern'):
                FONT.check(root)

if __name__ == '__main__':
    unittest.main()
