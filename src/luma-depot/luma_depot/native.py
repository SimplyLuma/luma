# SPDX-License-Identifier: Apache-2.0
"""Real Depot data; discovery metadata never authorizes an installation."""
import hashlib
from pathlib import Path
import shutil
import threading
import time

from gi.repository import Gio, GLib

from luma_installer.depot_inventory import visible_applications, removal_arguments
from luma_installer.desktop import iter_records
from luma_installer import depot_permissions
from .providers import (App, Catalogue, Collection, InstalledApp, Permission, ProviderError,
                        Progress, Screenshot, run_async)
from .appstream_catalogue import CATEGORIES
from . import channels as channel_apps

try:
    from luma_installer import depot_system_apps as system_apps
except ImportError:
    # Same reason as below: an older luma_installer on this computer must not
    # stop Depot opening. Without it, Depot lists no Luma-shipped apps' state.
    system_apps = None

try:
    from luma_installer.depot_catalog import current_catalog as load_catalog
except ImportError:
    # An installed luma_installer may predate the published catalogue -- this
    # machine carries a user-site copy of it that does. Depot has to open
    # regardless: a shipped catalogue is a smaller loss than no window at all,
    # and an ImportError at module scope means the application simply never
    # appears, with nothing on screen to say why.
    from luma_installer.depot_catalog import load_catalog


#: Schema 3 entries carry no categories; these keep the shelves they had.
_LEGACY_GROUPS = {'gimp': 'Create', 'audacity': 'Create', 'obs': 'Create',
                  'spotify': 'Media', 'vlc': 'Media', 'steam': 'Media',
                  'libreoffice': 'Work', 'obsidian': 'Work', 'thunderbird': 'Work',
                  'discord': 'Work'}
#: Catalogue categories (ADR-028 schema 4) onto Depot's four shelves.
_SHELVES = {
    'Create': {'create', 'graphics', 'design', 'photo', 'photography', 'video', 'audio', 'music-production'},
    'Work': {'work', 'office', 'productivity', 'development', 'develop', 'communication', 'communicate',
             'education', 'science', 'finance', 'writing'},
    'Media': {'media', 'games', 'game', 'entertainment', 'music', 'streaming', 'reading'},
}
_TONES = ('blue', 'green', 'violet', 'amber')
_SOURCE_TITLES = {'luma': 'Luma', 'flathub': 'Flathub'}
UPDATE_CHECK_SECONDS = 10 * 60
#: Where a person on another distribution gets Luma (ADR-031).
GET_LUMA_URL = 'https://simplyluma.com/download'
HELPER = '/usr/libexec/luma-installer-system'
SYSTEM_AVAILABILITY = {
    'installed': 'Installed with Luma',
    'layered': 'Installed on this computer',
    'removed': 'Removed',
    'removal-pending': 'Removed when you restart',
    'restore-pending': 'Restored when you restart',
}
#: Desktop file id -> catalogue identity for apps Luma's image ships, so an
#: installed app and its listing are one app in every list (ADR-031).
_system_identities = {}


def providers():
    """Select the actual installed sandbox boundary; native behavior is retained."""
    from .host_client import sandboxed
    if sandboxed():
        from .host_client import Client, Catalogue, Installation
        client = Client()
        return Catalogue(client), Installation(client), client
    return NativeCatalogue(), NativeInstallation(), None


def inventory():
    return visible_applications(Gio.AppInfo.get_all(), iter_records())


def architecture():
    from luma_installer.inspectors import host_architecture
    return host_architecture()


def remember_system_identities(catalog):
    """Map each listed image app's desktop file to its catalogue identity."""
    found = {}
    for entry in catalog.applications:
        system = getattr(entry, 'luma_system', None)
        if system is not None:
            found[system.desktop_id] = 'catalog:' + entry.id
    _system_identities.clear()
    _system_identities.update(found)


def identity_for_installed(record):
    # A curated application's identity must survive install/remove refreshes.
    from luma_installer.depot_flatpak import APPLICATION_SOURCES
    if record.desktop_id in _system_identities:
        return _system_identities[record.desktop_id]
    source = record.app_info.get_string('X-Flatpak') if hasattr(record.app_info, 'get_string') else None
    for identity, app_id in APPLICATION_SOURCES.items():
        if source == app_id or record.desktop_id == app_id + '.desktop':
            return 'catalog:' + identity
    return record.desktop_id


def app_for_installed(record):
    icon = record.app_info.get_icon()
    return App(identity_for_installed(record), record.name, record.provider,
               description=record.management,
               icon_name=icon.to_string() if icon else 'application-x-executable-symbolic',
               installable=False, removable=record.can_review_removal,
               availability=record.management)


def shelf(entry):
    for category in entry.categories:
        for name, members in _SHELVES.items():
            if category in members:
                return name
    return _LEGACY_GROUPS.get(entry.id, 'Tools')


def tone(name):
    return _TONES[hashlib.sha256(name.encode('utf-8')).digest()[0] % len(_TONES)]


