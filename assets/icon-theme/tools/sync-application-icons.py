#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate named theme assets from the approved catalog; --check detects drift."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
ROOT = Path(__file__).resolve().parents[3]
THEME = ROOT / 'assets/icon-theme/Prairie'


def generate(source, ratio, precomposed=False):
    root = ET.fromstring(source)
    box = list(map(float, root.attrib['viewBox'].split()))
    if box[:2] != [0, 0] or box[2] != box[3] or box[2] <= 0:
        raise ValueError('Application art requires a square, origin-aligned canvas')
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1] in {'script', 'foreignObject', 'image'}:
            raise ValueError('Application art must be self-contained vector geometry')
        for key, value in node.attrib.items():
            if key.rsplit('}', 1)[-1].startswith('on') or ('href' in key and not value.startswith('#')):
                raise ValueError('External resources and event handlers are prohibited')
    if any(n.get('id') == 'lumaTile' for n in root.iter()):
        raise ValueError('The source must be unwrapped authoring artwork')
    # The approved icon-family art carries its own inset tile, corner clip and
    # shadow. Preserve that geometry instead of wrapping it in a second tile.
    if precomposed:
        return source
    defs = root.find(f'{{{NS}}}defs')
    if defs is None:
        defs = ET.SubElement(root, f'{{{NS}}}defs')
    clip = ET.SubElement(defs, f'{{{NS}}}clipPath', {'id': 'lumaTile'})
    side = box[2]
    ET.SubElement(clip, f'{{{NS}}}rect', {'width': f'{side:g}', 'height': f'{side:g}',
                   'rx': f'{side * ratio:g}', 'ry': f'{side * ratio:g}'})
    group = ET.Element(f'{{{NS}}}g', {'clip-path': 'url(#lumaTile)'})
    for child in list(root):
        if child.tag.rsplit('}', 1)[-1] not in {'title', 'desc', 'metadata', 'defs'}:
            root.remove(child)
            group.append(child)
    root.append(group)
    return ET.tostring(root, encoding='utf-8') + b'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((THEME / 'application-manifest.json').read_text())
    ratio = json.loads((ROOT / 'config/shared/design-tokens.json').read_text())['icon']['application_corner_ratio']
    seen = set()
    bundled = 0
    for item in manifest['icons']:
        source = (THEME / item['source']).read_bytes()
        if hashlib.sha256(source).hexdigest() != item['sha256']:
            raise ValueError(f"Unreviewed artwork change: {item['source']}")
        output = generate(source, ratio, item.get('precomposed', False))
        for alias in item['aliases']:
            if alias in seen or '/' in alias or alias.startswith('.'):
                raise ValueError(f'Invalid or duplicate alias: {alias}')
            seen.add(alias)
            target = THEME / 'scalable/apps' / f'{alias}.svg'
            if args.check:
                if not target.is_file() or target.read_bytes() != output:
                    raise ValueError(f'Stale application icon: {alias}')
            else:
                target.write_bytes(output)
        # An application that bundles its own hicolor identity ships the
        # approved artwork itself, unwrapped: Luma's application-icon component
        # adds the tile, so a second baked clip would round the corners twice.
        # The theme's own scalable/apps copy stays clipped for the native
        # rasterizers that draw outside App Kit. Listing the bundled paths here
        # means a canvas revision reaches every app by re-running this
        # generator, instead of eight separate hand copies going stale.
        bundled_source = source
        if item.get('bundled_source'):
            bundled_source = (THEME / item['bundled_source']).read_bytes()
            if hashlib.sha256(bundled_source).hexdigest() != item['bundled_sha256']:
                raise ValueError(f"Unreviewed bundled artwork change: {item['bundled_source']}")
        for relative in item.get('bundled', ()):
            if relative.startswith('/') or '..' in Path(relative).parts:
                raise ValueError(f'Invalid bundled icon path: {relative}')
            bundled += 1
            target = ROOT / relative
            if args.check:
                if not target.is_file() or target.read_bytes() != bundled_source:
                    raise ValueError(f'Stale bundled application icon: {relative}')
            else:
                target.write_bytes(bundled_source)
    navigation = json.loads((THEME / 'upstream/lucide/navigation-manifest.json').read_text())
    for item in navigation['files']:
        path = THEME / 'symbolic/actions' / (item['alias'] + '.svg')
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['gtk_symbolic_sha256']:
            raise ValueError(f"Stale navigation icon: {item['alias']}")
        # Entries whose upstream Lucide SVG is vendored in-tree carry their
        # source digest too, so the ISC geometry a glyph claims is checkable
        # here rather than only against a path on one designer's machine.
        if not item['source'].startswith('/'):
            upstream = THEME / item['source']
            if hashlib.sha256(upstream.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError(f"Unreviewed Lucide source: {item['source']}")
    print(f"{len(manifest['icons'])} source icons, {len(seen)} aliases, "
          f"{bundled} bundled, {len(navigation['files'])} Lucide symbolics: "
          f"{'verified' if args.check else 'generated'}")


if __name__ == '__main__':
    main()
