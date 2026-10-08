"""Data-only Depot source discovery. Validation never authorizes installation.

Schemas 1 to 3 have no admitted state, commands, download locations, or trust
keys, and their validator refuses any field it does not know. Schema 4
(ADR-028, section 4.2) describes an application far more fully -- its tier,
developer, permissions, release and ratings -- and is read only after its
minisign signature has been verified against the key shipped in this package.

None of it can make an application installable. The backends and repositories
a catalogue may name are compiled in below, and the owning backend proves each
source independently (a known remote address, signature verification, a
pinned commit) before anything is installed. Reference URLs, media and links
are display, never package acquisition endpoints.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .depot_signature import SIGNATURE_MAX_BYTES, SignatureError, verify_file


MAX_BYTES = 128 * 1024
MAX_APPLICATIONS = 100
#: ADR-028 lifts both bounds for the signed schema 4 catalogue. They are
#: enforced after the signature is verified; before it, only the byte bound
#: applies, so an unauthenticated response cannot make Depot parse 8 MiB.
MAX_SIGNED_BYTES = 8 * 1024 * 1024
MAX_SIGNED_APPLICATIONS = 5000
MAX_COLLECTIONS = 200

DATA_DIRECTORY = Path(os.environ.get('LUMA_DEPOT_DATA_DIRECTORY', '/usr/share/luma/installer'))
#: The schema 3 document, still the source generated for older clients.
LEGACY_PATH = DATA_DIRECTORY / 'depot-catalog.json'
#: The schema 4 seed shipped in the package: public entries only.
SEED_PATH = DATA_DIRECTORY / 'depot-catalog-4.json'
DEFAULT_PATH = SEED_PATH if SEED_PATH.is_file() or not LEGACY_PATH.is_file() else LEGACY_PATH
#: The catalogue key (minisign format). Committed by the distribution
#: workstream as src/luma-installer/data/depot-catalog.pub.
PUBLIC_KEY_PATH = Path(os.environ.get('LUMA_DEPOT_CATALOG_KEY', str(DATA_DIRECTORY / 'depot-catalog.pub')))

#: Where a device reads the published catalogue from: static object storage
#: behind the CDN, never a server that runs code.
CATALOG_URL = os.environ.get(
    'LUMA_DEPOT_CATALOG_URL', 'https://dl.simplyluma.com/catalog/catalog-4.json')
CATALOG_TIMEOUT = 10
#: A window that is refreshed often still reads the network at most this often.
FRESH_SECONDS = 15 * 60
#: After a failed fetch, the local copy answers for this long without waiting
#: on the network again; an explicit refresh still tries at once.
RETRY_SECONDS = 2 * 60
_last_failure = 0.0

_ID = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
_PACKAGE = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9+_.-]{0,127}\Z')
_FLATPAK = re.compile(r'[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*){2,}\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_DEVELOPER_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_CATEGORY = re.compile(r'[a-z][a-z0-9-]{0,31}\Z')
_PERMISSION_KEY = re.compile(r'[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*){0,3}\Z')
_BRANCH = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z')
#: A desktop file id as the image installs it: reverse-DNS or a plain name.
_DESKTOP_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)*\.desktop\Z')
_REPOSITORIES = {'flatpak': {'flathub'},
                 # 'fedora' is the distribution's own repository, already
                 # enabled and already trusted on every Luma installation:
                 # the safest source there is, and the right answer whenever
                 # Fedora carries the application itself.
                 'rpm': {'fedora', 'google-chrome', 'brave-release', 'nordvpn',
                         'openai', 'virtualbox', 'teamviewer', 'mega'},
                 # Some publishers ship a versioned file and no repository at
                 # all. Listing those is still worth doing -- an application
                 # nobody can find is not available in any useful sense -- but
                 # the listing says where it comes from and nothing more: there
                 # is no address here for anything to fetch, and Valet reviews
                 # the file the person downloaded like any other.
                 'download': {'expressvpn',
                              # Figma publishes no Linux app; the community build Luma lists.
                              'figma-linux',
                              'termius'},
                 # Valet installs a .deb by building a Debian capsule and
                 # exporting the launcher, which is how an application
                 # published only for Debian reaches a Fedora-derived Luma.
                 'deb': {'anthropic'},
                 'snap': {'snap-store'}}
#: Schema 4 adds the Luma remote. Only schema 4 may name it: an unsigned
#: schema 3 document never can. ADR-031 adds ``rpm``/``luma``: an application
#: that ships in Luma's image and has no Flatpak yet. It is valid only beside a
#: ``sources.luma_system`` naming the same package, and it never installs
#: anything -- a Depot that predates ADR-031 has no ``luma`` RPM repository and
#: leaves such an entry out, as it does any source it does not know.
_REPOSITORIES_V4 = {**_REPOSITORIES, 'flatpak': {'flathub', 'luma'},
                    'rpm': _REPOSITORIES['rpm'] | {'luma'}}
TIERS = ('luma', 'verified', 'listed')
SIGN_IN = ('none', 'luma', 'third-party', 'required-third-party')
VERIFICATION = ('organization', 'individual')
PERMISSION_LEVELS = ('standard', 'sensitive', 'high')
PERMISSION_CHANGES = ('added', 'widened', 'removed', 'narrowed')


class CatalogError(ValueError):
    """Invalid discovery metadata; never silently accept a partial catalogue."""


@dataclass(frozen=True)
class Developer:
    id: str
    name: str
    verified: str = ''  # organization | individual | ''


@dataclass(frozen=True)
class Media:
    url: str
    sha256: str
    caption: str = ''
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    date: str = ''
    download_bytes: int = 0
    installed_bytes: int = 0
    notes: str = ''


@dataclass(frozen=True)
class PermissionGrant:
    key: str
    level: str
    read_only: bool = False
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class PermissionChange:
    key: str
    change: str
    level: str = 'standard'


@dataclass(frozen=True)
class Rating:
    average: float
    count: int


@dataclass(frozen=True)
class Installs:
    total: int
    last_30_days: int = 0


@dataclass(frozen=True)
class Collection:
    id: str
    name: str
    summary: str
    applications: tuple[str, ...]


@dataclass(frozen=True)
class LumaSystemSource:
    """ADR-031: the package in Luma's image that provides an application.

    Display and state only. Whether it is present, removed or restorable is read
    from the booted system, and removing it is authorized by the system helper
    against a verified catalogue, never by this object.
    """
    package: str
    desktop_id: str
    removable: bool = False


@dataclass(frozen=True)
class FlatpakSource:
    """ADR-031: the application as a Flatpak, on Luma and on other distributions."""
    repository: str
    source_id: str
    branch: str = 'stable'


@dataclass(frozen=True)
class SnapSource:
    """An application's own snap on the Snap Store, from a publisher pinned here.

    Depot installs it through snapd only when the Snap Store still names
    ``publisher`` as the snap's publisher and has verified that publisher, and
    only as a strictly confined snap: the system helper checks both again, as
    root, against the snap store before anything is installed.
    """
    name: str
    publisher: str
    channel: str = 'stable'
    #: Interfaces the snap declares that its store does not connect on its
    #: own and that Luma reviewed for this listing: connected to the system
    #: after install (``snap connect <name>:<plug>``) and shown on the listing.
    connect: tuple[str, ...] = ()


@dataclass(frozen=True)
class RpmRepositorySource:
    """A publisher's signed RPM repository, or Fedora's own (``id == 'fedora'``).

    ``id`` must be one of the RPM repositories compiled into this module. The
    signing key is fetched from ``gpgkey`` and accepted only when its primary
    fingerprint is one of ``fingerprints``; packages are then installed with
    signature checking on, the way ``sudo dnf install`` works on Luma.
    """
    id: str
    name: str
    package: str
    baseurl: str = ''
    gpgkey: str = ''
    fingerprints: tuple[str, ...] = ()
    repo_gpgcheck: bool = False


@dataclass(frozen=True)
class DebRepositorySource:
    """A publisher's signed APT repository; the package runs in a Debian capsule.

    Its key must match a pinned fingerprint, its ``InRelease`` must verify
    with that key, and the package must hash to what the signed index says
    (luma_installer.depot_deb_repository).
    """
    id: str
    name: str
    package: str
    url: str
    key: str
    fingerprints: tuple[str, ...]
    suite: str = 'stable'
    component: str = 'main'


@dataclass(frozen=True)
class Channel:
    """Where a listing installs from, as a person reads it. Display only."""
    kind: str               # luma | flathub | snap | rpm-repository | fedora | publisher
    title: str
    publisher: str = ''
    publisher_verified: bool = False
    community: bool = False
    #: Only for ``publisher``: why Depot cannot install it itself.
    reason: str = ''
    #: Only for ``publisher``: the app is a web app; the link opens it.
    web: bool = False


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    backend: str
    source_id: str
    repository: str
    distribution: str
    architectures: tuple[str, ...]
    reference_url: str
    qualification: str
    qualification_reason: str
    # Schema 2: what a person reads about an application Depot cannot install
    # itself, and where the publisher offers it. Display only; none of these
    # can make anything installable.
    publisher: str = ''
    summary: str = ''
    description: str = ''
    homepage: str = ''
    # Schema 4. Display and routing metadata; still no install authority.
    app_id: str = ''
    tier: str = 'listed'
    visibility: str = 'public'
    developer: Developer | None = None
    categories: tuple[str, ...] = ()
    icon: Media | None = None
    screenshots: tuple[Media, ...] = ()
    license: str = ''
    support_url: str = ''
    privacy_url: str = ''
    source_url: str = ''
    branch: str = 'stable'
    release: ReleaseInfo | None = None
    permissions: tuple[PermissionGrant, ...] = ()
    permission_changes: tuple[PermissionChange, ...] = ()
    sign_in: str = ''
    age_rating: str = ''
    rating: Rating | None = None
    installs: Installs | None = None
    # ADR-031 (schema 4 ``sources``). ``flatpak`` is also filled in from the
    # top-level fields of an entry that predates ``sources``, so a client asks
    # one question -- "is there a Flatpak?" -- whatever the catalogue's age.
    luma_system: LumaSystemSource | None = None
    flatpak: FlatpakSource | None = None
    preinstalled_on_luma: bool = False
    # Official channels other than a Flatpak (schema 4 ``sources``).
    snap: SnapSource | None = None
    rpm_repository: RpmRepositorySource | None = None
    deb_repository: DebRepositorySource | None = None
    channel: Channel | None = None
    #: flatpak | snap-strict | snap-classic | '' (installs outside a sandbox).
    sandbox: str = ''
    #: A system tool: exempt from the screenshot requirement.
    system_tool: bool = False

    @property
    def installable(self) -> bool:
        return False

    @property
    def identifier(self) -> str:
        """The application id people and links use: the app id, else the source."""
        return self.app_id or (self.source_id if self.backend == 'flatpak' else '')


@dataclass(frozen=True)
class Catalog:
    reviewed_on: str
    applications: tuple[CatalogEntry, ...]
    schema_version: int = 3
    generated_at: str = ''
    collections: tuple[Collection, ...] = ()
    #: Schema 4 entries left out because this Depot cannot describe them.
    skipped: int = 0
    #: True when the document was authenticated by the catalogue key.
    verified: bool = False

    def members(self, collection: Collection) -> tuple[CatalogEntry, ...]:
        found = (self.find(member) for member in collection.applications)
        return tuple(entry for entry in found if entry is not None)

    def find(self, identifier: str):
        """An entry by catalogue id, application id or Flatpak id."""
        for entry in self.applications:
            if identifier in (entry.id, entry.app_id, entry.source_id):
                return entry
        folded = identifier.casefold()
        return next((entry for entry in self.applications
                     if folded in (entry.app_id.casefold(), entry.source_id.casefold())
                     and entry.backend == 'flatpak'), None)


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise CatalogError('Unexpected or missing catalogue fields')


def _text(value, maximum=256):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise CatalogError('Invalid catalogue text')
    return value


def _prose(value, maximum):
    """Longer text that may carry paragraph breaks, but no other controls."""
    if (not isinstance(value, str) or len(value) > maximum
            or any((ord(c) < 32 and c not in '\n\t') or ord(c) == 127 for c in value)):
        raise CatalogError('Invalid catalogue text')
    return value.strip()


SCHEMA3_FIELDS = ('id', 'name', 'backend', 'source_id', 'repository', 'distribution',
                  'architectures', 'reference_url', 'qualification', 'qualification_reason')
OPTIONAL_FIELDS = ('publisher', 'summary', 'description', 'homepage')


def _https(address):
    try:
        if not isinstance(address, str):
            raise ValueError()
        url = urlsplit(address)
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.password is not None or url.port not in {None, 443}
                or '\\' in address or any(c.isspace() for c in address)
                or len(address) > 2048):
            raise ValueError()
    except ValueError as error:
        raise CatalogError('Invalid reference URL') from error
    return address


def _integer(value, maximum=2 ** 53):
    if type(value) is not int or value < 0 or value > maximum:
        raise CatalogError('Invalid catalogue number')
    return value


def _check_source(row, repositories, schema):
    backend = row['backend']
    if backend not in repositories or row['repository'] not in repositories[backend]:
        # Schema 3 lists applications from sources a newer Depot knows.
        # An older one leaves those out instead of refusing the whole
        # catalogue: listing a source never widens what may be installed,
        # because the sources a client accepts are compiled into it.
        # A name that is not even shaped like a source is still refused.
        if (schema >= 3 and all(type(row[key]) is str and _ID.fullmatch(row[key])
                                for key in ('backend', 'repository'))):
            return False
        raise CatalogError('Unsupported source backend or repository')
    pattern = _FLATPAK if backend == 'flatpak' else _PACKAGE
    if not isinstance(row['source_id'], str) or not pattern.fullmatch(row['source_id']):
        raise CatalogError('Invalid source identity')
    if row['distribution'] not in {'publisher', 'community', 'publisher-verified-community'}:
        raise CatalogError('Invalid distribution classification')
    architectures = row['architectures']
    if (not isinstance(architectures, list)
            or any(not isinstance(a, str) or a not in {'x86_64', 'aarch64'} for a in architectures)
            or len(set(architectures)) != len(architectures)):
        raise CatalogError('Invalid architecture collection')
    _https(row['reference_url'])
    return True


def validate_catalog(value) -> Catalog:
    if isinstance(value, dict) and type(value.get('schema_version')) is int \
            and value.get('schema_version') == 4:
        return _validate_v4(value)
    _fields(value, ('schema_version', 'reviewed_on', 'applications'))
    if type(value['schema_version']) is not int or value['schema_version'] not in (1, 2, 3):
        raise CatalogError('Unsupported catalogue schema')
    schema = value['schema_version']
    reviewed = _text(value['reviewed_on'], 10)
    try:
        if date.fromisoformat(reviewed).isoformat() != reviewed:
            raise ValueError()
    except ValueError as error:
        raise CatalogError('Invalid review date') from error
    rows = value['applications']
    if not isinstance(rows, list) or len(rows) > MAX_APPLICATIONS:
        raise CatalogError('Invalid application collection')
    entries, ids, sources = [], set(), set()
    for row in rows:
        optional = {key for key in row if key in OPTIONAL_FIELDS} if schema >= 2 and isinstance(row, dict) else set()
        _fields(row, SCHEMA3_FIELDS + tuple(optional))
        for name in SCHEMA3_FIELDS + tuple(optional):
            if name != 'architectures':
                _text(row[name], 1024 if name in ('reference_url', 'homepage', 'description') else 512)
        if 'homepage' in optional:
            _https(row['homepage'])
        if not _ID.fullmatch(row['id']) or row['id'] in ids:
            raise CatalogError('Invalid or duplicate application identity')
        if not _check_source(row, _REPOSITORIES, schema):
            continue
        source = (row['backend'], row['repository'], row['source_id'])
        if source in sources:
            raise CatalogError('Duplicate source mapping')
        if row['qualification'] not in {'pending', 'unsupported'}:
            raise CatalogError('Catalogue cannot admit an application for installation')
        ids.add(row['id'])
        sources.add(source)
        entries.append(CatalogEntry(**{**row, 'architectures': tuple(row['architectures'])},
                                    flatpak=_top_level_flatpak(row, 'stable')))
    return Catalog(reviewed, tuple(entries), schema_version=schema)


def _top_level_flatpak(row, branch):
    if row['backend'] != 'flatpak':
        return None
    return FlatpakSource(row['repository'], row['source_id'], branch)


# ── Schema 4 ─────────────────────────────────────────────────────────────

class _Skip(Exception):
    """This entry is well formed JSON that this Depot cannot describe."""


def _optional_text(row, name, maximum=512):
    if name not in row or row[name] in (None, ''):
        return ''
    return _text(row[name], maximum)


def _optional_url(row, name):
    if name not in row or row[name] in (None, ''):
        return ''
    return _https(row[name])


def _media(value, captioned=False):
    if not isinstance(value, dict):
        raise CatalogError('Invalid media')
    sha256 = value.get('sha256')
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise CatalogError('Media must carry a SHA-256 digest')
    return Media(_https(value.get('url')), sha256,
                 _optional_text(value, 'caption', 256) if captioned else '',
                 _integer(value.get('width', 0), 65535), _integer(value.get('height', 0), 65535))


def _v4_entry(row, ids, sources):
    if not isinstance(row, dict):
        raise CatalogError('Invalid application entry')
    missing = [name for name in SCHEMA3_FIELDS + ('tier', 'visibility') if name not in row]
    if missing:
        raise CatalogError('Missing catalogue fields')
    for name in SCHEMA3_FIELDS:
        if name != 'architectures':
            _text(row[name], 1024 if name == 'reference_url' else 512)
    if not _ID.fullmatch(row['id']):
        raise CatalogError('Invalid application identity')
    if row['id'] in ids:
        raise CatalogError('Duplicate application identity')
    ids.add(row['id'])
    if row['visibility'] not in ('public', 'unlisted'):
        # A snapshot carries only public and unlisted entries. A private or
        # withdrawn one reaching a device is a publishing fault: never show it.
        raise _Skip()
    if row['tier'] not in TIERS:
        raise _Skip()
    if not _check_source(row, _REPOSITORIES_V4, 4):
        raise _Skip()
    luma_system, listed_flatpak = _sources(row)
    snap, rpm_repository = _channel_sources(row)
    deb_repository = _deb_source(row)
    if (row['backend'], row['repository']) == ('rpm', 'luma'):
        # An application Luma's image ships and no remote carries yet. The
        # top-level fields name the Flatpak whenever one exists (ADR-031), so
        # an rpm/luma entry that also lists one contradicts itself.
        if luma_system is None or luma_system.package != row['source_id'] or listed_flatpak is not None:
            raise _Skip()
        # One package can provide several applications (prairie-core-apps);
        # what must be unique is the application, its desktop file.
        source = ('rpm', 'luma', luma_system.desktop_id)
    else:
        source = (row['backend'], row['repository'], row['source_id'])
    # Two listings for one desktop file would put the same app in Depot twice.
    desktop = ('luma_system', luma_system.desktop_id) if luma_system is not None else None
    if source in sources or desktop in sources:
        raise CatalogError('Duplicate source mapping')
    sources.update(key for key in (source, desktop) if key is not None)
    qualification = row['qualification']
    if qualification not in ('pending', 'unsupported', 'admitted'):
        raise _Skip()
    # Admission is a statement about a reviewed release on the Luma remote.
    # A listed application installs from its publisher and is never admitted.
    if qualification == 'admitted' and row['tier'] == 'listed':
        raise _Skip()
    if row['repository'] == 'luma' and row['tier'] == 'listed':
        raise _Skip()

    app_id = _optional_text(row, 'app_id', 255)
    if app_id and not _FLATPAK.fullmatch(app_id):
        raise CatalogError('Invalid application id')
    if row['backend'] == 'flatpak' and app_id and app_id != row['source_id']:
        raise CatalogError('Application id and Flatpak source disagree')

    developer = None
    if isinstance(row.get('developer'), dict):
        value = row['developer']
        if not isinstance(value.get('id'), str) or not _DEVELOPER_ID.fullmatch(value['id']):
            raise CatalogError('Invalid developer')
        verified = value.get('verified')
        developer = Developer(value['id'], _text(value.get('name'), 128),
                              verified if verified in VERIFICATION else '')
    elif row.get('developer') is not None:
        raise CatalogError('Invalid developer')

    categories = row.get('categories', [])
    if (not isinstance(categories, list) or len(categories) > 8
            or any(not isinstance(c, str) or not _CATEGORY.fullmatch(c) for c in categories)):
        raise CatalogError('Invalid categories')

    icon = _media(row['icon']) if row.get('icon') is not None else None
    shots = row.get('screenshots', [])
    if not isinstance(shots, list) or len(shots) > 12:
        raise CatalogError('Invalid screenshots')

    release = None
    if row.get('release') is not None:
        value = row['release']
        if not isinstance(value, dict):
            raise CatalogError('Invalid release')
        released = _optional_text(value, 'date', 10)
        if released:
            try:
                date.fromisoformat(released)
            except ValueError as error:
                raise CatalogError('Invalid release date') from error
        release = ReleaseInfo(_text(value.get('version'), 64), released,
                              _integer(value.get('download_bytes', 0)),
                              _integer(value.get('installed_bytes', 0)),
                              _prose(value.get('notes', ''), 8192))

    permissions = []
    grants = row.get('permissions', [])
    if not isinstance(grants, list) or len(grants) > 64:
        raise CatalogError('Invalid permissions')
    for grant in grants:
        if (not isinstance(grant, dict) or not isinstance(grant.get('key'), str)
                or not _PERMISSION_KEY.fullmatch(grant['key'])
                or grant.get('level') not in PERMISSION_LEVELS):
            raise CatalogError('Invalid permission')
        names = grant.get('names', [])
        if not isinstance(names, list) or len(names) > 32:
            raise CatalogError('Invalid permission names')
        permissions.append(PermissionGrant(
            grant['key'], grant['level'], grant.get('access') == 'read',
            tuple(_text(name, 255) for name in names)))

    changes = []
    listed_changes = row.get('permission_changes', [])
    if not isinstance(listed_changes, list) or len(listed_changes) > 64:
        raise CatalogError('Invalid permission changes')
    for change in listed_changes:
        if isinstance(change, str) and _PERMISSION_KEY.fullmatch(change):
            changes.append(PermissionChange(change, 'added'))
            continue
        if (not isinstance(change, dict) or not isinstance(change.get('key'), str)
                or not _PERMISSION_KEY.fullmatch(change['key'])
                or change.get('change', 'added') not in PERMISSION_CHANGES
                or change.get('level', 'standard') not in PERMISSION_LEVELS):
            raise CatalogError('Invalid permission change')
        changes.append(PermissionChange(change['key'], change.get('change', 'added'),
                                        change.get('level', 'standard')))

    rating = None
    if row.get('rating') is not None:
        value = row['rating']
        average = value.get('average') if isinstance(value, dict) else None
        if type(average) not in (int, float) or not 0 <= average <= 5:
            raise CatalogError('Invalid rating')
        rating = Rating(float(average), _integer(value.get('count', 0)))
    installs = None
    if row.get('installs') is not None:
        value = row['installs']
        if not isinstance(value, dict):
            raise CatalogError('Invalid install counts')
        installs = Installs(_integer(value.get('total', 0)), _integer(value.get('last_30_days', 0)))

    branch = _optional_text(row, 'branch', 32) or 'stable'
    if not _BRANCH.fullmatch(branch):
        raise CatalogError('Invalid branch')
    flatpak = _top_level_flatpak(row, branch)
    if listed_flatpak is not None and listed_flatpak != flatpak:
        # Clients that do not read ``sources`` install what the top-level
        # fields name. Two descriptions of one Flatpak that disagree would
        # make the same listing install different things on different Depots.
        raise _Skip()
    preinstalled = row.get('preinstalled_on_luma', False)
    if type(preinstalled) is not bool:
        raise CatalogError('Invalid preinstalled flag')
    sign_in = row.get('sign_in') if row.get('sign_in') in SIGN_IN else ''

    return CatalogEntry(
        id=row['id'], name=row['name'], backend=row['backend'], source_id=row['source_id'],
        repository=row['repository'], distribution=row['distribution'],
        architectures=tuple(row['architectures']), reference_url=row['reference_url'],
        qualification=qualification, qualification_reason=row['qualification_reason'],
        publisher=_optional_text(row, 'publisher', 512),
        summary=_optional_text(row, 'summary', 512),
        description=_prose(row.get('description', '') or '', 16384),
        homepage=_optional_url(row, 'homepage'),
        app_id=app_id, tier=row['tier'], visibility=row['visibility'],
        developer=developer, categories=tuple(categories), icon=icon,
        screenshots=tuple(_media(shot, captioned=True) for shot in shots),
        license=_optional_text(row, 'license', 256),
        support_url=_optional_url(row, 'support_url'),
        privacy_url=_optional_url(row, 'privacy_url'),
        source_url=_optional_url(row, 'source_url'),
        branch=branch, release=release, permissions=tuple(permissions),
        permission_changes=tuple(changes), sign_in=sign_in,
        age_rating=_optional_text(row, 'age_rating', 64),
        rating=rating, installs=installs,
        luma_system=luma_system, flatpak=flatpak,
        preinstalled_on_luma=preinstalled and luma_system is not None,
        snap=snap, rpm_repository=rpm_repository, deb_repository=deb_repository, channel=_channel(row),
        sandbox=row.get('sandbox') if row.get('sandbox') in SANDBOXES else '',
        system_tool=row.get('system_tool') is True)


def _sources(row):
    """ADR-031 ``sources``: (luma_system or None, flatpak or None).

    Malformed values refuse the catalogue like any other malformed field.
    Unknown source kinds are ignored, so a later schema can add one. Only a
    Luma-tier entry may name a package of Luma's image as its own: a verified or
    listed developer's listing naming ``nautilus`` would otherwise make Depot
    offer to remove the system's file manager on the developer's say-so.

    A developer's Flatpak listing may still say that Luma's image also ships
    the same application, so Depot shows it as included where the package is
    present and never installs a second copy exporting the same desktop file.
    That mapping is accepted only for the listing's own desktop file and is
    never removable: it can hide the listing's Install, nothing more.
    """
    value = row.get('sources')
    if value is None:
        return None, None
    if not isinstance(value, dict):
        raise CatalogError('Invalid sources')
    luma_system = flatpak = None
    system = value.get('luma_system')
    if system is not None:
        if (not isinstance(system, dict)
                or not isinstance(system.get('package'), str) or not _PACKAGE.fullmatch(system['package'])
                or system['package'].startswith('-')
                or not isinstance(system.get('desktop_id'), str)
                or len(system['desktop_id']) > 255 or not _DESKTOP_ID.fullmatch(system['desktop_id'])
                or type(system.get('removable', False)) is not bool):
            raise CatalogError('Invalid Luma system source')
        if row.get('tier') == 'luma':
            luma_system = LumaSystemSource(system['package'], system['desktop_id'],
                                           system.get('removable', False))
        elif (row.get('backend') == 'flatpak' and isinstance(row.get('app_id'), str)
              and system['desktop_id'] == row['app_id'] + '.desktop'):
            luma_system = LumaSystemSource(system['package'], system['desktop_id'], False)
    listed = value.get('flatpak')
    if listed is not None:
        if (not isinstance(listed, dict) or not isinstance(listed.get('repository'), str)
                or not isinstance(listed.get('source_id'), str)
                or not _FLATPAK.fullmatch(listed['source_id'])):
            raise CatalogError('Invalid Flatpak source')
        branch = listed.get('branch', 'stable')
        if not isinstance(branch, str) or not _BRANCH.fullmatch(branch):
            raise CatalogError('Invalid branch')
        if listed['repository'] not in _REPOSITORIES_V4['flatpak']:
            if not _ID.fullmatch(listed['repository']):
                raise CatalogError('Unsupported source backend or repository')
            raise _Skip()
        flatpak = FlatpakSource(listed['repository'], listed['source_id'], branch)
    return luma_system, flatpak


_SNAP_NAME = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?\Z')
_SNAP_PUBLISHER = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
_SNAP_CHANNELS = ('stable', 'candidate', 'beta', 'edge')
_SNAP_PLUG = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
_FINGERPRINT = re.compile(r'[0-9A-F]{40}\Z')
CHANNEL_KINDS = ('luma', 'flathub', 'snap', 'rpm-repository', 'deb-repository', 'fedora', 'publisher')
SANDBOXES = ('flatpak', 'snap-strict', 'snap-classic', 'deb-capsule')


def _repository_url(value):
    """An https repository address; dnf's $basearch and $releasever are allowed."""
    if not isinstance(value, str) or len(value) > 512:
        raise CatalogError('Invalid repository address')
    probe = value.replace('$basearch', 'x86_64').replace('$releasever', '44')
    if '$' in probe:
        raise CatalogError('Invalid repository address')
    _https(probe)
    return value


