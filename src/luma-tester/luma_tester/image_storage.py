# SPDX-License-Identifier: Apache-2.0
"""Explicit deletion of managed downloads and the workspaces that depend on them."""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil

from . import host, provision, vm
from .core import Session, TesterError, read_manifest


def cache_path(root, image):
    sha = image['sha256']
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        raise TesterError('Invalid downloaded image identity.')
    extension = '.iso' if image.get('media') == 'iso' else '.qcow2'
    return Path(root) / 'images' / (sha + extension)


@contextmanager
def lease(root, sha):
    # Downloads, preparation and deletion serialize per image, across processes.
    cache_path(root, dict(sha256=sha))
    directory = Path(root) / 'image-locks'
    if directory.is_symlink():
        raise TesterError('Cannot use a linked image lock folder.')
    host.private_directory(directory)
    with (directory / (sha + '.lock')).open('a+b') as stream:
        try:
            host.lock(stream)
        except OSError as exc:
            raise TesterError('This image is busy. Wait for its current operation to finish.') from exc
        yield


@dataclass(frozen=True)
class Removal:
    image: Path
    sha256: str
    media: str
    size: int
    workspaces: tuple[Path, ...]
    sessions: tuple[Path, ...]
    registrations: tuple[Path, ...]
    references: tuple[str, ...]
    presets: tuple[str, ...]


def inspect(root, image):
    """Describe the exact scope that the user will confirm; never follow imports."""
    root = Path(root)
    base = cache_path(root, image)
    if base.parent.is_symlink() or base.is_symlink() or not base.is_file():
        raise TesterError('This image is not a managed download on this computer.')
    sha = image['sha256']
    sessions, workspaces = [], []
    sessions_root = root / 'sessions'
    if sessions_root.is_symlink():
        raise TesterError('Cannot delete images through a linked workspace folder.')
    for directory in sorted(sessions_root.glob('*')):
        if directory.is_symlink():
            raise TesterError('Cannot check a linked workspace. No image was deleted.')
        manifest = directory / 'manifest.json'
        if not manifest.exists():
            if directory.is_dir() and any(directory.iterdir()):
                raise TesterError('A workspace has no image record. No image was deleted.')
            continue
        try:
            value = read_manifest(manifest)
        except (OSError, ValueError, TypeError, TesterError) as exc:
            raise TesterError('A workspace image record cannot be checked. No image was deleted.') from exc
        if value['image']['sha256'] != sha:
            continue
        if value['id'] != directory.name or manifest.is_symlink():
            raise TesterError('A workspace image record does not match its folder.')
        sessions.append(manifest)
        session = Session(manifest, root)
        # An installed ISO workspace is independent of its original installer.
        # A prepared disk workspace is a backing overlay, including its snapshots.
        if value.get('media') != 'iso' or not provision.ready(session):
            workspaces.append(directory)
    registrations = []
    for name in ('imported-images', 'preset-images'):
        directory = root / name
        if directory.is_symlink():
            raise TesterError('Cannot delete images through a linked image folder.')
        for path in sorted(directory.glob('*.json')):
            if path.is_symlink():
                continue
            try:
                value = read_manifest(path)
            except (OSError, ValueError, TypeError, TesterError):
                continue
            if value['image']['sha256'] == sha and (image.get('media') != 'iso' or not sessions):
                registrations.append(path)
    from .presets import load
    saved_presets = load(root/'presets.json')
    removed = {str(p.resolve()) for p in registrations}
    removed.update(str((p/'manifest.json').resolve()) for p in workspaces)
    references = [p['manifest'] for p in saved_presets]
    preferences = root/'preferences.json'
    if preferences.is_file():
        value = json.loads(preferences.read_text())
        references += value.get('images', [])
        for key in ('selected_images', 'selected_variants'):
            references += list(value.get(key, {}).values())
    for reference in references:
        path = Path(reference)
        try:
            value = read_manifest(path)
            if value['image']['sha256'] == sha:
                independent = value.get('media') == 'iso' and provision.ready(Session(path,root))
                if not independent:
                    removed.add(str(path.resolve()))
        except (OSError, ValueError, TypeError, TesterError):
            continue
    names = tuple(p['name'] for p in saved_presets if str(Path(p['manifest']).resolve()) in removed)
    return Removal(base, sha, image.get('media', 'disk'), base.stat().st_size,
                   tuple(workspaces), tuple(sessions), tuple(registrations), tuple(sorted(removed)), names)


def _unlink(path):
    # Verified downloads are read-only; Windows requires clearing that flag.
    if path.exists():
        if host.windows():
            path.chmod(0o600)
        path.unlink()


def remove(root, confirmed):
    """Recheck dependencies and leases before any deletion; scope cannot grow."""
    root = Path(root)
    with lease(root, confirmed.sha256), ExitStack() as locks:
        current = inspect(root, dict(sha256=confirmed.sha256, media=confirmed.media))
        if current != confirmed:
            raise TesterError('This image’s workspaces changed. Review the deletion again.')
        for manifest in current.sessions:
            session = Session(manifest, root)
            locks.enter_context(session.lock())
            if session.status() != 'stopped':
                raise TesterError('Shut down Luma before deleting this image.')
        # Keep metadata for independently installed ISO workspaces. Never remove
        # an original import, shared folder, or a different image's workspace.
        removed = set(current.references)
        for directory in current.workspaces:
            shutil.rmtree(directory)
        _unlink(current.image)
        for path in current.registrations:
            _unlink(path)
        from .presets import load
        presets = load(root/'presets.json')
        if presets:
            vm.atomic_json(root/'presets.json', [p for p in presets if str(Path(p['manifest']).resolve()) not in removed])
        preferences = root/'preferences.json'
        if preferences.is_file():
            value = json.loads(preferences.read_text())
            value['images'] = [p for p in value.get('images', []) if str(Path(p).resolve()) not in removed]
            for key in ('selected_images', 'selected_variants'):
                value[key] = {k:p for k,p in value.get(key, {}).items() if str(Path(p).resolve()) not in removed}
            vm.atomic_json(preferences, value)
    return current