def permission_rows(grants, changes=()):
    """Catalogue or computed grants as the rows the window draws."""
    changed = {change.key: change.change for change in changes}
    rows = []
    for grant in grants:
        described = depot_permissions.Grant(grant.key, grant.level,
                                            getattr(grant, 'read_only', False),
                                            tuple(getattr(grant, 'names', ())))
        rows.append(Permission(grant.key, described.title, described.detail,
                               notable=grant.level != 'standard', level=grant.level,
                               change=changed.get(grant.key, '')))
    for change in changes:
        if change.change == 'removed':
            described = depot_permissions.Grant(change.key, change.level)
            rows.append(Permission(change.key, described.title, described.detail,
                                   level=change.level, change='removed'))
    return tuple(rows)


def change_rows(changes):
    rows = []
    for change in changes:
        described = depot_permissions.Grant(change.key, change.level)
        rows.append(Permission(change.key, described.title, described.detail,
                               notable=change.change in ('added', 'widened'),
                               level=change.level, change=change.change))
    return tuple(rows)


def _installations():
    from luma_installer.depot_flatpak import _installations as installations
    return installations()


def installed_flatpak_refs():
    """Installed Flatpak applications by id: (installation, installed ref)."""
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    found = {}
    try:
        for installation in _installations():
            for ref in installation.list_installed_refs_by_kind(Flatpak.RefKind.APP, None):
                found.setdefault(ref.get_name(), (installation, ref))
    except (GLib.Error, ValueError, ImportError):
        pass
    return found


def installed_source(record, refs):
    """Associate a canonical Luma launcher with its installed Flatpak owner."""
    source = (record.app_info.get_string('X-Flatpak')
              if hasattr(record.app_info, 'get_string') else None)
    if source:
        return source
    # Older images put a native launcher ahead of Flatpak's exported desktop
    # file. Its canonical id still names the managed app, but X-Flatpak is
    # absent. Infer ownership only from that exact id and an installed ref
    # from a known remote, never from a display name or catalogue alias.
    from luma_installer.depot_flatpak import APPLICATION_SOURCES
    candidate = record.desktop_id.removesuffix('.desktop')
    ref = refs.get(candidate)
    if (record.desktop_id == candidate + '.desktop'
            and candidate in APPLICATION_SOURCES.values()
            and ref is not None and ref[1].get_origin() in _SOURCE_TITLES):
        return candidate
    return None


def runtime_installed(name):
    """Whether any installation has runtime ``name`` (any branch or arch)."""
    import gi
    try:
        gi.require_version('Flatpak', '1.0')
        from gi.repository import Flatpak
        for installation in _installations():
            for ref in installation.list_installed_refs_by_kind(Flatpak.RefKind.RUNTIME, None):
                if ref.get_name() == name:
                    return True
    except (GLib.Error, ValueError, ImportError):
        return True  # unknown: say nothing rather than something untrue
    return False


def read_system_apps(catalog):
    """The system half of every listing on this computer, or None when unknown."""
    if system_apps is None:
        return None
    packages = {entry.luma_system.package for entry in catalog.applications
                if getattr(entry, 'luma_system', None) is not None}
    try:
        return system_apps.SystemApps.read(packages)
    except Exception:
        return None


def system_removal(system, listing, state, blockers):
    """(Depot may offer Remove, the sentence saying why not)."""
    if state != 'installed':
        if state == 'layered':
            return False, 'It was added to this computer, so Valet removes it.'
        return False, ''
    if not listing.removable:
        return False, 'Luma needs this app, so it cannot be removed.'
    view = system.view
    if view is not None and view.booted is not None and listing.package in view.booted.replaced:
        return False, 'This computer runs a replaced copy of it.'
    if getattr(system_apps, 'in_base_database', None) is not None and \
            system_apps.in_base_database(listing.package) is False:
        return False, 'This version of Luma cannot remove it from its system image.'
    if listing.package not in blockers:
        blockers[listing.package] = system_apps.required_by(listing.package)
    needed = blockers[listing.package]
    if needed:
        return False, 'Other parts of Luma use it, so it stays.'
    return True, ''


def local_permissions(ref):
    from luma_installer.depot_flatpak import metadata_text
    try:
        text = metadata_text(ref.load_metadata(None))
    except GLib.Error:
        return ()
    return depot_permissions.from_metadata(text, ref.get_name()) if text else ()