def _channel_sources(row):
    """``sources.snap`` and ``sources.rpm_repository``: (snap or None, rpm repository or None).

    Each must agree with the entry's top-level source, so a client that reads
    only ``backend``/``repository``/``source_id`` and one that reads ``sources``
    describe the same thing. A repository id that is not compiled into this
    Depot leaves the entry out, like any unknown source.
    """
    value = row.get('sources')
    if not isinstance(value, dict):
        return None, None
    snap = rpm = None
    # A kind that does not describe this entry's own source is ignored, as an
    # unknown kind is: only the snap of a snap entry, the repository of an
    # RPM entry, can say how that entry installs.
    listed = value.get('snap') if row.get('backend') == 'snap' else None
    if listed is not None:
        if (not isinstance(listed, dict) or not isinstance(listed.get('name'), str)
                or not _SNAP_NAME.fullmatch(listed['name'])
                or not isinstance(listed.get('publisher'), str)
                or not _SNAP_PUBLISHER.fullmatch(listed['publisher'])
                or listed.get('channel', 'stable') not in _SNAP_CHANNELS):
            raise CatalogError('Invalid snap source')
        if (row.get('backend'), row.get('repository'), row.get('source_id')) != \
                ('snap', 'snap-store', listed['name']):
            raise _Skip()
        connect = listed.get('connect', [])
        if (not isinstance(connect, list) or len(connect) > 16
                or any(not isinstance(p, str) or not _SNAP_PLUG.fullmatch(p) for p in connect)):
            raise CatalogError('Invalid snap interface list')
        snap = SnapSource(listed['name'], listed['publisher'], listed.get('channel', 'stable'), tuple(connect))
    listed = value.get('rpm_repository') if row.get('backend') == 'rpm' and row.get('repository') != 'luma' else None
    if listed is not None:
        if (not isinstance(listed, dict) or not isinstance(listed.get('id'), str)
                or not _ID.fullmatch(listed['id'])
                or not isinstance(listed.get('package'), str) or not _PACKAGE.fullmatch(listed['package'])
                or listed['package'].startswith('-')):
            raise CatalogError('Invalid RPM repository source')
        if listed['id'] not in _REPOSITORIES_V4['rpm'] or listed['id'] == 'luma':
            raise _Skip()
        if (row.get('backend'), row.get('repository'), row.get('source_id')) != \
                ('rpm', listed['id'], listed['package']):
            raise _Skip()
        fingerprints = listed.get('fingerprints', [])
        if (not isinstance(fingerprints, list) or len(fingerprints) > 4
                or any(not isinstance(f, str) or not _FINGERPRINT.fullmatch(f) for f in fingerprints)):
            raise CatalogError('Invalid repository key fingerprint')
        if listed['id'] == 'fedora':
            # Fedora's repositories are already configured and trusted on the
            # system; a listing may name a package in them, never an address.
            if listed.get('baseurl') or listed.get('gpgkey') or fingerprints:
                raise CatalogError('Fedora packages cannot name a repository address')
            rpm = RpmRepositorySource('fedora', _text(listed.get('name', 'Fedora'), 64), listed['package'])
        else:
            if not fingerprints:
                raise CatalogError('A publisher repository needs its key fingerprint')
            if type(listed.get('repo_gpgcheck', False)) is not bool:
                raise CatalogError('Invalid repository signature setting')
            rpm = RpmRepositorySource(listed['id'], _text(listed.get('name'), 64), listed['package'],
                                      _repository_url(listed.get('baseurl')),
                                      _repository_url(listed.get('gpgkey')),
                                      tuple(fingerprints), listed.get('repo_gpgcheck', False))
    return snap, rpm


