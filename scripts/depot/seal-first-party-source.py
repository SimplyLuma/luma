#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Seal the maintained responsive application source without inventing commits.

Every input byte/mode is recorded. Outputs are immutable, content-addressed
archives plus a build manifest understood by the normal Flatpak producer.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import yaml

REGISTRY = Path(__file__).resolve().parents[2] / 'packaging/flatpak/first-party-updates.json'
APPS = {entry['id'].rsplit('.', 1)[-1]: entry
        for entry in json.loads(REGISTRY.read_text())['applications']
        if entry.get('recipe') and entry['producer'] not in {'creative-external'}}

def seal(repo, app, output, filer_srpm=None, viola_engine=None):
    entry = APPS[app]
    source = repo / entry['source_tree']
    app_id = entry['id']
    if (app_id == 'org.gnome.Nautilus') != (filer_srpm is not None):
        raise ValueError('Only Filer requires an explicitly admitted --filer-srpm input')
    if (app_id == 'com.rhyme.viola') != (viola_engine is not None):
        raise ValueError('Only Viola requires an explicitly admitted --viola-engine input')
    recipe = repo / 'packaging/flatpak/apps' / app_id
    if output.exists():
        raise ValueError('output already exists; source snapshots are immutable')
    output.mkdir(mode=0o755, parents=True)
    paths = [repo / 'LICENSE.md'] + sorted(source.rglob('*'))
    upstream = []
    if app_id == 'org.gnome.Nautilus':
        paths += sorted((repo / 'patches/nautilus').glob('0[0-9][0-9][0-9]-*.patch'))
        paths += [repo / 'config/desktop/inputs.env',
                  repo / 'scripts/packages/build-nautilus.sh',
                  repo / 'scripts/packages/run-in-rpm-builder.sh',
                  repo / 'scripts/packages/patch-series.sh',
                  repo / 'scripts/packages/spec-release.sh',
                  repo / 'src/luma-shell-state/org.project_luma.shell-state.gschema.xml']
        helper = repo / 'packaging/nautilus/flatpak-source.py'
        specification = importlib.util.spec_from_file_location('luma_filer_source', helper)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        upstream = module.admitted_upstream(filer_srpm)
    if app_id == 'com.rhyme.viola':
        paths += sorted((repo / 'packaging/viola').rglob('*'))
        paths += [repo / 'scripts/packages/build-viola-browser.sh',
                  repo / 'scripts/packages/run-in-rpm-builder.sh']
        helper = repo / 'packaging/viola/flatpak-source.py'
        specification = importlib.util.spec_from_file_location('luma_viola_source', helper)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        upstream = module.admitted_engine(viola_engine)
    if app_id == 'org.projectluma.Depot':
        # Maintained UI view models, rendering and typed client adapters only;
        # source contains no installed trust data, host profiles or signing keys.
        paths += sorted((repo / 'src/luma-installer/luma_installer').rglob('*'))
    if app_id in {'org.projectluma.Write', 'org.projectluma.Grid',
                  'org.projectluma.Stage', 'org.projectluma.Session',
                  'org.projectluma.Reel'}:
        # Only the fixed typed preference importer runs in the application.
        # Native reader schemas and the host migration owner remain installed
        # by Installer; no host trust, credentials or full installer package.
        paths += [repo / 'src/luma-installer/luma_installer/app_preferences.py']
    if app_id == 'org.projectluma.Phone':
        # Only the maintained lightweight sandbox call client; host enrollment,
        # device identity, crypto and daemon code remain native host services.
        paths += [repo / 'src/luma-continuity/luma_continuity' / name
                  for name in ('__init__.py', 'phone_contract.py', 'call_provider.py')]
    if app_id == 'org.projectluma.Viewer':
        # Viewer consumes the same maintained read-only EPUB engine, including
        # its licensed assets; it does not implement a second reader engine.
        paths += sorted((repo / 'src/luma-leaf').rglob('*'))
    metainfo = recipe / f'{app_id}.metainfo.xml'
    materials = {}
    files = []
    for path in paths:
        if any(part in {'__pycache__', '.pytest_cache', '.git'} for part in path.parts):
            continue
        if path.is_symlink():
            raise ValueError(f'symlink input is not admitted: {path}')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f'nonregular source input: {path}')
        name = path.relative_to(repo).as_posix()
        if any(ord(c) < 32 for c in name):
            raise ValueError('control character in source name')
        data = path.read_bytes()
        mode = path.stat().st_mode & 0o777
        materials[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'mode': mode, 'bytes': len(data)}
        files.append((name, data, mode))
    for name, data, mode in upstream:
        if name in materials or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Invalid admitted upstream source member')
        materials[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'mode': mode, 'bytes': len(data)}
        files.append((name, data, mode))
    data = metainfo.read_bytes()
    materials['depot.metainfo.xml'] = {'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644, 'bytes': len(data)}
    files.append(('depot.metainfo.xml', data, 0o644))
    for path in sorted((recipe / 'media').glob('*.png')):
        if path.is_symlink() or not path.is_file():
            raise ValueError('application media must be a regular source file')
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        name = 'depot-media/' + digest + '.png'
        materials[name] = {'sha256': digest, 'mode': 0o644, 'bytes': len(data)}
        files.append((name, data, 0o644))
    if app not in ('Notes', 'Tide'):
        icon = repo / 'assets/icon-theme/Prairie/scalable/apps' / f'{app_id}.svg'
        creative_icons = {
            'org.projectluma.Write': 'data/org.projectluma.Write.svg',
            'org.projectluma.Grid': 'data/io.luma.Grid.svg',
            'org.projectluma.Stage': 'data/io.luma.Stage.svg',
            'org.projectluma.Session': 'data/org.projectluma.Session.svg',
            'org.projectluma.Reel': 'data/org.projectluma.Reel.svg',
        }
        if app_id in creative_icons:
            # Seal the same maintained artwork that the app installs, including
            # legacy source filenames whose installed identity is canonical.
            icon = source / creative_icons[app_id]
        if app_id in {'org.projectluma.Displays', 'org.projectluma.Imager'}:
            # These maintained applications ship their own canonical artwork.
            icon = source / 'data' / f'{app_id}.svg'
        if app_id == 'org.projectluma.Charlie':
            icon = source / 'data/icons/hicolor/scalable/apps' / f'{app_id}.svg'
        if app_id == 'com.rhyme.viola':
            icon = source / 'appkit-spike/assets/org.projectluma.Viola.NativeIntegration.svg'
        if icon.is_symlink() or not icon.is_file():
            raise ValueError('The maintained application icon is missing or unsafe')
        data = icon.read_bytes()
        materials['depot-icon.svg'] = {'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644, 'bytes': len(data)}
        files.append(('depot-icon.svg', data, 0o644))
    manifest_bytes = (json.dumps({'schema': 'org.projectluma.app-source/v1', 'app_id': app_id,
                                'source_revision': 'working-snapshot', 'materials': materials}, sort_keys=True, indent=2) + '\n').encode()
    archive = output / 'source.tar'
    with tarfile.open(archive, 'w', format=tarfile.PAX_FORMAT) as tf:
        for name, data, mode in files + [('SOURCE-MANIFEST.json', manifest_bytes, 0o644)]:
            info = tarfile.TarInfo('source/' + name)
            info.size, info.mode, info.mtime = len(data), mode, 0
            info.uid = info.gid = 0
            tf.addfile(info, io.BytesIO(data))
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    sealed = output / f'source-{digest}.tar'
    archive.rename(sealed)
    (output / 'SOURCE-MANIFEST.json').write_bytes(manifest_bytes)
    doc = yaml.safe_load((recipe / f'{app_id}.yml').read_text())
    if len(doc['modules']) != 1:
        raise ValueError('first-party application recipe must have one maintained module')
    doc['modules'][0]['sources'] = [{'type': 'archive', 'path': sealed.name, 'sha256': digest}]
    (output / f'{app_id}.yml').write_text(yaml.safe_dump(doc, sort_keys=False))
    print(json.dumps({'app_id': app_id, 'archive_sha256': digest, 'materials': len(materials), 'path': str(output)}))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', choices=APPS)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--filer-srpm', type=Path)
    parser.add_argument('--viola-engine', type=Path)
    args = parser.parse_args()
    seal(args.repo.resolve(), args.app, args.output.resolve(), args.filer_srpm, args.viola_engine)
