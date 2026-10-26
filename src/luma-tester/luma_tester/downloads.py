# SPDX-License-Identifier: Apache-2.0
"""Anonymous Hub attribution and resumable, checksum-verified file downloads.

The Hub catalog describes files, not bootable Tester session profiles. An ISO
in that catalog must never be substituted for a developer qcow2 manifest.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
from pathlib import Path
import platform
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener

from . import DOWNLOAD_SOURCE, __version__
from .core import HTTPSRedirects, TesterError
from .transfers import TransferReporter

HUB = 'https://hub.simplyluma.com/api/hub/downloads/'
EXPERIENCES = ('desktop', 'tablet', 'mobile', 'tv', 'home', 'watch', 'other')
PURPOSES = ('first_download', 'update', 'explicit_redownload')


class IntegrityError(TesterError):
    pass


def host_arch():
    machine = platform.machine().lower()
    return {'arm64': 'aarch64', 'amd64': 'x86_64'}.get(machine, machine)


def host_os():
    return {'Darwin': 'macos', 'Windows': 'windows', 'Linux': 'linux'}.get(platform.system())


def architecture_notice(architectures, native=None):
    native = native or host_arch()
    if native == 'aarch64' and native not in architectures:
        return 'An ARM64 image is coming. You can try x86_64 with emulation.'
    return ''


def validate_artifact(artifact):
    if not isinstance(artifact, dict):
        raise TesterError('Invalid download catalog entry.')
    url = urlparse(artifact.get('url', ''))
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or not re.fullmatch(r'[0-9a-f]{64}', str(artifact.get('sha256', '')))
            or type(artifact.get('size_bytes')) is not int
            or not 0 < artifact['size_bytes'] <= 64 * 1024**3):
        raise TesterError('Download requires HTTPS, an exact size and SHA-256.')
    if not isinstance(artifact.get('artifact_id'), str) or not artifact['artifact_id']:
        raise TesterError('Download artifact identity is missing.')
    return artifact


class HubClient:
    def __init__(self, source=DOWNLOAD_SOURCE):
        if source not in ('test', 'tester'):
            raise ValueError('Invalid Tester build attribution.')
        self.source = source

    def request(self, path, body=None):
        # Never use arbitrary report URLs supplied in responses, browser cookies,
        # application credentials, local paths or persistent machine identities.
        request = Request(HUB + path, data=None if body is None else json.dumps(body).encode(),
                          headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                                   'User-Agent': 'Luma-Tester/' + __version__})
        with build_opener(HTTPSRedirects).open(request, timeout=3) as response:
            if urlparse(response.url).netloc != urlparse(HUB).netloc:
                raise TesterError('Unexpected Hub response origin.')
            data = response.read(2 * 1024**2 + 1)
        if len(data) > 2 * 1024**2:
            raise TesterError('Hub response exceeds its limit.')
        return json.loads(data)

    def catalog(self, **filters):
        if set(filters) - {'channel', 'arch', 'kind'}:
            raise ValueError('Unknown catalog filter.')
        result = self.request('catalog?' + urlencode(filters))
        artifacts = [validate_artifact(a) for a in result['artifacts']]
        native = host_arch()
        # Stable ordering retains the Hub's newest-first order within each arch.
        return sorted(artifacts, key=lambda a: a.get('arch') != native)

    def best_effort(self, path, body):
        try:
            return self.request(path, body)
        except (OSError, ValueError, KeyError, TypeError, TesterError):
            return None

    def ticket(self, artifact, experience, purpose):
        if experience not in EXPERIENCES or purpose not in PURPOSES:
            raise ValueError('Invalid download experience or purpose.')
        body = dict(artifact_id=artifact['artifact_id'], source=self.source,
                    purpose=purpose, experience=experience, tester_version=__version__)
        if host_os():
            body['host_os'] = host_os()
        if host_arch() in ('x86_64', 'aarch64'):
            body['host_arch'] = host_arch()
        ticket = self.best_effort('tickets', body)
        if not isinstance(ticket, dict):
            return None
        identifier = ticket.get('download_id', '')
        # Pin to the selected artifact. A changed server entry is never silently
        # downloaded under a manifest's earlier build label or digest.
        if (not isinstance(identifier, str) or not re.fullmatch(r'dl_[A-Za-z0-9_-]{1,100}', identifier)
                or ticket.get('sha256') != artifact['sha256']
                or ticket.get('size_bytes') != artifact['size_bytes']
                or not isinstance(ticket.get('artifact'), dict)
                or ticket['artifact'].get('artifact_id') != artifact['artifact_id']):
            return None
        try:
            validate_artifact(dict(artifact, url=ticket['url']))
        except (KeyError, TypeError, TesterError):
            return None
        return ticket

    def complete(self, ticket, sha256, size):
        return self.best_effort('tickets/' + ticket['download_id'] + '/complete',
                                {'sha256': sha256, 'size_bytes': size})

    def fail(self, ticket, reason, count):
        return self.best_effort('tickets/' + ticket['download_id'] + '/fail',
                                {'reason': reason, 'bytes_received': count})


@contextlib.contextmanager
def file_lock(path):
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.write(b'\0'); stream.flush(); stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def download(artifact, destination, *, experience='desktop', purpose='first_download',
             client=None, report=lambda message: None, progress=None):
    """Return a verified file; leave partial bytes and the ticket on interruption.

    Catalog and ticket metadata must agree. Reporting is best effort; artifact
    verification is mandatory. Callers validate the file format before booting.
    """
    validate_artifact(artifact)
    destination = Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with file_lock(destination.with_name(destination.name + '.lock')):
        return _download(artifact, destination, experience, purpose, client or HubClient(), report, progress)


def _download(artifact, destination, experience, purpose, client, report, progress=None):
    size, sha = artifact['size_bytes'], artifact['sha256']
    transfer = TransferReporter(size, report, progress)
    if destination.exists():
        if destination.stat().st_size != size or transfer.digest(destination) != sha:
            raise TesterError('Cached download does not match its size or SHA-256.')
        transfer.update('ready', size, force=True)
        return destination  # Cached launches issue no ticket and send no reports.
    transfer.update('connecting', 0, force=True)
    partial = destination.with_name(destination.name + '.part')
    metadata = destination.with_name(destination.name + '.download.json')
    identity = {key: artifact[key] for key in ('artifact_id', 'sha256', 'size_bytes')}
    try:
        saved = json.loads(metadata.read_text())
        if saved['identity'] != identity or saved.get('source') != client.source:
            raise ValueError('Different download')
    except (OSError, ValueError, KeyError, TypeError):
        partial.unlink(missing_ok=True)
        saved = {'identity': identity, 'source': client.source,
                 'ticket': client.ticket(artifact, experience, purpose)}
        temporary = metadata.with_name(metadata.name + '.new')
        temporary.write_text(json.dumps(saved) + '\n'); temporary.chmod(0o600)
        temporary.replace(metadata)
    ticket = saved.get('ticket')
    count = partial.stat().st_size if partial.exists() else 0
    try:
        if count > size:
            raise IntegrityError('Download exceeds declared size.')
        if count < size:
            headers = {'Range': 'bytes=' + str(count) + '-'} if count else {}
            headers['User-Agent'] = 'Luma-Tester/' + __version__
            request = Request(ticket['url'] if ticket else artifact['url'], headers=headers)
            transfer.update('connecting', count, force=True)
            with build_opener(HTTPSRedirects).open(request, timeout=30) as source:
                if urlparse(source.url).scheme != 'https':
                    raise TesterError('Refusing a download redirected away from HTTPS.')
                status = source.getcode()
                if count and status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', source.headers.get('Content-Range', ''))
                    if not match or int(match[1]) != count or int(match[3]) != size:
                        raise TesterError('Invalid resumed download range.')
                elif status == 200:
                    count = 0  # Server ignored Range: restart, keep the same ticket.
                else:
                    raise TesterError('Unexpected download response.')
                transfer.update('downloading', count, force=True)
                read = getattr(source, 'read1', source.read)
                with partial.open('ab' if count else 'wb') as output:
                    while chunk := read(64 * 1024):
                        count += len(chunk)
                        if count > size:
                            raise IntegrityError('Download exceeds declared size.')
                        output.write(chunk)
                        transfer.update('downloading', count)
                    transfer.update('downloading', count, force=True)
                    output.flush()
                    os.fsync(output.fileno())
        if count != size:
            raise URLError('Incomplete download; retry to resume.')
        if transfer.digest(partial) != sha:
            raise IntegrityError('Download SHA-256 mismatch. Retry to download again.')
        partial.replace(destination)
        transfer.update('ready', size, force=True)
    except BaseException as exc:
        reason = 'other'
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            reason = 'cancelled'
        elif isinstance(exc, HTTPError):
            reason = 'server_error' if exc.code >= 500 else 'network'
        elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            reason = 'disk_full'
        elif isinstance(exc, (URLError, ConnectionError, TimeoutError)):
            reason = 'network'
        elif isinstance(exc, IntegrityError):
            reason = 'checksum_mismatch'
        if ticket:
            client.fail(ticket, reason, count)
        if reason == 'checksum_mismatch':
            partial.unlink(missing_ok=True)
        raise
    if ticket:
        client.complete(ticket, sha, size)
    metadata.unlink(missing_ok=True)
    return destination
