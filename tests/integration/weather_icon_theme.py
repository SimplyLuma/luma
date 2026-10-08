#!/usr/bin/env python3
"""Resolve all Weather Lucide glyphs from a staged or installed icon path."""
import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gtk

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('cloud-rain', 'cloud-sun', 'cloud-moon', 'cloud-fog',
         'cloud-lightning', 'snowflake')
NS = '{http://www.w3.org/2000/svg}'

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=('staged', 'installed'))
parser.add_argument('path', type=Path, help='stage/python/icons or prefix/share/icons')
args = parser.parse_args()
manifest = json.loads((ROOT / 'assets/icon-theme/Prairie/upstream/lucide/lumaui-manifest.json').read_text())
entries = {entry['lucide']: entry for entry in manifest['icons']}
for name in NAMES:
    entry = entries[name]
    assert entry['source_origin'] == f'https://cdn.jsdelivr.net/npm/lucide-static@1.48.0/icons/{name}.svg'
    source = ROOT / 'assets/icon-theme/Prairie' / entry['source']
    source_svg = source.read_bytes()
    assert hashlib.sha256(source_svg).hexdigest() == entry['sha256']
    assert b'lucide-static v1.48.0 - ISC' in source_svg
    origin = ET.fromstring(source_svg)
    assert origin.attrib['viewBox'] == '0 0 24 24'
    assert origin.attrib['width'] == origin.attrib['height'] == '24'
    adapted = ROOT / 'assets/icon-theme/Prairie/symbolic/actions' / f'lumaui-{name}-symbolic.svg'
    adapted_svg = adapted.read_bytes()
    assert hashlib.sha256(adapted_svg).hexdigest() == entry['gtk_symbolic_sha256']
    symbolic = ET.fromstring(adapted_svg)
    assert symbolic.attrib['viewBox'] == '0 0 24 24'
    assert symbolic.attrib['width'] == symbolic.attrib['height'] == '24'
    assert len([node for node in symbolic if node.tag in
                (NS + 'path', NS + 'circle', NS + 'rect', NS + 'ellipse')]) == len(list(origin))

Gtk.init()
theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default()) if args.mode == 'staged' else Gtk.IconTheme.new()
if args.mode == 'staged':
    folder = args.path
    theme.add_search_path(str(folder))
else:
    folder = args.path / 'Prairie/symbolic/actions'
    theme.add_search_path(str(args.path))
    theme.set_theme_name('Prairie')
for name in NAMES:
    icon = f'lumaui-{name}-symbolic'
    assert theme.has_icon(icon), (args.mode, icon, theme.get_search_path())
    paintable = theme.lookup_icon(icon, None, 24, 1, Gtk.TextDirection.NONE, 0)
    file = paintable.get_file()
    assert file and Path(file.get_path()) == folder / f'{icon}.svg', (icon, file)
print(f'PASS {args.mode}: all six Gtk.IconTheme names resolve from {folder}; source/adapted 24px geometry and hashes match')