_APT_WORD = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')


def _deb_source(row):
    value = row.get('sources')
    if not isinstance(value, dict) or row.get('backend') != 'deb':
        return None
    listed = value.get('deb_repository')
    if listed is None:
        return None
    if (not isinstance(listed, dict) or not isinstance(listed.get('id'), str) or not _ID.fullmatch(listed['id'])
            or not isinstance(listed.get('package'), str) or not _PACKAGE.fullmatch(listed['package'])
            or listed['package'].startswith('-')
            or any(not isinstance(listed.get(key, default), str) or not _APT_WORD.fullmatch(listed.get(key, default))
                   for key, default in (('suite', 'stable'), ('component', 'main')))):
        raise CatalogError('Invalid Debian repository source')
    if listed['id'] not in _REPOSITORIES_V4['deb']:
        raise _Skip()
    if (row.get('repository'), row.get('source_id')) != (listed['id'], listed['package']):
        raise _Skip()
    fingerprints = listed.get('fingerprints', [])
    if (not isinstance(fingerprints, list) or not fingerprints or len(fingerprints) > 4
            or any(not isinstance(f, str) or not _FINGERPRINT.fullmatch(f) for f in fingerprints)):
        raise CatalogError('A publisher repository needs its key fingerprint')
    return DebRepositorySource(listed['id'], _text(listed.get('name'), 64), listed['package'],
                               _https(listed.get('url')), _https(listed.get('key')), tuple(fingerprints),
                               listed.get('suite', 'stable'), listed.get('component', 'main'))


