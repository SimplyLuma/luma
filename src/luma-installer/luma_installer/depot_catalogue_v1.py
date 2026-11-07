# SPDX-License-Identifier: Apache-2.0
"""Third-party apps from the signed Depot catalogue v1.

The Hub publishes every Depot listing as one document, signed by the review
pipeline with the minisign key ``depot-catalogue-v1``. This module reads the
third-party Flatpak apps from it, so they install, update and uninstall from
the Luma remote like Luma's own apps.

The document is verified against the key shipped in this package before it is
parsed, and the last copy that verified is kept and verified again on every
read. A published copy older than the kept one is refused. Nothing here ever
raises to a caller: a missing, unreachable or invalid catalogue means no
third-party apps, and first-party sources work exactly as before.

A third-party app can never take a Luma app's place: reserved id prefixes,
the maintained first-party roles, the catalogue's own first-party listings
and every id an existing source already lists are refused.

Listing data is display and routing only. libflatpak still proves the remote,
the ref and its signature, and depot_flatpak.verify_installed checks the
deployed commit against Luma's signing key.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request

from .depot_signature import SIGNATURE_MAX_BYTES, SignatureError, verify_file

DATA_DIRECTORY = Path(os.environ.get('LUMA_DEPOT_DATA_DIRECTORY', '/usr/share/luma/installer'))
PUBLIC_KEY_PATH = Path(os.environ.get('LUMA_DEPOT_CATALOGUE_V1_KEY',
                                      str(DATA_DIRECTORY / 'depot-catalogue-v1.pub')))
URL = os.environ.get('LUMA_DEPOT_CATALOGUE_V1_URL',
                     'https://dl.simplyluma.com/depot/catalogue/v1/catalogue.json')
TIMEOUT = 10
MAX_BYTES = 8 * 1024 * 1024
MAX_APPS = 5000
FRESH_SECONDS = 15 * 60
RETRY_SECONDS = 2 * 60
#: The signer's trusted comment names the document type and its digest.
COMMENT = re.compile(r'depot-catalogue v1 sha256=([0-9a-f]{64})(?: .*)?\Z')

ARCHES = ('x86_64', 'aarch64')
BRANCH = 'stable'
REMOTE = 'luma'
#: Ids that only Luma publishes (the pipeline and the portal refuse them too).
RESERVED_PREFIXES = ('org.projectluma.', 'io.luma.', 'com.simplyluma.',
                     'org.simplyluma.', 'com.projectluma.')

_APP_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*){2,}\Z')
_COMMIT = re.compile(r'[0-9a-f]{64}\Z')
_WORD = re.compile(r'[A-Za-z][A-Za-z0-9 _-]{0,31}\Z')
_DEVELOPER = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')

_last_failure = 0.0


class CatalogueError(ValueError):
    """The catalogue cannot be trusted or read."""


@dataclass(frozen=True)
class ThirdPartyApp:
    app_id: str
    name: str
    summary: str
    developer_id: str
    developer_name: str
    verified: bool
    arches: tuple[str, ...]
    #: ((word, reason), ...) as the developer declared them.
    permissions: tuple[tuple[str, str], ...]
    version: str
    #: The live release's commit per architecture, as the pipeline published it.
    commits: dict

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(f'app/{self.app_id}/{arch}/{BRANCH}' for arch in self.arches)


@dataclass(frozen=True)
class CatalogueV1:
    generated: str
    apps: tuple[ThirdPartyApp, ...]
    #: Ids the catalogue itself marks as Luma's: never third-party.
    first_party: frozenset


# ── Verification ─────────────────────────────────────────────────────────

def _key(path=None) -> bytes:
    try:
        with Path(path or PUBLIC_KEY_PATH).open('rb') as stream:
            return stream.read(1025)
    except OSError as error:
        raise CatalogueError('The catalogue key is not installed') from error


def verified(content: bytes, signature: bytes, key: bytes) -> CatalogueV1:
    """Verify, then parse. Nothing is parsed before the signature holds."""
    if len(content) > MAX_BYTES:
        raise CatalogueError('The catalogue is too large')
    try:
        comment = verify_file(key, content, signature)
    except SignatureError as error:
        raise CatalogueError(str(error)) from error
    found = COMMENT.fullmatch(comment)
    if found is None or found.group(1) != hashlib.sha256(content).hexdigest():
        raise CatalogueError('The signature is not for this catalogue')
    try:
        document = json.loads(content.decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise CatalogueError('Cannot read the catalogue') from error
    return parse(document)


def _moment(text):
    try:
        value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (AttributeError, ValueError) as error:
        raise CatalogueError('Invalid generation time') from error
    if value.tzinfo is None:
        raise CatalogueError('Invalid generation time')
    return value


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError()
    return value.strip()


def reserved(app_id: str) -> bool:
    folded = app_id.casefold()
    return any(folded.startswith(prefix) for prefix in RESERVED_PREFIXES)


def _first_party_row(row) -> bool:
    developer = row.get('developer')
    return (row.get('first_party') is not False
            or (isinstance(developer, dict) and developer.get('id') == 'luma'))


def _app(row) -> ThirdPartyApp | None:
    """One third-party Flatpak app, or None for anything else or malformed."""
    if row.get('kind') != 'app' or row.get('format') != 'flatpak' or _first_party_row(row):
        return None
    try:
        app_id = _text(row.get('id'), 255)
        if not _APP_ID.fullmatch(app_id) or reserved(app_id):
            return None
        developer = row.get('developer')
        if not isinstance(developer, dict) or not _DEVELOPER.fullmatch(str(developer.get('id', ''))):
            return None
        live = [release for release in row.get('releases') or ()
                if isinstance(release, dict) and release.get('status') == 'live']
        if len(live) != 1 or not isinstance(live[0].get('arches'), dict):
            return None  # withdrawn, end-of-life or ambiguous: nothing to install
        commits = {arch: commit for arch, commit in live[0]['arches'].items()
                   if arch in ARCHES and isinstance(commit, str) and _COMMIT.fullmatch(commit)}
        listed = row.get('arches')
        if not isinstance(listed, list):
            return None
        arches = tuple(arch for arch in ARCHES if arch in listed and arch in commits)
        if not arches:
            return None
        permissions = []
        for grant in row.get('permissions') or ():
            if not isinstance(grant, dict):
                return None
            word = _text(grant.get('word'), 32)
            if not _WORD.fullmatch(word):
                return None
            permissions.append((word, _text(grant.get('reason', ''), 512)))
        if len(permissions) > 64:
            return None
        return ThirdPartyApp(
            app_id=app_id, name=_text(row.get('name'), 128) or app_id,
            summary=_text(row.get('summary') or '', 512),
            developer_id=developer['id'], developer_name=_text(developer.get('name') or '', 128),
            verified=developer.get('verified') is True, arches=arches,
            permissions=tuple(permissions),
            version=_text(live[0].get('version') or row.get('version') or '', 64),
            commits={arch: commits[arch] for arch in arches})
    except ValueError:
        return None


def parse(document) -> CatalogueV1:
    if not isinstance(document, dict) or document.get('schema') != 1:
        raise CatalogueError('Not a catalogue v1 document')
    generated = document.get('generated')
    _moment(generated)
    rows = document.get('apps')
    if not isinstance(rows, list) or len(rows) > MAX_APPS:
        raise CatalogueError('Invalid app list')
    apps, seen, first_party = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str):
            continue
        if _first_party_row(row):
            first_party.add(row['id'].casefold())
            continue
        app = _app(row)
        if app is None:
            continue
        folded = app.app_id.casefold()
        if folded in seen:
            # Two listings claiming one id: neither is trustworthy.
            apps = [other for other in apps if other.app_id.casefold() != folded]
            continue
        seen.add(folded)
        apps.append(app)
    apps = [app for app in apps if app.app_id.casefold() not in first_party]
    return CatalogueV1(generated, tuple(apps), frozenset(first_party))


# ── Fetching and the kept copy ───────────────────────────────────────────

def cache_path() -> Path:
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return Path(base) / 'luma/depot/catalogue-v1.json'


def _signature_path(path: Path) -> Path:
    return path.with_name(path.name + '.minisig')


def _read(path: Path, limit: int) -> bytes:
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise CatalogueError('Kept catalogue is too large')
    return content


def load_cached(cache=None, *, key=None) -> CatalogueV1:
    """The last catalogue verified here, verified again."""
    path = Path(cache) if cache is not None else cache_path()
    try:
        content = _read(path, MAX_BYTES)
        signature = _read(_signature_path(path), SIGNATURE_MAX_BYTES)
    except OSError as error:
        raise CatalogueError('No catalogue is kept') from error
    return verified(content, signature, _key(key))


def _get(url, limit, timeout):
    request = urllib.request.Request(url, headers={'User-Agent': 'Luma-Depot/4'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as stream:
            content = stream.read(limit + 1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise CatalogueError('Cannot reach the catalogue') from error
    if len(content) > limit:
        raise CatalogueError('The catalogue is too large')
    return content


def _keep(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.catalogue')
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(content)
            os.replace(temporary, path)
        except BaseException:
            os.unlink(temporary)
            raise
    except OSError:
        pass


def fetch(url=None, *, cache=None, key=None, timeout=TIMEOUT) -> CatalogueV1:
    """Download, verify and keep the published catalogue."""
    url = url or URL
    path = Path(cache) if cache is not None else cache_path()
    key_data = _key(key)
    content = _get(url, MAX_BYTES, timeout)
    signature = _get(url + '.minisig', SIGNATURE_MAX_BYTES, timeout)
    catalogue = verified(content, signature, key_data)
    try:
        kept = load_cached(path, key=key)
    except CatalogueError:
        kept = None
    if kept is not None and _moment(kept.generated) > _moment(catalogue.generated):
        raise CatalogueError('The published catalogue is older than the kept one')
    # Signature first: a reader never pairs new content with an old signature
    # that verifies (it cannot), and a torn pair just reads as no catalogue.
    _keep(_signature_path(path), signature)
    _keep(path, content)
    return catalogue


def current(*, refresh=False, offline=False, url=None, cache=None, key=None) -> CatalogueV1 | None:
    """The newest catalogue this device can vouch for, or None.

    ``offline`` reads only the kept copy. Otherwise the network is read at most
    every FRESH_SECONDS (``refresh`` forces it), and a failure falls back to
    the kept copy.
    """
    global _last_failure
    path = Path(cache) if cache is not None else cache_path()
    if not offline and not refresh:
        try:
            if time.time() - path.stat().st_mtime < FRESH_SECONDS:
                offline = True
        except OSError:
            pass
        if _last_failure and time.monotonic() - _last_failure < RETRY_SECONDS:
            offline = True
    if not offline:
        try:
            return fetch(url, cache=path, key=key)
        except CatalogueError:
            _last_failure = time.monotonic()
    try:
        return load_cached(path, key=key)
    except CatalogueError:
        return None


# ── Merging with the first-party sources ─────────────────────────────────

def _maintained_ids() -> set:
    from .native_app_roles import APPS
    return {app_id.casefold() for app_id in APPS}


def admitted(catalogue: CatalogueV1 | None, taken=()) -> tuple[ThirdPartyApp, ...]:
    """Third-party apps that shadow nothing: not reserved, not Luma's, not listed already."""
    if catalogue is None:
        return ()
    blocked = _maintained_ids() | set(catalogue.first_party) | {value.casefold() for value in taken if value}
    return tuple(app for app in catalogue.apps
                 if not reserved(app.app_id) and app.app_id.casefold() not in blocked)


