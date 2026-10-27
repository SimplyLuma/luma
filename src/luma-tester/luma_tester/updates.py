# SPDX-License-Identifier: Apache-2.0
"""Discover and stage app packages; never replace or execute the running app."""
import re
from pathlib import Path

from . import __version__
from .core import TesterError
from .downloads import HubClient, download, host_arch, host_os, validate_artifact


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}', value):
        return None
    return tuple(map(int, value.split('.')))


def package_identity(artifact):
    validate_artifact(artifact)
    match = re.fullmatch(r'tester/([^/]+)/([a-z0-9_-]+)', artifact['artifact_id'])
    if artifact.get('kind') != 'tester_app' or not match or version(match[1]) is None:
        raise TesterError('Invalid Tester app release identity.')
    release, platform = match.groups()
    if artifact.get('version', release) != release or artifact.get('platform', platform) != platform:
        raise TesterError('Tester release metadata disagrees with its identity.')
    suffix = {'linux': '.AppImage', 'macos': '.dmg', 'windows': '.zip'}.get(platform.split('-')[0])
    if suffix is None or not str(artifact.get('file', '')).lower().endswith(suffix.lower()):
        raise TesterError('Unsupported Tester app package.')
    return release, platform, suffix


def latest(artifacts, current=__version__, system=None, architecture=None):
    """The emulated guest CPU never participates in app package selection."""
    current = version(current)
    if current is None:
        return None
    system, architecture = system or host_os(), architecture or host_arch()
    platforms = {
        ('linux', 'x86_64'): ('linux-x86_64',),
        ('linux', 'aarch64'): ('linux-aarch64', 'linux-arm64'),
        ('macos', 'x86_64'): ('macos-x86_64', 'macos-universal'),
        ('macos', 'aarch64'): ('macos-arm64', 'macos-aarch64', 'macos-universal'),
        ('windows', 'x86_64'): ('windows-x86_64',),
        ('windows', 'aarch64'): ('windows-arm64', 'windows-aarch64'),
    }.get((system, architecture), ())
    candidates = []
    for item in artifacts:
        try:
            release, platform, _ = package_identity(item)
        except (TesterError, TypeError, ValueError):
            continue
        if platform in platforms and version(release) > current:
            candidates.append((version(release), -platforms.index(platform), item))
    return max(candidates, key=lambda row: row[:2])[2] if candidates else None


def check(client=None):
    return latest((client or HubClient()).catalog(kind='tester_app'))


def stage(artifact, root, report=lambda message: None, progress=None, client=None):
    release, platform, suffix = package_identity(artifact)
    # Catalog filenames never choose a local path. Keep update packages out of
    # workspace directories and retain .part files for the normal resume path.
    directory = Path(root)/'updates'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory/f'Luma-Tester-{release}-{platform}{suffix}'
    download(artifact, destination, experience='other', purpose='update',
             report=report, progress=progress, client=client)
    return destination