class NativeCatalogue:
    def __init__(self):
        self._refresh = False
        self._lock = threading.Lock()
        self._last = None

    def request_refresh(self):
        self._refresh = True

    def _catalog(self):
        from luma_installer.depot_counting import Countme
        refresh, self._refresh = self._refresh, False
        try:
            return load_catalog(refresh=refresh, counter=Countme())
        except TypeError:
            return load_catalog()

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def _snapshot(self):
        installed = inventory()
        from .native_metadata import cached_metadata
        arch = architecture()
        metadata = cached_metadata(arch)
        from luma_installer.depot_flatpak import luma_remote_available
        catalog = self._catalog()
        remember_system_identities(catalog)
        try:
            channel_state = channel_apps.State(catalog)
        except Exception:  # snapd or rpm unreadable: list the apps, show none installed
            channel_state = None
        if channel_state is not None:
            _system_identities.update(channel_state.identities())
        system = read_system_apps(catalog)
        siblings = {}
        for entry in catalog.applications:
            listing = getattr(entry, 'luma_system', None)
            if listing is not None:
                siblings.setdefault(listing.package, []).append(entry.name)
        blockers = {}
        apps = [app_for_installed(record) for record in installed]
        # Installed applications remain first-class even when not curated.
        by_source = {record.app_info.get_string('X-Flatpak'): record for record in installed
                     if hasattr(record.app_info, 'get_string')}
        luma_ready = None
        refs = installed_flatpak_refs()
        luma_runtime = runtime_installed('org.projectluma.Platform')
        for entry in catalog.applications:
            listing = getattr(entry, 'luma_system', None)
            state = system.state(listing.package) if listing is not None and system is not None else ''
            existing = by_source.get(entry.source_id) or next(
                (r for r in installed if r.desktop_id in (
                    entry.source_id + '.desktop', listing.desktop_id if listing is not None else None)), None)
            identity = 'catalog:' + entry.id
            if existing is None and channel_state is not None and entry.id in channel_state.installed:
                # A snap or repository package: its desktop file is its identity.
                existing = next((r for r in installed if identity_for_installed(r) == identity), None)
            if existing:
                apps = [a for a in apps if a.app_id != identity_for_installed(existing)]
            base = app_for_installed(existing) if existing else None
            details = metadata.get(entry.source_id, {})
            # Never offer the Flatpak of an app whose image package is here, was
            # removed (Restore brings it back), or is waiting for a restart:
            # the same app would appear twice in the app grid (ADR-031).
            system_owned = state not in ('', 'absent')
            flatpak = (entry.backend == 'flatpak' and entry.repository in _SOURCE_TITLES
                       and not system_owned)
            if flatpak and entry.repository == 'luma' and luma_ready is None:
                luma_ready = luma_remote_available()
            supported = (flatpak and arch in entry.architectures
                         and (entry.repository != 'luma' or bool(luma_ready)))
            channel_kind = channel_apps.kind(entry) if not system_owned else ''
            channel_reason = ''
            if channel_kind:
                supported, channel_reason = channel_apps.available(entry, arch)
            summary = entry.summary or details.get('summary') or entry.qualification_reason
            description = (entry.description or details.get('description')
                           or (entry.summary if listing is not None else '') or entry.qualification_reason)
            homepage = '' if supported else entry.homepage or entry.reference_url
            source_title = _SOURCE_TITLES.get(entry.repository, '') if flatpak else ''
            shown = channel_apps.presentation(entry, installable=supported)
            if channel_kind and entry.channel is not None:
                source_title = entry.channel.title
            system_fields = {}
            if system_owned:
                removable, note = system_removal(system, listing, state, blockers)
                availability = SYSTEM_AVAILABILITY.get(state, '')
                homepage = ''
                source_title = 'Luma'
                system_fields = dict(
                    system_state=state, system_package=listing.package, system_removable=removable,
                    system_note=note, system_busy=system.busy,
                    system_siblings=tuple(name for name in siblings.get(listing.package, ())
                                          if name != entry.name))
            elif supported:
                availability = shown['source_label'] or f'From {source_title}'
            elif channel_reason:
                availability = channel_reason
            elif listing is not None and not flatpak and entry.backend != 'flatpak':
                if system is not None and system.luma:
                    availability = 'Not included in this version of Luma'
                    homepage = ''
                else:
                    availability = 'Available on Luma'
                    homepage = GET_LUMA_URL
                    system_fields = dict(luma_only=True)
            elif flatpak and arch not in entry.architectures:
                availability = 'Not available for this computer’s processor'
            elif flatpak and entry.repository == 'luma':
                availability = 'The Luma app source is not set up on this computer yet'
            else:
                availability = entry.qualification_reason
            developer = entry.developer.name if entry.developer else (
                details.get('developer', '') or entry.publisher)
            release = entry.release
            local = ()
            if flatpak and entry.source_id in refs:
                local = local_permissions(refs[entry.source_id][1])
            if local:
                permissions, computed = permission_rows(local), True
            elif channel_kind == 'snap':
                permissions, computed = channel_apps.snap_permissions(entry), False
            else:
                permissions = permission_rows(entry.permissions, entry.permission_changes)
                computed = False
            releases = ()
            if release is not None:
                from .providers import Release
                releases = (Release(release.version, release.date, release.notes),)
            apps.append(App(
                identity, entry.name, summary,
                description=description,
                developer=developer,
                licence=entry.license or details.get('licence', ''),
                age_rating=entry.age_rating,
                categories=(shelf(entry),),
                icon_name=base.icon_name if base else details.get('icon_name') or 'application-x-executable-symbolic',
                tone=tone(entry.name),
                download_bytes=release.download_bytes if release else 0,
                installed_bytes=release.installed_bytes if release else 0,
                rating=entry.rating.average if entry.rating else 0.0,
                rating_count=entry.rating.count if entry.rating else 0,
                screenshots=tuple(Screenshot(shot.url, shot.caption, shot.sha256, shot.width, shot.height)
                                  for shot in entry.screenshots),
                releases=releases,
                permissions=permissions,
                installable=supported,
                removable=(True if base and channel_kind else
                           base.removable if base and not system_owned else False),
                homepage=homepage,
                availability=(availability if system_owned or not base or channel_kind or flatpak
                              else base.availability),
                slug=entry.id, flatpak_id=entry.identifier, tier=entry.tier if catalog.schema_version >= 4 else '',
                developer_verified=entry.developer.verified if entry.developer else '',
                sign_in=entry.sign_in,
                icon_url=entry.icon.url if entry.icon else '',
                icon_sha256=entry.icon.sha256 if entry.icon else '',
                support_url=entry.support_url, privacy_url=entry.privacy_url,
                source_url=entry.source_url,
                web_url=(entry.homepage if entry.homepage.startswith('https://simplyluma.com/apps/') else ''),
                source_title=source_title or entry.publisher,
                channel_kind=channel_kind if supported else '',
                runtime_note=('The first app from Luma also downloads Luma’s app platform, about 1.9 GB, '
                              'which every Luma app then shares.'
                              if flatpak and entry.repository == 'luma' and supported and not luma_runtime
                              else ''),
                **{key: value for key, value in shown.items() if value},
                installs_total=entry.installs.total if entry.installs else 0,
                permission_changes=change_rows(entry.permission_changes),
                permissions_computed=computed,
                unlisted=entry.visibility == 'unlisted',
                **system_fields))
        identities = {app.app_id for app in apps}
        collections = tuple(
            Collection(item.id, item.name, item.summary,
                       tuple(app_id for app_id in ('catalog:' + member for member in item.applications)
                             if app_id in identities))
            for item in catalog.collections)
        self._last = Catalogue(tuple(apps), CATEGORIES, collections=collections)
        return self._last

    def load_catalogue(self, callback, cancellable=None):
        run_async(self.snapshot, callback, cancellable)

    def search(self, query, callback, cancellable=None):
        def work():
            catalogue = self._last or self.snapshot()
            text = query.casefold()
            return tuple(a for a in catalogue.apps
                         if not a.unlisted and text in (a.name + ' ' + a.summary + ' ' + a.developer).casefold())
        run_async(work, callback, cancellable)

    def app(self, app_id, callback, cancellable=None):
        run_async(lambda: (self._last or self.snapshot()).find(app_id), callback, cancellable)

    def permissions(self, app, callback, cancellable=None):
        """Compute an uninstalled Flatpak app's permissions from its remote metadata."""
        def work():
            import gi
            gi.require_version('Flatpak', '1.0')
            from gi.repository import Flatpak
            from luma_installer.depot_flatpak import (
                SOURCE_REMOTES, SourceUnavailable, configured_installation, metadata_text,
                select_installation, _installations as installations, _refresh)
            _refresh()
            remote, branch = SOURCE_REMOTES.get(app.flatpak_id, ('', ''))
            if not remote:
                return ()
            try:
                installation = (configured_installation(cancellable, remote) if remote == 'flathub'
                                else select_installation(installations(cancellable), cancellable, remote))
                ref = installation.fetch_remote_ref_sync(remote, Flatpak.RefKind.APP, app.flatpak_id,
                                                         architecture(), branch, cancellable)
                data = ref.get_metadata() if hasattr(ref, 'get_metadata') else None
                if data is None or not data.get_size():
                    data = installation.fetch_remote_metadata_sync(remote, ref, cancellable)
            except (SourceUnavailable, GLib.Error) as error:
                raise ProviderError('Permissions unavailable',
                                    hint='Depot could not read this app’s sandbox yet.') from error
            return permission_rows(depot_permissions.from_metadata(metadata_text(data), app.flatpak_id))
        run_async(work, callback, cancellable)