def _channel(row):
    value = row.get('channel')
    if not isinstance(value, dict) or value.get('kind') not in CHANNEL_KINDS:
        return None
    try:
        return Channel(value['kind'], _text(value.get('title'), 128),
                       _optional_text(value, 'publisher', 128),
                       value.get('publisher_verified') is True, value.get('community') is True,
                       _prose(value.get('reason', '') or '', 1024), value.get('web') is True)
    except CatalogError:
        return None


def _validate_v4(value) -> Catalog:
    for name in ('generated_at', 'applications'):
        if name not in value:
            raise CatalogError('Missing catalogue fields')
    generated = _text(value['generated_at'], 40)
    try:
        moment = datetime.fromisoformat(generated.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError()
    except ValueError as error:
        raise CatalogError('Invalid generation time') from error
    rows = value['applications']
    if not isinstance(rows, list) or len(rows) > MAX_SIGNED_APPLICATIONS:
        raise CatalogError('Invalid application collection')
    entries, ids, sources, skipped = [], set(), set(), 0
    for row in rows:
        try:
            entries.append(_v4_entry(row, ids, sources))
        except _Skip:
            skipped += 1
    collections, collection_ids = [], set()
    listed = value.get('collections', [])
    if not isinstance(listed, list) or len(listed) > MAX_COLLECTIONS:
        raise CatalogError('Invalid collections')
    for row in listed:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) \
                or not _ID.fullmatch(row['id']) or row['id'] in collection_ids:
            raise CatalogError('Invalid collection')
        members = row.get('applications', [])
        if (not isinstance(members, list) or len(members) > MAX_SIGNED_APPLICATIONS
                or any(not isinstance(m, str) or not _ID.fullmatch(m) for m in members)):
            raise CatalogError('Invalid collection members')
        collection_ids.add(row['id'])
        # Members that are not in this snapshot (private until their public
        # gate, or withdrawn) are kept by id: first-boot provisioning must
        # know they were chosen so it can install them once they are public.
        # Anything that draws a collection shows only the members it can find.
        collections.append(Collection(
            row['id'], _text(row.get('name'), 128), _optional_text(row, 'summary', 512),
            tuple(dict.fromkeys(members))))
    return Catalog(moment.date().isoformat(), tuple(entries), schema_version=4,
                   generated_at=generated, collections=tuple(collections), skipped=skipped)


