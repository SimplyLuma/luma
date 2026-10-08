#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Keep native Plymouth until the installer owns a graphical frame.

Apply only to Lorax's disposable installer runtime, never the installed OS.
Anaconda 44.30's text/recovery paths remain visible and keep their logs.
"""
import ast
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

POLICY = 'usr/share/luma-installer-atlas/plymouth-handoff-policy'
DROPIN = '''[Unit]
# Anaconda's graphical viewer releases Plymouth after its first real frame.
# Text/basic-graphics/rescue startup keeps the upstream quit behavior.
ConditionKernelCommandLine=!rhgb
'''
RELEASE = '''def _luma_release_boot_splash():
    # Luma installer-only policy. Restore native console behavior for TUI/RDP
    # and graphical-startup failures; do not hide exceptional diagnostics.
    if os.path.isfile("/usr/share/luma-installer-atlas/plymouth-handoff-policy"):
        try:
            util.execWithRedirect("plymouth", ["quit"])
        except OSError as error:
            log.warning("Luma boot splash could not be released: %s", error)


'''


def background_schema_inputs(root):
    """Resolve native schema links inside the disposable installer root."""
    root = root.resolve()
    inputs = {}
    for name in ('org.gnome.desktop.background.gschema.xml', 'org.gnome.desktop.enums.xml'):
        source = root / 'usr/share/glib-2.0/schemas' / name
        for _ in range(8):
            if not source.is_symlink():
                break
            target = source.readlink()
            source = root / str(target).lstrip('/') if target.is_absolute() else source.parent / target
        else:
            raise ValueError(f'unsupported native GNOME schema link: {name}')
        if not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError(f'missing or unrooted native GNOME schema: {name}')
        inputs[name] = source.read_bytes()
    background = ET.fromstring(inputs['org.gnome.desktop.background.gschema.xml'])
    enumerations = ET.fromstring(inputs['org.gnome.desktop.enums.xml'])
    required = {key.get('enum') for key in background.iter('key') if key.get('enum')}
    provided = {item.get('id') for item in enumerations.iter('enum')}
    if not required or not required <= provided:
        raise ValueError('missing native GNOME background enumerations')
    return inputs


def materialize_background_schemas(schemas, inputs):
    # Lorax links may be absolute to the runtime root. The host compiler cannot
    # follow those links, and writing through them could modify its own files.
    for name, data in inputs.items():
        destination = schemas / name
        if destination.is_symlink():
            destination.unlink()
        destination.write_bytes(data)


def prepare(root):
    displays = list(root.glob('usr/lib*/python*/site-packages/pyanaconda/display.py'))
    if len(displays) != 1:
        raise ValueError('unsupported Anaconda display owner')
    display = displays[0]
    text = display.read_text()
    replacements = {
        'def do_startup_wl_actions(timeout, headless=False, headless_resolution=None):':
            RELEASE + 'def do_startup_wl_actions(timeout, headless=False, headless_resolution=None):',
        '    if headless:\n        # headless (remote connection) - stay on VT1':
            '    if headless:\n        _luma_release_boot_splash()\n        # headless (remote connection) - stay on VT1',
        '    # with Wayland running we can initialize the UI interface\n    anaconda.initialize_interface()':
            '    # with Wayland running we can initialize the UI interface\n'
            '    if anaconda.tui_mode:\n        _luma_release_boot_splash()\n'
            '    anaconda.initialize_interface()',
    }
    replacements["env_add={'XDG_DATA_DIRS': xdg_data_dirs}"] = (
        "env_add={'XDG_DATA_DIRS': xdg_data_dirs, "
        "'GSETTINGS_SCHEMA_DIR': datadir + '/window-manager/glib-2.0/schemas'}"
    )
    for before, after in replacements.items():
        if text.count(before) != 1:
            raise ValueError(f'unsupported Anaconda display handoff: {before}')
        text = text.replace(before, after)
    ast.parse(text)
    units = root / 'usr/lib/systemd/system'
    for name in ('plymouth-quit.service', 'plymouth-quit-wait.service'):
        if not (units / name).is_file():
            raise ValueError(f'missing native Plymouth owner: {name}')
    schemas = root / 'usr/share/anaconda/window-manager/glib-2.0/schemas'
    if not schemas.is_dir() or not schemas.resolve().is_relative_to(root.resolve()):
        raise ValueError('missing or unrooted native GNOME Kiosk schema directory')
    schema_inputs = background_schema_inputs(root)
    for name in schema_inputs:
        destination = schemas / name
        if destination.exists() and not destination.is_symlink() and not destination.is_file():
            raise ValueError(f'unsupported native GNOME schema destination: {name}')
    profile = root / 'usr/share/dconf/profile/gnomekiosk'
    kiosk_defaults = 'file-db:/usr/share/gnome-kiosk/gnomekiosk.dconf.compiled'
    if (not profile.is_file() or profile.read_text().splitlines() !=
            ['user-db:user', kiosk_defaults] or
            not (root / kiosk_defaults.removeprefix('file-db:/')).is_file()):
        raise ValueError('unsupported native GNOME Kiosk dconf profile')
    brand = root / 'usr/share/luma/boot/brand/luma-wordmark.svg'
    wordmark = ET.fromstring(brand.read_bytes())
    if wordmark.get('viewBox') != '0 0 2219 715':
        raise ValueError('unsupported Luma wordmark geometry')
    # A native Kiosk background covers its first frame while the viewer loads.
    # It uses the verified boot package's brand geometry; no polling cover or
    # second resident application is introduced.
    # Native centered placement preserves the 142x75 desktop Plymouth lockup
    # at every screen aspect ratio, instead of scaling a full-screen SVG.
    svg = ET.Element('svg', {'xmlns': 'http://www.w3.org/2000/svg',
                            'viewBox': '0 0 142 75', 'width': '142', 'height': '75'})
    wordmark.attrib.update(x='0', y='0', width='142', height='45', color='#f2f3f4')
    svg.append(wordmark)
    for x, opacity in zip((50, 62, 74, 86), ('.4', '.7', '1', '.7')):
        ET.SubElement(svg, 'rect', {'x': str(x), 'y': '69', 'width': '6', 'height': '6',
                                  'fill': '#f2f3f4', 'opacity': opacity})
    # All supported-source markers are checked before mutating the scratch.
    display.write_text(text)
    for name in ('plymouth-quit.service', 'plymouth-quit-wait.service'):
        directory = units / f'{name}.d'
        directory.mkdir(exist_ok=True)
        (directory / '50-luma-atlas-handoff.conf').write_text(DROPIN)
    (root / POLICY).write_text('first-frame\n')
    (root / 'usr/share/luma-installer-atlas/loader-background.svg').write_bytes(ET.tostring(svg))
    materialize_background_schemas(schemas, schema_inputs)
    (schemas / 'org.gnome.desktop.background.gschema.override').write_text('''[org.gnome.desktop.background]
picture-uri='file:///usr/share/luma-installer-atlas/loader-background.svg'
picture-uri-dark='file:///usr/share/luma-installer-atlas/loader-background.svg'
picture-options='centered'
primary-color='#21252b'
secondary-color='#21252b'
color-shading-type='solid'
''')
    subprocess.run(['glib-compile-schemas', '--strict', str(schemas)], check=True)
    # Kiosk's main() selects DCONF_PROFILE=gnomekiosk internally. Its file-db
    # forces grey/none even when schema defaults resolve our SVG. Add native
    # installer defaults ahead of that database, preserving user overrides and
    # every unrelated Kiosk default. No installed desktop profile is changed.
    database = root / 'usr/share/luma-installer-atlas/kiosk-background.dconf'
    with tempfile.TemporaryDirectory(prefix='luma-kiosk-defaults-') as temporary:
        source = Path(temporary)
        (source / '00-background').write_text('''[org/gnome/desktop/background]
picture-uri='file:///usr/share/luma-installer-atlas/loader-background.svg'
picture-uri-dark='file:///usr/share/luma-installer-atlas/loader-background.svg'
picture-options='centered'
primary-color='#21252b'
secondary-color='#21252b'
color-shading-type='solid'
''')
        subprocess.run(['dconf', 'compile', str(database), str(source)], check=True)
    profile.write_text('user-db:user\nfile-db:/usr/share/luma-installer-atlas/kiosk-background.dconf\n'
                       + kiosk_defaults + '\n')
    print('Installer native first-frame splash and Kiosk background prepared')


if __name__ == '__main__':
    prepare(Path(sys.argv[1]))
