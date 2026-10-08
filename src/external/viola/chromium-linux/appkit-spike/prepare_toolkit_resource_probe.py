# SPDX-License-Identifier: GPL-3.0-only
"""Prepare private GTK/libadwaita resources from existing Luma menu source.

This diagnoses missing compiled menu styles. It does not install a stylesheet
provider into an app or modify system packages. The native theme resource is
replaced only in an explicitly opted-in process via GLib resource overlays.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import gi
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compiled-menu-directory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--appearance', choices=('light', 'dark'), required=True)
    args = parser.parse_args()
    if os.environ.get('G_RESOURCE_OVERLAYS'):
        raise RuntimeError('Baseline extraction must use unmodified installed resources')
    if args.output.exists():
        raise RuntimeError('Use a fresh resource candidate directory')
    Adw.get_major_version()  # Load the actual installed native library/resources.
    args.output.mkdir(parents=True)
    menus = {mode: (args.compiled_menu_directory / ('menu-' + mode + '.css')).read_text()
             for mode in ('light', 'dark')}
    provenance = json.loads((args.compiled_menu_directory / 'provenance.json').read_text())
    for mode, content in menus.items():
        expected = provenance.get('compiled', {}).get('menu-' + mode + '.css')
        if expected != hashlib.sha256(content.encode()).hexdigest():
            raise ValueError('Native menu artifact does not match its compiler provenance')
        if '.luma-menu-keycap' not in content or '.luma-menu-submenu-heading' not in content:
            raise ValueError('Compiled native menu contract is incomplete')
    report = {'classification': 'private native toolkit resource candidate; not system deployment',
              'appearance': args.appearance,
              'source': provenance,
              'resources': {}}
    resources = {'adw/gtk.css': '/org/gnome/Adwaita/styles/gtk.css',
                 'gtk/Default-light.css': '/org/gtk/libgtk/theme/Default/Default-light.css',
                 'gtk/Default-dark.css': '/org/gtk/libgtk/theme/Default/Default-dark.css'}
    for relative, resource in resources.items():
        baseline = Gio.resources_lookup_data(resource, 0).get_data().decode()
        if '.luma-menu-keycap' in baseline:
            raise RuntimeError('Installed toolkit already contains menu keycaps: ' + resource)
        # This bounded visual candidate has an explicit appearance. GLib
        # named-color declarations are global; nesting them in a media rule
        # does not provide an independently scoped dark token table.
        addition = (menus[args.appearance] if relative.startswith('adw/')
                    else menus['dark' if '-dark' in relative else 'light'])
        content = baseline + '\n/* Existing Luma native menu resource candidate. */\n' + addition
        output = args.output / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
        report['resources'][resource] = {'baseline_sha256': hashlib.sha256(baseline.encode()).hexdigest(),
            'candidate_sha256': hashlib.sha256(content.encode()).hexdigest()}
    (args.output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