# ── Reading and fetching ─────────────────────────────────────────────────

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError('Duplicate JSON field')
        result[key] = value
    return result


def _decode(content: bytes):
    try:
        return json.loads(content.decode('utf-8'), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise CatalogError('Cannot read catalogue') from error


def cache_path() -> Path:
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return Path(base) / 'luma/depot/catalog-4.json'


def signature_path(path: Path) -> Path:
    return path.with_name(path.name + '.minisig')


def public_key(path=None) -> bytes:
    try:
        with Path(path or PUBLIC_KEY_PATH).open('rb') as stream:
            return stream.read(1025)
    except OSError as error:
        raise CatalogError('The catalogue key is not installed') from error


def _verified(content: bytes, signature: bytes, key: bytes) -> Catalog:
    try:
        verify_file(key, content, signature)
    except SignatureError as error:
        raise CatalogError(str(error)) from error
    catalog = validate_catalog(_decode(content))
    if catalog.schema_version != 4:
        raise CatalogError('The signed catalogue is not schema 4')
    return Catalog(catalog.reviewed_on, catalog.applications, catalog.schema_version,
                   catalog.generated_at, catalog.collections, catalog.skipped, verified=True)


def _read_bounded(stream, limit):
    content = stream.read(limit + 1)
    if len(content) > limit:
        raise CatalogError('Catalogue exceeds size limit')
    return content


def _get(url, limit, timeout):
    request = urllib.request.Request(url, headers={
        'Accept': 'application/json, application/octet-stream',
        'User-Agent': 'Luma-Depot/4'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as stream:
            return _read_bounded(stream, limit)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise CatalogError('Cannot reach catalogue') from error


def fetch_catalog(url=None, *, timeout=CATALOG_TIMEOUT, cache=None, key=None,
                  countme=None, on_counted=None) -> Catalog:
    """Read the published catalogue, verify and validate it, keep it for next time.

    The signature is checked before the document is parsed, the size bound
    before the signature. A verified catalogue older than the one already kept
    is refused, so a stale copy replayed from a mirror cannot roll a device
    back to a listing that has since been withdrawn.

    ``countme`` is the weekly active-installation bucket (ADR-028, section 9),
    added to this one request only; see :mod:`luma_installer.depot_counting`.
    """
    url = url or CATALOG_URL
    cache = Path(cache) if cache is not None else cache_path()
    key_data = public_key(key)
    address = url if countme is None else f'{url}{"&" if "?" in url else "?"}countme={int(countme)}'
    content = _get(address, MAX_SIGNED_BYTES, timeout)
    if countme is not None and on_counted is not None:
        # The server has seen this week's request whatever the payload turns
        # out to be; counting it again would count this computer twice.
        on_counted()
    signature = _get(url + '.minisig', SIGNATURE_MAX_BYTES, timeout)
    catalog = _verified(content, signature, key_data)
    try:
        kept = load_verified(cache, key=key)
    except CatalogError:
        kept = None
    if kept is not None and _moment(kept.generated_at) > _moment(catalog.generated_at):
        raise CatalogError('The published catalogue is older than the one on this device')
    _remember(signature_path(cache), signature)
    _remember(cache, content)
    return catalog


def _moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _remember(path: Path, content: bytes) -> None:
    """Keep a verified file, atomically. A partial file is never read."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.catalog')
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(content)
            os.replace(temporary, path)
        except BaseException:
            os.unlink(temporary)
            raise
    except OSError:
        # A catalogue that cannot be cached is still a catalogue.
        pass


def load_verified(path=None, *, key=None) -> Catalog:
    """The last catalogue this device verified. Re-verified on every read."""
    path = Path(path) if path is not None else cache_path()
    try:
        with path.open('rb') as stream:
            content = _read_bounded(stream, MAX_SIGNED_BYTES)
        with signature_path(path).open('rb') as stream:
            signature = _read_bounded(stream, SIGNATURE_MAX_BYTES)
    except OSError as error:
        raise CatalogError('No verified catalogue is kept') from error
    return _verified(content, signature, public_key(key))


def load_catalog(path=DEFAULT_PATH) -> Catalog:
    """Read a bounded local catalogue shipped in a package; no network access.

    Only the package's own files are read this way. A downloaded catalogue is
    read with :func:`load_verified`, which checks its signature again.
    """
    try:
        with Path(path).open('rb') as stream:
            head = stream.read(MAX_BYTES + 1)
            if len(head) > MAX_BYTES:
                # Only the schema 4 seed may be larger than the old bound.
                content = head + _read_bounded(stream, MAX_SIGNED_BYTES - len(head))
                value = _decode(content)
                if not isinstance(value, dict) or value.get('schema_version') != 4:
                    raise CatalogError('Catalogue exceeds size limit')
            else:
                value = _decode(head)
        return validate_catalog(value)
    except OSError as error:
        raise CatalogError('Cannot read catalogue') from error


def seed_catalog(seed=None) -> Catalog:
    for path in ((seed,) if seed is not None else (SEED_PATH, LEGACY_PATH)):
        try:
            return load_catalog(path)
        except CatalogError:
            continue
    raise CatalogError('No catalogue is installed')


def local_catalog(*, cache=None, seed=None, key=None) -> Catalog:
    """Newest catalogue on disk: the verified cache, else the shipped seed."""
    try:
        return load_verified(cache, key=key)
    except CatalogError:
        return seed_catalog(seed)


def current_catalog(*, url=None, cache=None, seed=None, key=None, refresh=False,
                    counter=None) -> Catalog:
    """The newest catalogue this device can vouch for.

    What was published and verifies, then the last one that verified here,
    then the copy the package shipped. An offline device, or one where the
    catalogue key or remote is not live yet, still gets a catalogue; it does
    not get today's.

    ``counter`` is an optional :class:`depot_counting.Countme`; the bucket is
    attached only to a fetch in a week not yet counted, and the week is marked
    only once the request succeeded.
    """
    global _last_failure
    cache = Path(cache) if cache is not None else cache_path()
    if not refresh:
        try:
            if time.time() - cache.stat().st_mtime < FRESH_SECONDS:
                return load_verified(cache, key=key)
        except (OSError, CatalogError):
            pass
        if time.monotonic() - _last_failure < RETRY_SECONDS and _last_failure:
            return local_catalog(cache=cache, seed=seed, key=key)
    bucket = counter.pending() if counter is not None else None
    try:
        return fetch_catalog(url, cache=cache, key=key, countme=bucket,
                             on_counted=counter.counted if counter is not None else None)
    except CatalogError:
        _last_failure = time.monotonic()
    return local_catalog(cache=cache, seed=seed, key=key)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