class NativeInstallation:
    def __init__(self):
        self._updates = {}
        self._updates_checked = 0.0
        self._lock = threading.Lock()
        self.force_update_check = False

    def installed(self, callback, cancellable=None, *, force=False):
        def work():
            if not _system_identities:
                try:
                    from luma_installer.depot_catalog import local_catalog
                    remember_system_identities(local_catalog())
                except Exception:
                    pass
            channel_state = self._channel_state()
            if channel_state is not None:
                _system_identities.update(channel_state.identities())
            updates = (self._pending_updates(cancellable, force=True) if force
                       else self._pending_updates(cancellable))
            refs = installed_flatpak_refs()
            from luma_installer.depot_flatpak import APPLICATION_SOURCES
            managed = {source for source in APPLICATION_SOURCES.values()}
            records = []
            seen = set()
            for record in inventory():
                identity = identity_for_installed(record)
                seen.add(identity)
                channel = (channel_state.installed.get(identity.removeprefix('catalog:'))
                           if channel_state is not None and identity.startswith('catalog:') else None)
                if channel is not None:
                    # A snap or repository package Depot installed from its
                    # publisher's channel: Depot removes it itself.
                    records.append(InstalledApp(identity, channel['version'], channel['bytes'],
                                                app=app_for_installed(record), managed=channel['managed']))
                    continue
                source = installed_source(record, refs)
                ref = refs.get(source) if source else None
                pending = self._compared_update(updates.get(source), ref[1] if ref else None)
                is_managed = bool(ref and source in managed and ref[1].get_origin() in _SOURCE_TITLES)
                records.append(InstalledApp(
                    identity,
                    (ref[1].get_appdata_version() or '') if ref else '',
                    ref[1].get_installed_size() if ref else 0,
                    update_version=pending['version'] if pending else '',
                    update_bytes=pending['bytes'] if pending else 0,
                    update_summary=pending['summary'] if pending else '',
                    app=app_for_installed(record),
                    permission_changes=pending['changes'] if pending else (),
                    permissions=permission_rows(local_permissions(ref[1])) if ref else (),
                    managed=is_managed,
                    commit=(ref[1].get_commit() or '') if ref else '',
                    update_commit=pending.get('commit', '') if pending else '',
                    update_channel=(ref[1].get_branch() if ref and ref[1].get_origin() == 'luma' else '')))
            # A catalogued Flatpak that is installed but whose launcher this
            # session cannot see yet (a first per-user install exports into a
            # folder the session adds only at the next sign-in) is installed.
            try:
                from luma_installer.depot_catalog import local_catalog
                catalogued = {entry.source_id: entry for entry in local_catalog().applications
                              if entry.backend == 'flatpak'}
            except Exception:
                catalogued = {}
            for source, (installation, ref) in refs.items():
                entry = catalogued.get(source)
                if entry is None or 'catalog:' + entry.id in seen:
                    continue
                seen.add('catalog:' + entry.id)
                pending = self._compared_update(updates.get(source), ref)
                records.append(InstalledApp(
                    'catalog:' + entry.id, ref.get_appdata_version() or '', ref.get_installed_size(),
                    update_version=pending['version'] if pending else '',
                    update_bytes=pending['bytes'] if pending else 0,
                    update_summary=pending['summary'] if pending else '',
                    app=App('catalog:' + entry.id, entry.name, entry.summary, installable=False),
                    permissions=permission_rows(local_permissions(ref)),
                    permission_changes=pending["changes"] if pending else (),
                    managed=bool(source in managed and ref.get_origin() in _SOURCE_TITLES),
                    commit=ref.get_commit() or '', update_commit=pending.get('commit', '') if pending else '',
                    update_channel=ref.get_branch() if ref.get_origin() == 'luma' else ''))
            if channel_state is not None:
                # Installed, but its launcher is not visible to this session
                # yet (a first snap adds its folder to the session's data
                # directories only at the next sign-in): still installed.
                for identifier, channel in channel_state.installed.items():
                    identity = 'catalog:' + identifier
                    if identity in seen:
                        continue
                    entry = channel_state.entries[identifier]
                    records.append(InstalledApp(identity, channel['version'], channel['bytes'],
                                                app=App(identity, entry.name, entry.summary, installable=False),
                                                managed=channel['managed']))
            return tuple(records)
        run_async(work, callback, cancellable)

    @staticmethod
    def _channel_state():
        try:
            from luma_installer.depot_catalog import local_catalog
            return channel_apps.State(local_catalog())
        except Exception:
            return None

    @staticmethod
    def _channel_entry(app_id):
        from luma_installer.depot_catalog import CatalogError, local_catalog
        try:
            catalog = local_catalog()
        except CatalogError:
            return None
        entry = next((e for e in catalog.applications if app_id == 'catalog:' + e.id), None)
        return entry if entry is not None and channel_apps.kind(entry) else None

    @staticmethod
    def _compared_update(pending, ref):
        # A cached diff belongs to its installed baseline, not the app name.
        if pending is None or ref is None or not pending.get('installed_commit'):
            return None
        return pending if pending['installed_commit'] == ref.get_commit() else None

    def _pending_updates(self, cancellable=None, *, force=False):
        with self._lock:
            fresh = time.monotonic() - self._updates_checked < UPDATE_CHECK_SECONDS
            if fresh and not force and not self.force_update_check:
                return self._updates
            self.force_update_check = False
            try:
                self._updates = self._check_updates(cancellable)
                self._updates_checked = time.monotonic()
            except Exception as error:
                if force:
                    raise ProviderError('Update permissions could not be refreshed.', hint='Retry before updating. Your installed applications are preserved.') from error
                # A failed check keeps what was known and says nothing new.
                pass
            return self._updates

    def _check_updates(self, cancellable=None):
        from luma_installer.depot_flatpak import pending_updates, metadata_text
        from luma_installer.depot_catalog import CatalogError, local_catalog
        try:
            entries = {entry.source_id: entry for entry in local_catalog().applications}
        except CatalogError:
            entries = {}
        found = {}
        for installation, ref in pending_updates(_installations(), cancellable):
            name = ref.get_name()
            try:
                remote = installation.fetch_remote_ref_sync(
                    ref.get_origin(), ref.get_kind(), name, ref.get_arch(), ref.get_branch(), cancellable)
            except GLib.Error:
                continue
            changes = ()
            try:
                data = remote.get_metadata() if hasattr(remote, 'get_metadata') else None
                if data is None or not data.get_size():
                    # That fallback API fetches metadata for the current ref,
                    # not this immutable commit. Do not mix two snapshots.
                    continue
                before = depot_permissions.from_metadata(metadata_text(ref.load_metadata(cancellable)), name, strict=True)
                after = depot_permissions.from_metadata(metadata_text(data), name, strict=True)
                changes = change_rows(depot_permissions.diff(before, after))
            except (GLib.Error, ValueError):
                # Unknown permissions are not permission-free. Never enqueue
                # an update whose sandbox could not be compared.
                continue
            entry = entries.get(name)
            version = entry.release.version if entry and entry.release else ''
            widened = [row.title for row in changes if row.change in ('added', 'widened')]
            summary = (f'Version {version}' if version else 'A newer version is available')
            if widened:
                summary += ' · Asks for more: ' + ', '.join(widened)
            found[name] = {'version': version or (remote.get_commit() or '')[:12] or 'new',
                           'commit': remote.get_commit() or '',
                           'bytes': remote.get_download_size(), 'summary': summary,
                           'changes': changes, 'installed_commit': ref.get_commit() or ''}
        return found

    def free_bytes(self):
        return shutil.disk_usage('/var').free

    def _record(self, app_id):
        record = next((r for r in inventory() if r.desktop_id == app_id or identity_for_installed(r) == app_id), None)
        if record is None:
            raise ProviderError('Application no longer installed', hint='Refresh My apps and try again.')
        return record

    def launch(self, app_id, context):
        try:
            record = self._record(app_id)
        except ProviderError:
            record = None
        if record is not None:
            record.app_info.launch([], context)
            return
        entry = self._channel_entry(app_id)
        state = self._channel_state() if entry is not None else None
        infos = [Gio.DesktopAppInfo.new_from_filename(path)
                 for path in (state.desktop_files.get(entry.id, ()) if state is not None else ())]
        # A snap may export several launchers (NordVPN: its command line and
        # its window); open the one a person would see in the app grid.
        infos = sorted((info for info in infos if info is not None), key=lambda info: not info.should_show())
        for info in infos:
            info.launch([], context)
            return
        flatpak_entry = self._entry(app_id)
        if flatpak_entry is not None:
            found = installed_flatpak_refs().get(flatpak_entry.source_id)
            if found is not None:
                installation, ref = found
                exported = (Path(installation.get_path().get_path()) / 'exports/share/applications'
                            / f'{flatpak_entry.source_id}.desktop')
                info = Gio.DesktopAppInfo.new_from_filename(str(exported)) if exported.is_file() else None
                if info is not None:
                    info.launch([], context)
                    return
        raise ProviderError('Application no longer installed', hint='Refresh My apps and try again.')

    def review_removal(self, app_id):
        # Valet rechecks package ownership and presents its actual removal plan.
        Gio.Subprocess.new(removal_arguments(self._record(app_id)), Gio.SubprocessFlags.NONE)

    @staticmethod
    def _entry(app_id):
        from luma_installer.depot_catalog import CatalogError, local_catalog
        from luma_installer.depot_flatpak import APPLICATION_SOURCES, _refresh
        _refresh()
        try:
            catalog = local_catalog()
        except CatalogError:
            return None
        entry = next((e for e in catalog.applications if app_id == 'catalog:' + e.id), None)
        if (entry is None or entry.backend != 'flatpak'
                or APPLICATION_SOURCES.get(entry.id) != entry.source_id):
            return None
        return entry

    @staticmethod
    def _run(tx, app_id, on_progress, cancellable, stage):
        if tx is None:
            return

        # No second 'ready' handler here: the signal has no accumulator, so a
        # later handler's answer would replace the commit check transaction()
        # connected. The operation list is read once it is final instead.
        def operation(prepared, current, progress):
            refs = [op.get_ref() for op in prepared.get_operations()] or [current.get_ref()]
            index = refs.index(current.get_ref()) if current.get_ref() in refs else 0
            total = max(1, len(refs))

            def changed(value):
                fraction = (index + value.get_progress() / 100) / total
                GLib.idle_add(on_progress, Progress(
                    app_id, min(.99, fraction), value.get_bytes_transferred(),
                    current.get_download_size(), stage if total == 1 else f'{stage} ({index + 1} of {total})'))
            progress.connect('changed', changed)
            changed(progress)
        tx.connect('new-operation', operation)
        tx.run(cancellable)

    @staticmethod
    def _event(entry, kind, version=''):
        def send():
            from luma_installer.depot_counting import send_install_event
            send_install_event(entry.identifier or entry.source_id,
                               version or (entry.release.version if entry.release else ''),
                               architecture(), kind)
        threading.Thread(target=send, daemon=True).start()

    def install(self, app, on_progress, callback, cancellable=None):
        def work():
            from luma_installer.depot_flatpak import (
                SourceUnavailable, installation_for, installed_ref, resolve, transaction)
            import gi
            gi.require_version('Flatpak', '1.0')
            # Source mapping is resolved from validated local identity, never a
            # URL or command supplied by the app card.
            channel = self._channel_entry(app.app_id)
            if channel is not None:
                if channel_apps.kind(channel) == 'deb':
                    channel_apps.run_deb('install', channel, app.app_id, on_progress)
                else:
                    channel_apps.run(channel_apps.kind(channel), 'install', channel, app.app_id, on_progress)
                self._event(channel, 'install')
                return
            entry = self._entry(app.app_id)
            if entry is None:
                raise ProviderError('Not available to install', hint=app.availability)
            try:
                installation, remote = installation_for(entry.source_id, cancellable)
                source = resolve(installation, entry.source_id, architecture(), cancellable,
                                 remote, entry.branch)
                tx = transaction(installation, source, cancellable)
                self._run(tx, app.app_id, on_progress, cancellable, 'Installing')
                installation.drop_caches(cancellable)
                done = installed_ref(installation, source, cancellable)
                if done is None:
                    raise SourceUnavailable('Installation did not produce an installed application.')
            except (SourceUnavailable, GLib.Error) as error:
                raise failure(error, app.app_id, entry.name, 'install') from error
            if tx is not None:
                self._event(entry, 'install', done.get_appdata_version() or '')
            self._updates_checked = 0.0
        run_async(work, callback, cancellable)

    def update(self, app_id, on_progress, callback, cancellable=None, *, expected_commit="", expected_installed_commit=""):
        def work():
            from luma_installer.depot_flatpak import SourceUnavailable, resolve, transaction, require_reviewed_update
            channel = self._channel_entry(app_id)
            if channel is not None:
                if channel_apps.kind(channel) == 'deb':
                    channel_apps.run_deb('update', channel, app_id, on_progress)
                else:
                    channel_apps.run(channel_apps.kind(channel), 'update', channel, app_id, on_progress)
                return
            entry = self._entry(app_id)
            refs = installed_flatpak_refs()
            if entry is None or entry.source_id not in refs:
                raise ProviderError('Updates unavailable',
                                    hint='Depot does not manage this application’s updates.')
            installation, ref = refs[entry.source_id]
            try:
                source = resolve(installation, entry.source_id, ref.get_arch(), cancellable,
                                 ref.get_origin(), ref.get_branch())
                require_reviewed_update(source, expected_commit)
                tx = transaction(installation, source, cancellable, update=True,
                                 expected_installed_commit=expected_installed_commit)
                self._run(tx, app_id, on_progress, cancellable, 'Updating')
            except (SourceUnavailable, GLib.Error) as error:
                raise failure(error, app_id, entry.name, 'update') from error
            if tx is not None:
                self._event(entry, 'update')
            with self._lock:
                self._updates.pop(entry.source_id, None)
        run_async(work, callback, cancellable)

    def review_channel(self, app_id, branch, callback, cancellable=None):
        def work():
            from luma_installer.depot_app_channels import review
            entry = self._entry(app_id)
            pair = installed_flatpak_refs().get(entry.source_id) if entry else None
            if pair is None:
                raise ProviderError('Channel unavailable', hint='Refresh My apps before changing its update channel.')
            installation, ref = pair
            checked = review(installation, ref, branch, cancellable)
            return {'checked': checked, 'branch': checked.source.branch,
                    'permissions': change_rows(checked.permission_changes)}
        run_async(work, callback, cancellable)

    def switch_channel(self, app_id, reviewed, on_progress, callback, cancellable=None):
        def work():
            from luma_installer.depot_app_channels import transaction
            entry = self._entry(app_id)
            pair = installed_flatpak_refs().get(entry.source_id) if entry else None
            checked = reviewed.get('checked')
            if pair is None or checked is None or checked.source.app_id != entry.source_id:
                raise ProviderError('Channel unavailable', hint='Review this app’s update channel again.')
            installation, _ref = pair
            tx = transaction(installation, checked, cancellable)
            self._run(tx, app_id, on_progress, cancellable, 'Changing update channel')
            installation.drop_caches(cancellable)
            from luma_installer.depot_flatpak import installed_ref
            actual = installed_ref(installation, checked.source, cancellable)
            old = next((r for r in installation.list_installed_refs(cancellable)
                        if r.format_ref() == checked.old_ref), None)
            if actual is None or actual.get_commit() != checked.source.commit or old is not None:
                raise ProviderError('Channel change incomplete', hint='Refresh My apps to review the installed channels.')
            with self._lock:
                self._updates.pop(entry.source_id, None)
                self._updates_checked = 0.0
        run_async(work, callback, cancellable)

    def revert(self, app_id, commit, on_progress, callback, cancellable=None):
        """Go back to an earlier build (``commit``) of a Depot-managed Flatpak.

        A per-user installation changes here; a system installation only
        through luma-installer-system, after polkit authorizes it, because only
        root may name a commit there."""
        def work():
            from luma_installer.depot_flatpak import SourceUnavailable, revert_transaction
            entry = self._entry(app_id)
            refs = installed_flatpak_refs()
            if entry is None or entry.source_id not in refs:
                raise ProviderError('Going back unavailable',
                                    hint='Depot does not manage this application.')
            installation, ref = refs[entry.source_id]
            try:
                if installation.get_is_user():
                    tx = revert_transaction(installation, ref, commit, cancellable)
                    self._run(tx, app_id, on_progress, cancellable, 'Going back')
                else:
                    run_revert_helper(entry.source_id, commit, app_id, on_progress, cancellable)
            except (SourceUnavailable, GLib.Error, ProviderError) as error:
                raise failure(error, app_id, entry.name, 'revert') from error
            with self._lock:
                self._updates_checked = 0.0
        run_async(work, callback, cancellable)

    def system_change(self, app, action, on_progress, callback, cancellable=None):
        """Remove (``override-remove``) or restore (``override-reset``) an image app.

        The package name comes from the catalogue this process verified; the
        helper checks it again as root against its own trusted catalogue and
        the system, so nothing the window says is taken on trust.
        """
        def work():
            return run_system_helper(action, app.system_package, app.app_id, on_progress, cancellable)
        run_async(work, callback, cancellable)

    def can_remove(self, app_id):
        channel = self._channel_entry(app_id)
        if channel is not None:
            state = self._channel_state()
            return bool(state and state.installed.get(channel.id, {}).get('managed'))
        entry = self._entry(app_id)
        if entry is None:
            return False
        ref = installed_flatpak_refs().get(entry.source_id)
        return bool(ref and ref[1].get_origin() in _SOURCE_TITLES)

    def remove(self, app_id, *, keep_data, callback, cancellable=None):
        def work():
            from luma_installer.depot_flatpak import SourceUnavailable, removal_transaction
            from .removal_data import backup_private_data, remove_backed_up_data
            channel = self._channel_entry(app_id)
            if channel is not None:
                if not keep_data:
                    raise ProviderError('App data removal is unavailable for this package')
                if channel_apps.kind(channel) == 'deb':
                    channel_apps.run_deb('remove', channel, app_id, lambda _p: False)
                else:
                    channel_apps.run(channel_apps.kind(channel), 'remove', channel, app_id, lambda _p: False)
                self._event(channel, 'remove')
                return
            entry = self._entry(app_id)
            refs = installed_flatpak_refs()
            if entry is None or entry.source_id not in refs:
                raise ProviderError('Removal requires review',
                                    hint='Use Review removal to see the package provider’s plan in Valet.')
            installation, ref = refs[entry.source_id]
            version = ref.get_appdata_version() or ''
            try:
                backup = backup_private_data(ref.get_name(), Path.home()) if not keep_data else None
            except (OSError, ValueError) as error:
                raise ProviderError('Could not back up this app’s data', detail=str(error)) from error
            try:
                tx = removal_transaction(installation, ref, cancellable)
                tx.run(cancellable)
            except (SourceUnavailable, GLib.Error) as error:
                raise failure(error, app_id, entry.name, 'remove') from error
            try:
                remove_backed_up_data(ref.get_name(), Path.home(), backup)
            except (OSError, ValueError) as error:
                self._event(entry, 'remove', version)
                return {'data_removal_error': str(error), 'backup': str(backup)}
            self._event(entry, 'remove', version)
        run_async(work, callback, cancellable)


