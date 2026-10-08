# SPDX-License-Identifier: Apache-2.0
"""An app from its publisher's signed APT repository, in a Debian capsule.

Some publishers ship Linux apps only for Debian and Ubuntu, from a signed APT
repository (Claude desktop). Luma already runs a Debian package inside a
Debian capsule (Valet, ``backends._install_deb``); this module is the part
that makes it one click and verifiable, the way ``apt`` itself would be:

1. The repository's signing key is downloaded over HTTPS and accepted only
   when every primary key in it has a fingerprint the signed catalogue pins.
2. ``dists/<suite>/InRelease`` must carry a good signature by that key and a
   ``Valid-Until`` in the future; the ``SHA256`` it lists for
   ``<component>/binary-<arch>/Packages`` is what the downloaded index must hash to.
3. The newest version of the pinned package in that index names the ``.deb``,
   its size and SHA-256; the downloaded file must match both.
4. The verified file goes through Valet's own inspection and capsule install,
   so the result is an ordinary Valet application record Depot can open,
   update and remove.

Everything runs as the person, needs no administrator, and changes nothing
outside their home directory and container storage. A failure at any step
installs nothing.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

GPG = '/usr/bin/gpg'
KEY_LIMIT = 256 * 1024
INDEX_LIMIT = 32 * 1024 * 1024
PACKAGE_LIMIT = 2 * 1024 ** 3
ARCHITECTURES = {'x86_64': 'amd64', 'aarch64': 'arm64'}
USER_AGENT = 'Luma-Depot/4'


class RepositoryError(RuntimeError):
    """The repository could not be read or did not verify; nothing was installed."""


def download_directory(environment=None) -> Path:
    env = os.environ if environment is None else environment
    base = env.get('XDG_CACHE_HOME') or os.path.join(env.get('HOME', str(Path.home())), '.cache')
    return Path(base) / 'luma/depot/downloads'


def fetch(url: str, limit: int, *, opener=urllib.request.urlopen, progress=None) -> bytes:
    if not url.startswith('https://'):
        raise RepositoryError('Repository files are only fetched over HTTPS.')
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with opener(request, timeout=60) as response:
            total = int(response.headers.get('Content-Length') or 0) if hasattr(response, 'headers') else 0
            chunks, received = [], 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > limit:
                    raise RepositoryError('A repository file is larger than expected.')
                chunks.append(chunk)
                if progress is not None:
                    progress(received, total)
    except (OSError, ValueError) as error:
        raise RepositoryError(f'Could not download {url}: {error}') from error
    return b''.join(chunks)


# ── Signatures ───────────────────────────────────────────────────────────

def _gpg(home: str, arguments, *, data: bytes | None = None, gpg=GPG):
    return subprocess.run([gpg, '--batch', '--no-tty', '--homedir', home, *arguments], input=data,
                          capture_output=True, timeout=120, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})


def verify_release(inrelease: bytes, key: bytes, fingerprints, *, gpg=GPG) -> str:
    """The signed text of ``InRelease`` when a pinned key made a good signature."""
    pinned = {value.upper() for value in fingerprints}
    with tempfile.TemporaryDirectory(prefix='luma-depot-apt-') as home:
        os.chmod(home, 0o700)
        if _gpg(home, ['--import'], data=key, gpg=gpg).returncode != 0:
            raise RepositoryError('The publisher’s signing key could not be read.')
        listed = _gpg(home, ['--with-colons', '--fingerprint', '--list-keys'], gpg=gpg).stdout.decode()
        primaries, expect = [], False
        for line in listed.splitlines():
            fields = line.split(':')
            if fields[0] == 'pub':
                expect = True
            elif fields[0] == 'fpr' and expect:
                primaries.append(fields[9].upper())
                expect = False
            elif fields[0] == 'sub':
                expect = False
        if not primaries or any(value not in pinned for value in primaries):
            raise RepositoryError('The publisher’s signing key is not the one Luma has on record.')
        result = _gpg(home, ['--status-fd', '2', '--decrypt'], data=inrelease, gpg=gpg)
        status = result.stderr.decode(errors='replace')
        valid = [line.split() for line in status.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
        # VALIDSIG <signing-key fpr> ... <primary key fpr> is the last field.
        if result.returncode != 0 or not valid or not any(fields[-1].upper() in pinned for fields in valid):
            raise RepositoryError('The repository’s index is not signed by the publisher’s key.')
        return result.stdout.decode('utf-8', errors='strict')


# ── Indexes ──────────────────────────────────────────────────────────────

def release_fields(text: str) -> dict:
    """Top-level fields of a Release file, and its SHA256 list as {path: (hash, size)}."""
    fields, hashes, section = {}, {}, None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith(' '):
            if section == 'SHA256':
                parts = line.split()
                if len(parts) == 3 and re.fullmatch(r'[0-9a-f]{64}', parts[0]):
                    hashes[parts[2]] = (parts[0], int(parts[1]))
            continue
        name, _, value = line.partition(':')
        section = name
        fields[name] = value.strip()
    fields['SHA256'] = hashes
    return fields


def check_valid_until(fields: dict, *, now=None) -> None:
    value = fields.get('Valid-Until')
    if not value:
        return
    try:
        moment = dt.datetime.strptime(value.replace('UTC', '+0000'), '%a, %d %b %Y %H:%M:%S %z')
    except ValueError as error:
        raise RepositoryError('The repository’s index carries an unreadable expiry.') from error
    if (now or dt.datetime.now(dt.timezone.utc)) > moment:
        raise RepositoryError('The repository’s index has expired; the publisher has not refreshed it.')


def stanzas(text: str):
    current = {}
    key = None
    for line in text.splitlines():
        if not line.strip():
            if current:
                yield current
            current, key = {}, None
            continue
        if line.startswith((' ', '\t')) and key:
            current[key] += '\n' + line.strip()
            continue
        key, _, value = line.partition(':')
        current[key] = value.strip()
    if current:
        yield current


def _version_key(version: str):
    """Debian version order, enough for publishers' dotted numeric versions."""
    epoch, _, rest = version.rpartition(':')
    upstream, _, revision = rest.partition('-')
    def split(part):
        return tuple((0, int(chunk)) if chunk.isdigit() else (1, chunk)
                     for chunk in re.findall(r'\d+|[A-Za-z]+|~', part))
    return (int(epoch or 0), split(upstream), split(revision))


