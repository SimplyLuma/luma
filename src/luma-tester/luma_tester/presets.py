# SPDX-License-Identifier: Apache-2.0
"""Named local configurations, bound to an exact image rather than a nightly label."""
import json
import os
from pathlib import Path
import tempfile

from .core import TesterError, read_manifest


def load(path):
    try:
        data = json.loads(Path(path).read_text())
        if isinstance(data, list):
            return [item for item in data[:32] if isinstance(item, dict) and
                    isinstance(item.get('name'), str) and isinstance(item.get('manifest'), str) and
                    item.get('intent') in ('try', 'develop') and
                    item.get('target') in ('desktop','mobile','tablet','tv','smart-device') and isinstance(item.get('image'), dict)]
    except (OSError, ValueError):
        pass
    return []


def save(path, name, manifest_path, intent, settings=None, experience=None):
    name = name.strip()
    if not 1 <= len(name) <= 80 or any(ord(char) < 32 for char in name):
        raise TesterError('Name the preset using 1–80 characters.')
    if intent not in ('try', 'develop'):
        raise TesterError('Unknown workspace mode.')
    manifest_path = Path(manifest_path).resolve()
    manifest = read_manifest(manifest_path)
    from .experiences import compatible
    experience=experience or manifest['target']
    if not compatible(experience,manifest):raise TesterError('This image does not match the preset experience.')
    item = dict(name=name, manifest=str(manifest_path), target=experience,
                intent=intent, arch=manifest['arch'], image=manifest['image'])
    if settings is not None:
        from .vm import validate, DEFAULTS
        from copy import deepcopy
        item['vm'] = validate(dict(DEFAULTS,**deepcopy(settings)))
    items = [item] + [old for old in load(path) if old['name'] != name]
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(items[:32], stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return items[:32]


def resolve(item):
    path = Path(item['manifest'])
    manifest = read_manifest(path)
    # Moving a catalog or silently replacing a named nightly must not select
    # different bytes under an existing preset's architecture/profile identity.
    from .experiences import compatible
    if (not compatible(item['target'],manifest) or manifest['arch'] != item['arch'] or
            {key: manifest['image'][key] for key in ('sha256', 'bytes')} !=
            {key: item['image'].get(key) for key in ('sha256', 'bytes')}):
        raise TesterError('This preset’s image has changed. Choose the build again and save a new preset.')
    return path