def failure(error, app_id, name, action):
    """A ProviderError in plain words, with the original text behind Details and in Vitals."""
    from luma_installer.depot_errors import explain, journal
    explanation = explain(error, name=name, action=action)
    journal(explanation, app_id=app_id, name=name, action=action, error=error)
    return ProviderError(explanation.message, hint=explanation.message, detail=explanation.detail)


def parse_helper_line(line):
    """(fraction, text) from a ``progress: 0.40 Preparing the change`` line, else None."""
    if not line.startswith('progress: '):
        return None
    fraction, _, text = line[len('progress: '):].strip().partition(' ')
    try:
        value = float(fraction)
    except ValueError:
        return None
    return max(0.0, min(1.0, value)), text.strip()


def helper_error(code, lines):
    """The ProviderError a failed helper run means for a person."""
    if code in (126, 127):
        # pkexec: the authentication dialog was dismissed or refused.
        return ProviderError('Not authorized', hint='Nothing changed: Luma did not get permission.')
    for line in reversed(lines):
        if line.startswith('refused: '):
            return ProviderError('Refused', hint=line[len('refused: '):])
    for line in reversed(lines):
        if line.startswith('error: '):
            return ProviderError('The change did not finish', hint=line[len('error: '):])
    return ProviderError('The change did not finish', hint='Luma’s system helper stopped without saying why.')


