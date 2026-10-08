#!/usr/bin/env python3
"""Validate the frozen approved system artwork without rewriting any SVG."""
# SPDX-License-Identifier: Apache-2.0
import argparse
import configparser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET

SVG = 'http://www.w3.org/2000/svg'
ALLOWED = {'svg', 'title', 'metadata', 'defs', 'linearGradient', 'radialGradient',
           'stop', 'ellipse', 'path', 'g', 'polyline', 'line', 'circle', 'rect',
           'clipPath', 'use', 'mask'}
CONTEXTS = {'places', 'mimetypes', 'devices'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def relative(value):
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts, f'Unsafe path: {value}')
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_svg(data, label):
    require(not re.search(br'<!\s*(DOCTYPE|ENTITY)', data, re.I), f'{label}: XML declaration with external/entity capability')
    root = ET.fromstring(data)
    require(root.tag == f'{{{SVG}}}svg', f'{label}: missing SVG namespace/root')
    ids = [node.attrib['id'] for node in root.iter() if 'id' in node.attrib]
    require(len(ids) == len(set(ids)), f'{label}: duplicate paint/clip IDs')
    references = []
    for node in root.iter():
        require(node.tag.startswith(f'{{{SVG}}}') and node.tag.split('}')[-1] in ALLOWED,
                f'{label}: unsupported or active element {node.tag}')
        for key, value in node.attrib.items():
            attribute = key.split('}')[-1]
            require(not attribute.lower().startswith('on'), f'{label}: event handler')
            require(attribute != 'style', f'{label}: unreviewed inline CSS')
            if attribute == 'href':
                require(value.startswith('#') and len(value) > 1, f'{label}: external resource')
                references.append(value[1:])
            for reference in re.findall(r'url\(\s*[\'"]?([^\)\'\"]+)', value):
                require(reference.startswith('#'), f'{label}: external paint resource')
                references.append(reference[1:].strip())
    require(all(ref in ids for ref in references), f'{label}: unresolved internal reference')
    viewbox = [float(number) for number in root.attrib.get('viewBox', '').split()]
    require(len(viewbox) == 4 and viewbox[2] > 0 and viewbox[3] > 0, f'{label}: invalid viewBox')


def validate(theme, manifest_path, integration_path, payload=False):
    manifest = json.loads(manifest_path.read_text())
    integration = json.loads(integration_path.read_text())
    index = configparser.ConfigParser(interpolation=None)
    index.read(theme / 'index.theme')
    require(index['Icon Theme']['Inherits'] == 'Adwaita,hicolor', 'Fallback inheritance changed')
    directories = index['Icon Theme']['Directories'].split(',')
    for context, label in [('places', 'Places'), ('mimetypes', 'MimeTypes'), ('devices', 'Devices')]:
        section = f'scalable/{context}'
        require(section in directories and index[section]['Context'] == label and index[section]['Type'] == 'Scalable', f'Invalid theme context: {section}')
    icons = manifest['icons']
    require(len(icons) == manifest['approved_names'] == 51, 'Approval count differs from frozen 51-name handoff')
    require(len({i['source'] for i in icons}) == manifest['unique_assets'] == 41, 'Expected 41 unique approved source assets')
    require(len({i['name'] for i in icons}) == len(icons), 'Duplicate lookup name')
    require(len({i['destination'] for i in icons}) == len(icons), 'Duplicate destination')
    snapshot = theme / 'artwork' / str(relative(integration['snapshot']))
    if not payload:
        for name, expected in integration['snapshot_metadata_sha256'].items():
            require(digest(snapshot / str(relative(name))) == expected, f'Stale approval evidence: {name}')
        for name, expected in integration['lucide_reference_sha256'].items():
            require(digest(snapshot / 'lucide-source' / str(relative(name))) == expected, f'Lucide reference changed: {name}')
        selections = json.loads((snapshot / 'selections.json').read_text())
        inventory = json.loads((snapshot / 'inventory.json').read_text())
    for icon in icons:
        name, category = icon['name'], icon['category']
        require(category in CONTEXTS and not name.endswith('-symbolic'), f'Unapproved context: {name}')
        require(icon['destination'] == f'scalable/{category}/{name}.svg', f'Unexpected destination: {name}')
        target = theme / str(relative(icon['destination']))
        require(target.is_file() and not target.is_symlink(), f'Missing regular icon: {target}')
        require(digest(target) == icon['sha256'], f'Approved bytes changed: {name}')
        validate_svg(target.read_bytes(), name)
        if not payload:
            source = snapshot / str(relative(icon['source']))
            require(digest(source) == icon['sha256'], f'Source snapshot changed: {name}')
            approval = selections[icon['id']]
            require(approval.get('selected') and approval.get('file') == icon['source'] and approval['name'] == name,
                    f'Not approved: {name}')
            require(inventory[int(icon['id']) - 1]['name'] == name, f'Inventory ID/name mismatch: {name}')
    by_name = {i['name']: i for i in icons}
    alias_paths = []
    for alias in integration['aliases']:
        path = str(relative(alias['destination']))
        require(path not in alias_paths and path not in {i['destination'] for i in icons}, 'Duplicate integration alias')
        alias_paths.append(path)
        require(alias.get('reason'), 'Alias requires explicit reason')
        require(digest(theme / path) == by_name[alias['source_name']]['sha256'], f'Alias artwork differs: {path}')
    # New contexts must contain only approved files and explicitly documented aliases.
    expected_paths = {i['destination'] for i in icons} | set(alias_paths)
    for context in ('mimetypes', 'devices'):
        actual = {str(p.relative_to(theme)) for p in (theme / 'scalable' / context).rglob('*') if p.is_file()}
        require(actual == {p for p in expected_paths if p.startswith(f'scalable/{context}/')}, f'Unapproved {context} payload')
    full = theme / 'scalable/places/user-trash-full.svg'
    require(full.is_file() and digest(full) != by_name['user-trash']['sha256'], 'Full Trash must remain a separate existing design')
    if payload or (theme / 'scalable/apps/user-trash-full.svg').exists():
        require(digest(full) == digest(theme / 'scalable/apps/user-trash-full.svg'), 'Full Trash dock alias differs')
    print(f'Approved system artwork: {len(icons)} names, 41 source assets, {len(alias_paths)} documented integration aliases; hashes and self-contained SVGs pass')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--theme', type=Path, default=Path(__file__).resolve().parents[1] / 'Prairie')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--integration', type=Path)
    parser.add_argument('--payload', action='store_true', help='Validate extracted/installed approved destinations without source snapshot')
    args = parser.parse_args()
    validate(args.theme, args.manifest or args.theme / 'system-manifest.json',
             args.integration or args.theme / 'system-integration.json', args.payload)


if __name__ == '__main__':
    main()