def third_party_apps(*, refresh=False, offline=False, taken=()) -> tuple[ThirdPartyApp, ...]:
    """Never raises: () when there is no trustworthy catalogue."""
    try:
        return admitted(current(refresh=refresh, offline=offline), taken)
    except Exception:
        return ()


def is_third_party(app_id: str, *, offline=True) -> bool:
    return any(app.app_id == app_id for app in third_party_apps(offline=offline))


def entry(app: ThirdPartyApp):
    """A depot_catalog.CatalogEntry for one app, installing from the Luma remote."""
    from .depot_catalog import CatalogEntry, Developer, FlatpakSource, ReleaseInfo
    return CatalogEntry(
        id=app.app_id, name=app.name, backend='flatpak', source_id=app.app_id,
        repository=REMOTE, distribution='publisher', architectures=app.arches,
        reference_url='', qualification='admitted',
        qualification_reason='Reviewed by Luma and delivered through Depot from Luma’s signed app source.',
        publisher=app.developer_name, summary=app.summary, app_id=app.app_id,
        tier='verified', visibility='public',
        developer=Developer(app.developer_id, app.developer_name or app.developer_id),
        branch=BRANCH, release=ReleaseInfo(app.version) if app.version else None,
        flatpak=FlatpakSource(REMOTE, app.app_id, BRANCH), sandbox='flatpak')


def merge(catalog, *, refresh=False, offline=True):
    """``catalog`` plus the third-party apps that shadow none of its entries.

    Existing entries always win: a v1 app whose id any entry already uses (as
    its id, app id or Flatpak source) is left out.
    """
    from dataclasses import replace
    taken = set()
    for existing in catalog.applications:
        taken.update((existing.id, existing.app_id, existing.source_id))
        if existing.luma_system is not None:
            taken.add(existing.luma_system.desktop_id.removesuffix('.desktop'))
    extra = tuple(entry(app) for app in third_party_apps(refresh=refresh, offline=offline, taken=taken))
    if not extra:
        return catalog
    return replace(catalog, applications=catalog.applications + extra)
