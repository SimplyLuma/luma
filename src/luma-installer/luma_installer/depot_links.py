# SPDX-License-Identifier: Apache-2.0
"""``luma-depot://`` and ``appstream://`` links (ADR-028, section 11).

A link names an application or a collection. It never carries an instruction
Depot follows without a person: ``luma-depot://install/<app_id>`` opens the
application's page with the install confirmation ready, and the person still
presses Install.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import unquote, urlsplit

SCHEMES = ('luma-depot', 'appstream')
ACTIONS = ('app', 'install', 'collection')
MAX_LENGTH = 512

_APPLICATION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z')
_COLLECTION = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')


@dataclass(frozen=True)
class Link:
    action: str   # app | install | collection
    target: str


def parse(uri: str) -> Link | None:
    """Parse a link, or return None for anything that is not a well-formed one."""
    if not isinstance(uri, str) or len(uri) > MAX_LENGTH or any(c.isspace() for c in uri.strip()):
        return None
    uri = uri.strip()
    scheme, separator, rest = uri.partition(':')
    if not separator or scheme.lower() not in SCHEMES:
        return None
    scheme = scheme.lower()
    parts = urlsplit(uri)
    if scheme == 'appstream':
        # GNOME Software accepts both appstream://org.example.App and
        # appstream:org.example.App; so do the apps that emit them.
        target = rest[2:] if rest.startswith('//') else rest
        target = unquote(target.split('?', 1)[0].split('#', 1)[0]).strip('/')
        if target.endswith('.desktop'):
            target = target[:-len('.desktop')]
        if '/' in target or not _APPLICATION.fullmatch(target) or '..' in target:
            return None
        return Link('app', target)

    if not rest.startswith('//'):
        return None
    action = parts.netloc.lower()
    segments = [unquote(segment) for segment in parts.path.split('/') if segment]
    if not action and segments:
        # luma-depot:///app/<id> reaches here with an empty authority.
        action, segments = segments[0].lower(), segments[1:]
    if action not in ACTIONS or len(segments) != 1:
        return None
    target = segments[0]
    if target.endswith('.desktop') and action != 'collection':
        target = target[:-len('.desktop')]
    pattern = _COLLECTION if action == 'collection' else _APPLICATION
    if not pattern.fullmatch(target) or '..' in target:
        return None
    return Link(action, target)


def uri_for(action: str, target: str) -> str:
    if action not in ACTIONS:
        raise ValueError(action)
    return f'luma-depot://{action}/{target}'
