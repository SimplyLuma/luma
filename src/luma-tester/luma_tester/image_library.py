# SPDX-License-Identifier: Apache-2.0
"""Published installation media, separate from qualified developer workspaces."""
import json
from pathlib import Path
from .core import Session, TesterError, validate_manifest
from .downloads import HubClient, download, validate_artifact
from .vm import atomic_json


def manifest_paths(root, remembered=(), catalog=None):
    """Recover registered downloads even if the app exited before remembering them."""
    paths = [Path(path) for path in remembered]
    paths += sorted((Path(root) / 'imported-images').glob('*.json'))
    if catalog:paths += sorted(Path(catalog).glob('*.json'))
    return list(dict.fromkeys(paths))


def usable(artifact):
    validate_artifact(artifact)
    if artifact.get('arch') not in ('x86_64', 'aarch64'):return False
    filename = str(artifact.get('file', '')).lower()
    if artifact.get('kind') == 'os_image' and filename.endswith('.iso'):return True
    if artifact.get('kind') != 'tester_image' or not filename.endswith('.qcow2'):return False
    try:
        profile = developer_profile(artifact)
        return profile['arch'] == artifact['arch'] and profile.get('media', 'disk') == 'disk'
    except (TesterError, TypeError, KeyError):return False


def developer_profile(artifact):
    profile = artifact.get('tester_profile')
    if not isinstance(profile, dict):raise TesterError('A virtual image needs a boot profile.')
    # Catalog metadata cannot substitute different bytes or a setup command.
    return validate_manifest(dict(profile, image=dict(url=artifact['url'],
        bytes=artifact['size_bytes'], sha256=artifact['sha256'])))


def target(artifact):
    return developer_profile(artifact)['target'] if artifact.get('kind') == 'tester_image' else 'desktop'


def cached(root):
    try:
        items=json.loads((Path(root)/'download-catalog.json').read_text())
        return [item for item in items if usable(item)]
    except (OSError,ValueError,TypeError,TesterError):return []


def refresh(root,client=None):
    items=[item for item in (client or HubClient()).catalog() if usable(item)]
    atomic_json(Path(root)/'download-catalog.json',items)
    return items


def fetch(artifact,root,experience,report=lambda text:None,progress=None):
    if not usable(artifact):raise TesterError('This download is not supported installation media.')
    root=Path(root)
    developer = artifact.get('kind') == 'tester_image'
    destination=root/'images'/(artifact['sha256']+('.qcow2' if developer else '.iso'))
    download(artifact,destination,experience=experience,report=report,progress=progress)
    if developer:
        Session.validate_disk(destination)
        manifest=developer_profile(artifact)
        manifest['image']['url']=destination.as_uri()
        path=root/'imported-images'/('hub-vm-'+artifact['sha256'][:24]+'.json')
        atomic_json(path,manifest)
        return path
    # Validate actual media before exposing a bootable session profile.
    from .experiences import validate_iso
    validate_iso(destination)
    manifest=dict(schema=1,id='hub-iso-'+artifact['sha256'][:24],target='desktop',
        arch=artifact['arch'],boot='uefi',cloud_init=False,media='iso',user='luma',
        build=artifact.get('display_name') or artifact['file'],
        image=dict(url=destination.as_uri(),bytes=artifact['size_bytes'],sha256=artifact['sha256']))
    path=root/'imported-images'/(manifest['id']+'.json');atomic_json(path,manifest)
    return path
