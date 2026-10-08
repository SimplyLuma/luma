# SPDX-License-Identifier: Apache-2.0
"""Icons and screenshots named by the signed catalogue, kept by their digest.

The catalogue carries a SHA-256 for every image. An image is drawn only after
the downloaded bytes match that digest, so a CDN or a network in between cannot
substitute a picture for the one the listing was reviewed with. Files are kept
under their digest, which makes the cache content-addressed: a changed image
is a new file and an unchanged one is never fetched twice.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_IMAGE_BYTES = 16 * 1024 * 1024
TIMEOUT = 20
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_TYPES = {b'\x89PNG\r\n\x1a\n': '.png', b'\xff\xd8\xff': '.jpg', b'RIFF': '.webp'}


class MediaError(ValueError):
    pass


def media_directory(environment=None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get('XDG_CACHE_HOME') or os.path.join(env.get('HOME', str(Path.home())), '.cache')
    return Path(base) / 'luma/depot/media'


def cached(sha256: str, directory: Path | None = None) -> Path | None:
    if not _SHA256.fullmatch(sha256 or ''):
        return None
    directory = directory or media_directory()
    for suffix in ('.png', '.jpg', '.webp', '.svg', ''):
        path = directory / (sha256 + suffix)
        if path.is_file():
            return path
    return None


def _suffix(content: bytes) -> str:
    for magic, suffix in _TYPES.items():
        if content.startswith(magic):
            if suffix == '.webp' and content[8:12] != b'WEBP':
                continue
            return suffix
    head = content[:256].lstrip().lower()
    if head.startswith(b'<svg') or head.startswith(b'<?xml'):
        return '.svg'
    return ''


def fetch(url: str, sha256: str, *, directory: Path | None = None,
          opener=urllib.request.urlopen, timeout=TIMEOUT) -> Path:
    """The local file for an image, downloading and verifying it if needed."""
    if not _SHA256.fullmatch(sha256 or ''):
        raise MediaError('The image has no digest to check against.')
    directory = directory or media_directory()
    existing = cached(sha256, directory)
    if existing is not None:
        return existing
    address = urlsplit(url or '')
    if address.scheme != 'https' or not address.hostname or address.username or address.password:
        raise MediaError('Images are only fetched over HTTPS.')
    request = urllib.request.Request(url, headers={'User-Agent': 'Luma-Depot/4'})
    try:
        with opener(request, timeout=timeout) as response:
            content = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise MediaError('The image could not be downloaded.') from error
    if len(content) > MAX_IMAGE_BYTES:
        raise MediaError('The image is too large.')
    if hashlib.sha256(content).hexdigest() != sha256:
        raise MediaError('The image does not match the catalogue.')
    suffix = _suffix(content)
    if not suffix:
        raise MediaError('The image is not in a format Depot draws.')
    directory.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(directory), prefix='.media')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(content)
        target = directory / (sha256 + suffix)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target
