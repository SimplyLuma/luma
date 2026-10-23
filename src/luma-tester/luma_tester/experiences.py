# SPDX-License-Identifier: Apache-2.0
"""Requested interfaces share OS images; architecture remains image metadata."""
from pathlib import Path
from .core import TesterError, digest

CHOICES = ('desktop', 'mobile', 'tablet', 'tv', 'smart-device')
ARM_FIRST = ('mobile', 'tv', 'smart-device')
COMING_SOON = ('tv', 'smart-device')
LABELS = {'desktop':'Desktop','mobile':'Mobile','tablet':'Tablet','tv':'TV','smart-device':'Smart Home'}
FLAGS = {'smart-device':'home'}


def compatible(experience, image):
    return experience in CHOICES and image['target'] == ('mobile' if experience == 'mobile' else 'desktop')


def preferred_arch(experience, host):
    return 'aarch64' if experience in ARM_FIRST else host


def architecture_preferences(preferences, host):
    """Migrate previews that saved defaults as if the user had chosen them.

    Only choices carrying explicit provenance override the current defaults.
    Image and preset selections remain available independently of this policy.
    """
    result = {key: preferred_arch(key, host) for key in CHOICES}
    saved = preferences.get('architectures', {})
    explicit = preferences.get('architecture_choices', [])
    if not isinstance(saved, dict) or not isinstance(explicit, list):
        return result, set()
    choices = {key for key in explicit if key in CHOICES
               and saved.get(key) in ('auto', 'x86_64', 'aarch64')}
    result.update({key: saved[key] for key in choices})
    return result, choices


def select(experience, images, host, architecture='auto'):
    choices = [p for p in images if compatible(experience, p[1])]
    if architecture not in ('auto', 'x86_64', 'aarch64'):
        raise TesterError('Choose Automatic, x86-64 or ARM64.')
    if architecture != 'auto':
        return next((path for path, image in choices if image['arch'] == architecture), None)
    preferred = preferred_arch(experience, host)
    # A TV/home choice must not silently become an x86 machine on an x86 host.
    if experience in ('tv','smart-device'):
        choices = [p for p in choices if p[1]['arch'] == preferred]
    choices.sort(key=lambda p:p[1]['arch'] != preferred)
    return choices[0][0] if choices else None


def import_iso(path, root, arch):
    """Describe local media without changing it or claiming a ready developer OS."""
    path=Path(path).resolve()
    if arch not in ('aarch64','x86_64'):
        raise TesterError('Choose ARM64 or x86-64 for this ISO.')
    validate_iso(path)
    sha=digest(path)
    manifest=dict(schema=1,id='local-iso-'+sha[:24]+'-'+arch.replace('_','-'),target='desktop',
        arch=arch,boot='uefi',cloud_init=False,media='iso',user='luma',build=path.name,
        image=dict(url=path.as_uri(),sha256=sha,bytes=path.stat().st_size))
    directory=Path(root)/'imported-images';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    from .vm import atomic_json
    output=directory/(manifest['id']+'.json');atomic_json(output,manifest)
    return output


def validate_iso(path):
    with Path(path).open('rb') as stream:
        for sector in range(16,32):
            stream.seek(sector*2048);header=stream.read(7)
            if header==b'\x01CD001\x01':return
            if len(header)<7 or header[0]==255:break
    raise TesterError('Choose a bootable ISO9660 installation image.')