def newest(packages_text: str, package: str, architecture: str) -> dict:
    found = [entry for entry in stanzas(packages_text)
             if entry.get('Package') == package and entry.get('Architecture') in (architecture, 'all')]
    if not found:
        raise RepositoryError(f'The publisher’s repository has no {package} for this computer.')
    return max(found, key=lambda entry: _version_key(entry.get('Version', '0')))


# ── The whole check ──────────────────────────────────────────────────────

class Resolved:
    def __init__(self, url: str, version: str, size: int, sha256: str) -> None:
        self.url, self.version, self.size, self.sha256 = url, version, size, sha256


def resolve(source, machine: str, *, fetcher=fetch, gpg=GPG) -> Resolved:
    """The newest verified package the publisher's repository offers here."""
    architecture = ARCHITECTURES.get(machine)
    if architecture is None:
        raise RepositoryError('The publisher does not publish it for this computer’s processor.')
    base = source.url.rstrip('/')
    key = fetcher(source.key, KEY_LIMIT)
    text = verify_release(fetcher(f'{base}/dists/{source.suite}/InRelease', INDEX_LIMIT), key,
                          source.fingerprints, gpg=gpg)
    fields = release_fields(text)
    check_valid_until(fields)
    hashes = fields['SHA256']
    for name, decode in ((f'{source.component}/binary-{architecture}/Packages', lambda data: data),
                         (f'{source.component}/binary-{architecture}/Packages.gz', gzip.decompress)):
        if name in hashes:
            expected, size = hashes[name]
            data = fetcher(f'{base}/dists/{source.suite}/{name}', INDEX_LIMIT)
            if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
                raise RepositoryError('The repository’s package list does not match its signed index.')
            packages = decode(data).decode('utf-8', errors='replace')
            break
    else:
        raise RepositoryError('The repository’s signed index does not list its packages.')
    entry = newest(packages, source.package, architecture)
    filename = entry.get('Filename', '')
    if not filename or '..' in filename.split('/') or filename.startswith('/'):
        raise RepositoryError('The repository names an unexpected file.')
    sha256 = entry.get('SHA256', '').lower()
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise RepositoryError('The repository gives no SHA-256 for the package.')
    return Resolved(f'{base}/{filename}', entry.get('Version', ''), int(entry.get('Size') or 0), sha256)


def download(resolved: Resolved, *, directory=None, fetcher=fetch, progress=None) -> Path:
    """The package file, verified against the signed index, in the download cache."""
    directory = Path(directory or download_directory())
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{resolved.sha256}.deb'
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == resolved.sha256:
        return target
    if resolved.size and shutil.disk_usage(directory).free < resolved.size * 2:
        raise RepositoryError('There is not enough space to download it (no space left).')
    data = fetcher(resolved.url, min(PACKAGE_LIMIT, (resolved.size or PACKAGE_LIMIT) + 1), progress=progress)
    if (resolved.size and len(data) != resolved.size) or hashlib.sha256(data).hexdigest() != resolved.sha256:
        raise RepositoryError('The downloaded package does not match the publisher’s signed index.')
    handle, temporary = tempfile.mkstemp(dir=str(directory), prefix='.download')
    with os.fdopen(handle, 'wb') as stream:
        stream.write(data)
    os.replace(temporary, target)
    return target


def installed_records(package: str, *, records=None):
    """Valet's records of this Debian package, newest last."""
    if records is None:
        from .desktop import iter_records
        records = iter_records()
    found = [record for record in records if record.get('format') == 'deb' and record.get('package') == package]
    return sorted(found, key=lambda record: _version_key(str(record.get('version') or '0')))


def carry_data(old: dict, new: dict, *, home=None) -> None:
    """Move an app's capsule home from its previous build to the new one.

    A capsule's home is keyed by the package it was installed from, so an
    update would otherwise start signed out and empty.
    """
    base = Path(home or Path.home()) / '.local/share/luma/installer/data'
    source, target = base / str(old.get('sha256', '')), base / str(new.get('sha256', ''))
    if source == target or not source.is_dir() or not re.fullmatch(r'[0-9a-f]{64}', str(new.get('sha256', ''))):
        return
    if target.exists():
        if any(target.iterdir()):
            return
        target.rmdir()
    os.replace(source, target)
