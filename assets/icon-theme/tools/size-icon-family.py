#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive revision 42 art without the outer rim from the owner's revision 40."""

import argparse
import hashlib
import json
import re
from pathlib import Path


THEME = Path(__file__).resolve().parents[1] / 'Prairie'
ORIGINAL = THEME / 'artwork/icon-family-r40'
SIZED = THEME / 'artwork/icon-family-r42'
MANIFEST = THEME / 'application-manifest.json'
TRANSFORM = ('<g id="dock-size" transform="translate(512 512) '
             'scale(1.12) translate(-512 -512)">')


def sized(source: bytes) -> bytes:
    content = source.decode('utf-8')
    if content.count('</defs>') != 1 or content.count('</svg>') != 1:
        raise ValueError('Icon structure changed; review the scaling transform')
    if 'id="dock-size"' in content or 'viewBox="0 0 1024 1024"' not in content:
        raise ValueError('Expected unscaled 1024 px revision 40 art')
    # Remove only the explicitly named decorative tile rim. Interior strokes,
    # highlights, shadows and the author's clipping geometry stay intact.
    content, removed = re.subn(
        r'<g id="plate-rim"[^>]*><rect\b[^>]*/></g>', '', content)
    if removed != 1:
        raise ValueError('Expected exactly one decorative plate rim')
    content = content.replace('</defs>', '</defs>' + TRANSFORM, 1)
    content = content.replace('</svg>', '</g></svg>', 1)
    return content.encode('utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    selected = [item for item in manifest['icons']
                if 'icon-family-r40/' in item['source'] or
                'icon-family-r41/' in item['source'] or
                'icon-family-r42/' in item['source']]
    if len(selected) != 34:
        raise ValueError(f'Expected 34 selected icons, found {len(selected)}')
    SIZED.mkdir(parents=True, exist_ok=True)
    for item in selected:
        name = Path(item['source']).name
        original = (ORIGINAL / name).read_bytes()
        if item.get('bundled'):
            bundled_source = f'artwork/icon-family-r40/{name}'
            bundled_hash = hashlib.sha256(original).hexdigest()
            if args.check:
                if item.get('bundled_source') != bundled_source or \
                        item.get('bundled_sha256') != bundled_hash:
                    raise ValueError(f'Stale bundled icon source: {name}')
            else:
                item['bundled_source'] = bundled_source
                item['bundled_sha256'] = bundled_hash
        output = sized(original)
        target = SIZED / name
        if args.check:
            if not target.is_file() or target.read_bytes() != output:
                raise ValueError(f'Stale dock-sized icon: {name}')
            if item['source'] != f'artwork/icon-family-r42/{name}' or \
                    item['sha256'] != hashlib.sha256(output).hexdigest():
                raise ValueError(f'Stale icon manifest: {name}')
        else:
            target.write_bytes(output)
            item['source'] = f'artwork/icon-family-r42/{name}'
            item['sha256'] = hashlib.sha256(output).hexdigest()
    if not args.check:
        manifest['source'] = ('Owner-selected Luma icon family revision 40 '
                              '(2026-09-27), derived revision 42 retains dock '
                              'sizing and removes the decorative outer rim; '
                              'preserved v3 icons for unselected apps')
        manifest['license'] = ('Luma-owned icon-family revision 40 and derived '
                               'revision 42 artwork: CC-BY-SA-4.0; owner confirmed '
                               'distribution rights on 2026-09-28. Attributed '
                               'Lucide geometry retains ISC/MIT notices.')
        MANIFEST.write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'{len(selected)} dock-sized icons: '
          f'{"verified" if args.check else "generated"}')


if __name__ == '__main__':
    main()
