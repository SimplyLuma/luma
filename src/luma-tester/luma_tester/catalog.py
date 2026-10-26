# SPDX-License-Identifier: Apache-2.0
"""Resolve portable image assets; downloaded images remain checksum verified by Session."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from .core import TesterError


def resolve_asset(manifest, portable, appimage=None):
    result = deepcopy(manifest)
    bundled = result.pop('bundled_image', None)
    external = result.pop('external_image', None)
    if bundled and external:
        raise TesterError('An image cannot be both embedded and external.')
    base = Path(portable).resolve()
    if bundled:
        image = (base / bundled).resolve()
        if not image.is_relative_to(base):
            raise TesterError('Bundled image must remain inside the portable directory.')
        result['image']['url'] = image.as_uri()
    if external:
        expected = 'images/' + result['image']['sha256'] + ('.iso' if result.get('media')=='iso' else '.qcow2')
        if external != expected:
            raise TesterError('External images must use their checksum filename in images/.')
        roots = [Path(appimage).resolve().parent] if appimage else [base, base.parent]
        candidates = []
        for root in roots:
            image = (root / external).resolve()
            if not image.is_relative_to(root):
                raise TesterError('External image must remain inside its asset directory.')
            candidates.append(image)
        present = next((path for path in candidates if path.is_file()), None)
        if present or urlparse(result['image']['url']).scheme != 'https':
            result['image']['url'] = (present or candidates[0]).as_uri()
    return result