def run_revert_helper(source_id, commit, app_id, on_progress, cancellable=None, *, popen=None):
    import subprocess
    popen = popen or subprocess.Popen
    GLib.idle_add(on_progress, Progress(app_id, 0.02, 0, 0, 'Waiting for permission'))
    process = popen(['pkexec', HELPER, 'flatpak-revert', source_id, commit], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for raw in process.stdout:
        line = raw.strip()
        if not line:
            continue
        lines.append(line)
        parsed = parse_helper_line(line)
        if parsed is not None:
            fraction, text = parsed
            GLib.idle_add(on_progress, Progress(app_id, min(fraction, .99), 0, 0, text or 'Going back'))
    code = process.wait()
    if code != 0:
        error = helper_error(code, lines)
        if any('commit-unavailable' in line for line in lines):
            error = ProviderError('commit-unavailable', hint=error.hint)
        raise error
    return 'unchanged' if 'result: unchanged' in lines else 'reverted'


def run_system_helper(action, package, app_id, on_progress, cancellable=None, *, popen=None):
    import subprocess
    popen = popen or subprocess.Popen
    stage = 'Removing' if action == 'override-remove' else 'Restoring'
    GLib.idle_add(on_progress, Progress(app_id, 0.02, 0, 0, 'Waiting for permission'))
    process = popen(['pkexec', HELPER, action, package], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines = []
    for raw in process.stdout:
        line = raw.strip()
        if not line:
            continue
        lines.append(line)
        parsed = parse_helper_line(line)
        if parsed is not None:
            fraction, text = parsed
            GLib.idle_add(on_progress, Progress(app_id, min(fraction, .99), 0, 0, text or stage))
    code = process.wait()
    if code != 0:
        raise helper_error(code, lines)
    return 'unchanged' if 'result: unchanged' in lines else 'staged'
