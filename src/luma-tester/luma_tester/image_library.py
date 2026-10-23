# SPDX-License-Identifier: Apache-2.0
"""Published installation media, separate from qualified developer workspaces."""
import json
from pathlib import Path
from .core import TesterError
from .downloads import HubClient, download, validate_artifact
from .vm import atomic_json


def usable(artifact):
    validate_artifact(artifact)
    return artifact.get('kind')=='os_image' and artifact.get('arch') in ('x86_64','aarch64') and str(artifact.get('file','')).lower().endswith('.iso')


def cached(root):
    try:
        items=json.loads((Path(root)/'download-catalog.json').read_text())
        return [item for item in items if usable(item)]
    except (OSError,ValueError,TypeError,TesterError):return []


def refresh(root,client=None):
    items=[item for item in (client or HubClient()).catalog(kind='os_image') if usable(item)]
    atomic_json(Path(root)/'download-catalog.json',items)
    return items


def fetch(artifact,root,experience,report=lambda text:None):
    if not usable(artifact):raise TesterError('This download is not supported installation media.')
    root=Path(root)
    target=root/'images'/(artifact['sha256']+'.iso')
    download(artifact,target,experience=experience,report=report)
    # Validate actual media before exposing a bootable session profile.
    from .experiences import validate_iso
    validate_iso(target)
    manifest=dict(schema=1,id='hub-iso-'+artifact['sha256'][:24],target='desktop',
        arch=artifact['arch'],boot='uefi',cloud_init=False,media='iso',user='luma',
        build=artifact.get('display_name') or artifact['file'],
        image=dict(url=target.as_uri(),bytes=artifact['size_bytes'],sha256=artifact['sha256']))
    path=root/'imported-images'/(manifest['id']+'.json');atomic_json(path,manifest)
    return path
