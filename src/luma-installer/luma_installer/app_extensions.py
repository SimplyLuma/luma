# SPDX-License-Identifier: Apache-2.0
"""Fixed signed Office extension authority for the maintained document apps.

The application declaration comes from its verified OSTree commit. Instance
metadata only selects a commit to verify; it cannot introduce another extension
or a host directory. This module does not grant extension authority to other
applications or authorize unmaintained Flatpak extensions.
"""
import configparser
import os
from pathlib import Path
import re
import stat

from .app_data_migration import MigrationError

OFFICE = 'org.projectluma.Platform.Office'
OFFICE_APPS = frozenset({'org.projectluma.Write', 'org.projectluma.Grid',
                         'org.projectluma.Stage'})


def _extensions(raw, app):
    if not isinstance(raw, str) or len(raw) > 4096:
        raise MigrationError('The application extension identity is invalid.')
    if app not in OFFICE_APPS:
        if raw:
            raise MigrationError('Altered application deployments cannot access application services.')
        return {}
    entries = raw[:-1] if raw.endswith(';') else raw
    result = {}
    for entry in entries.split(';'):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{2,200}=[a-f0-9]{64}', entry):
            raise MigrationError('The document engine is not a maintained signed extension.')
        name, commit = entry.split('=', 1)
        if name not in {OFFICE, app + '.Locale'} or name in result:
            raise MigrationError('The application has an unsupported or duplicate extension.')
        result[name] = commit
    if OFFICE not in result:
        raise MigrationError('The signed document engine is missing. Repair the application in Depot.')
    return result


def _signed_metadata(installation, commit):
    import gi
    gi.require_version('OSTree', '1.0')
    from gi.repository import OSTree
    repository = OSTree.Repo.new(installation.get_path().get_child('repo'))
    repository.open(None)
    ok, root, actual_commit = repository.read_commit(commit, None)
    if not ok or actual_commit != commit:
        raise MigrationError('The signed application declaration is unavailable.')
    stream = root.get_child('metadata').read(None)
    try:
        data = bytearray()
        while len(data) <= 65536:
            block = stream.read_bytes(65537 - len(data), None).get_data()
            if not block:
                break
            data.extend(block)
    finally:
        stream.close(None)
    if len(data) > 65536:
        raise MigrationError('The signed application declaration is too large.')
    metadata = configparser.ConfigParser(interpolation=None, strict=True)
    metadata.read_string(data.decode('utf-8'))
    if metadata.defaults():
        raise MigrationError('The signed application declaration has unsupported defaults.')
    return metadata


def _declaration(metadata, app, name, app_branch):
    if metadata.get('Application', 'name', fallback='') != app:
        raise MigrationError('The extension declaration belongs to another application.')
    section = 'Extension ' + name
    if not metadata.has_section(section):
        raise MigrationError('The signed application did not declare this extension.')
    values = dict(metadata.items(section))
    if name == OFFICE:
        required = {'directory': 'lib/office', 'version': '44', 'add-ld-path': 'lib64'}
        optional = {'no-autodownload': 'false', 'autodelete': 'false'}
        if (any(values.get(key) != value for key, value in required.items())
                or set(values) - (required.keys() | optional.keys())
                or any(key in values and values[key] != value for key, value in optional.items())):
            raise MigrationError('The signed document engine declaration is unsupported.')
        return 'lib/office', '44'
    if name != app + '.Locale':
        raise MigrationError('The application extension is unsupported.')
    # Flatpak Builder may split the application's own translations. No other
    # locale provider or caller-selected directory is accepted here.
    allowed = {'directory', 'version', 'autodelete', 'locale-subset', 'subdirectories'}
    if (set(values) - allowed or values.get('directory') != 'share/runtime/locale'
            or values.get('version', app_branch) != app_branch
            or values.get('locale-subset') != 'true'
            or values.get('autodelete') != 'true'
            or values.get('subdirectories', 'false') != 'false'):
        raise MigrationError('The signed application locale declaration is unsupported.')
    return 'share/runtime/locale', app_branch


def _safe_directory(path):
    for member in (path, *path.parents):
        status = member.lstat()
        if (not stat.S_ISDIR(status.st_mode) or status.st_uid not in (0, os.getuid())
                or status.st_mode & 0o022):
            raise MigrationError('The installed extension directory is unsafe.')


def _runtime_mount(installation, name, arch, branch, commit, pid, directory):
    from .depot_flatpak import validate_remote
    from .native_app_roles import verify_deployed_commit
    validate_remote(installation.get_remote_by_name('luma', None), 'luma')
    exact_ref = f'runtime/{name}/{arch}/{branch}'
    refs = [ref for ref in installation.list_installed_refs(None)
            if ref.format_ref() == exact_ref]
    if len(refs) != 1 or refs[0].get_origin() != 'luma':
        raise MigrationError('The signed application extension is missing or ambiguous.')
    ref = refs[0]
    if (ref.get_name() != name or ref.get_arch() != arch or ref.get_branch() != branch
            or not re.fullmatch('[a-f0-9]{64}', ref.get_commit() or '')):
        raise MigrationError('The installed extension has a different identity.')
    # Verify the advertised commit and its exact runtime ref binding, including
    # retained running A while the installed extension has advanced to B.
    verify_deployed_commit(installation, ref, commit=commit, exact_ref=exact_ref)
    base = Path(installation.get_path().get_path())
    original = base / 'runtime' / name / arch / branch / commit / 'files'
    candidates = [original, base / '.removed' / f'{name}-{commit}' / 'files']
    if ref.get_commit() == commit:
        declared = Path(ref.get_deploy_dir()) / 'files'
        if declared != original:
            raise MigrationError('The installed extension uses an unsupported deployment path.')
    mounted = Path(f'/proc/{pid}/root/app') / directory
    actual = mounted.lstat()
    if not stat.S_ISDIR(actual.st_mode):
        raise MigrationError('The application extension mount is not a directory.')
    matches = []
    for candidate in candidates:
        try:
            status = candidate.lstat()
        except FileNotFoundError:
            continue
        _safe_directory(candidate)
        if (status.st_dev, status.st_ino) == (actual.st_dev, actual.st_ino):
            matches.append(candidate)
    if len(matches) != 1:
        raise MigrationError('The application extension mount differs from its signed deployment.')
    # Do not accept an inode that was replaced during the signature/path check.
    after = mounted.lstat()
    if (after.st_dev, after.st_ino) != (actual.st_dev, actual.st_ino):
        raise MigrationError('The application extension mount changed.')


def verify_application_extensions(info, app, pid, installation, app_commit):
    selected = _extensions(info.get('Instance', 'app-extensions', fallback=''), app)
    if not selected:
        return
    metadata = _signed_metadata(installation, app_commit)
    arch = info.get('Instance', 'arch')
    app_branch = info.get('Instance', 'branch')
    from .depot_flatpak import BRANCHES
    if arch not in {'x86_64', 'aarch64'} or app_branch not in BRANCHES:
        raise MigrationError('The application extension architecture is unsupported.')
    for name, commit in selected.items():
        directory, branch = _declaration(metadata, app, name, app_branch)
        _runtime_mount(installation, name, arch, branch, commit, pid, directory)
