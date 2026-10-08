# SPDX-License-Identifier: Apache-2.0
"""Presentation-only metadata from libflatpak's local AppStream cache.

The cache this reads lives under /var, so an image-based system cannot ship
it: a freshly installed computer has nothing here until something downloads
Flathub's metadata, which is what `luma-depot-appstream.service` is for. Until
then `cached_metadata` answers with nothing and every listing draws its
monogram; `cache_directory` says which it is, so the window can watch for the
data landing and redraw rather than waiting to be reopened.
"""
from functools import lru_cache
import gzip
from gi.repository import GLib
from pathlib import Path
import xml.etree.ElementTree as ET
from luma_installer import depot_icons
from luma_installer.depot_flatpak import configured_installation


def displayed_applications():
    """Flatpak identifiers worth reading metadata for: whatever is listed.

    This used to be depot_flatpak.APPLICATIONS, the set of applications
    admitted for installation. Reading a summary and an icon is display, not
    authority, and tying the two together meant an application could be listed
    in the catalogue and still draw as a blank tile because a second hardcoded
    list had not been updated to match. The installation gate is untouched and
    stays where it is: this only decides what is worth parsing.
    """

    from luma_installer.depot_catalog import CatalogError, local_catalog

    # No network here. This runs while parsing a local cache, and the freshest
    # catalogue the device holds is already on disk -- the interface refreshes
    # it on its own load path. Reading it here too would make drawing an icon
    # wait on a fetch. The kept copy is re-verified; the seed is the package's.
    try:
        catalog = local_catalog()
        return frozenset(entry.source_id for entry in catalog.applications
                         if entry.backend == 'flatpak')
    except CatalogError:
        pass

    # No readable catalogue at all. Fall back to the applications admitted for
    # installation, which is what this filter used to be: an icon should never
    # be lost because a catalogue could not be read.
    from luma_installer.depot_flatpak import APPLICATIONS
    return APPLICATIONS


def localized(node, tag):
    values = node.findall(tag)
    for language in GLib.get_language_names():
        normalized = language.split('.')[0].replace('-', '_')
        for value in values:
            if value.get('{http://www.w3.org/XML/1998/namespace}lang', '').replace('-', '_') == normalized:
                return value
    return next((v for v in values if not v.get('{http://www.w3.org/XML/1998/namespace}lang')), None)


def text(node, tag):
    value = localized(node, tag)
    return ''.join(value.itertext()).strip() if value is not None else ''


def _open(path: Path):
    return gzip.open(path, 'rb') if path.suffix == '.gz' else path.open('rb')


def _icon(node, directory: Path, size: int, scale: int) -> str:
    """The best cached icon for one component, or "" when it has none here.

    A *cached* icon is a file name belonging to `directory/icons/<size>`, and
    only ever a bare name: a component naming `../../secret.png` is refused.
    A *local* icon is already an absolute path. A *stock* name is an icon
    theme name, which this cache cannot answer for -- the window asks the
    theme itself, and draws the monogram when the theme says no.
    """
    for candidate in node.findall('icon'):
        kind = candidate.get('type')
        name = (candidate.text or '').strip()
        if kind == 'cached':
            found = depot_icons.appstream_icon(directory, name, size=size, scale=scale)
            if found is not None:
                return str(found)
        elif kind == 'local' and depot_icons.looks_like_a_path(name) and Path(name).is_file():
            return name
    return ''


@lru_cache(maxsize=8)
def _read(filename, stamp, size=128, scale=1):
    path = Path(filename)
    if path.stat().st_size > 64 * 1024 * 1024:
        return {}
    result = {}
    wanted = displayed_applications()
    with _open(path) as stream:
        parser = ET.iterparse(stream, events=('start', 'end'))
        _, root = next(parser)
        for event, node in parser:
            if event != 'end' or node.tag != 'component':
                continue
            # Flathub still lists some older applications under their
            # desktop file name (com.anydesk.Anydesk.desktop); the catalogue
            # names the application ID, so both spellings are the same app.
            identity = node.findtext('id', '')
            if identity not in wanted:
                identity = identity.removesuffix('.desktop')
            if identity in wanted:
                description = localized(node, 'description')
                result[identity] = {
                    'summary': text(node, 'summary'),
                    'description': ' '.join(description.itertext()) if description is not None else '',
                    'developer': text(node, 'developer/name') or text(node, 'developer_name'),
                    'licence': node.findtext('project_license', ''),
                    'icon_name': _icon(node, path.parent, size, scale),
                }
            root.clear()
    return result


def cache_directory(architecture):
    """Flathub's AppStream directory on this computer, or None if there is none."""
    try:
        installation = configured_installation()
        remote = installation.get_remote_by_name('flathub', None)
        directory = Path(remote.get_appstream_dir(architecture).get_path())
    except (OSError, ValueError, GLib.Error):
        return None
    return directory if depot_icons.appstream_document(directory) is not None else None


def cached_metadata(architecture, *, size=128, scale=1):
    try:
        directory = cache_directory(architecture)
        if directory is None:
            return {}
        path = depot_icons.appstream_document(directory)
        return _read(str(path), path.stat().st_mtime_ns, size, scale)
    except (OSError, ValueError, ET.ParseError, GLib.Error, gzip.BadGzipFile):
        return {}


def forget_cache():
    """Read the metadata again: it has just changed on disk."""
    _read.cache_clear()


def request_metadata(architecture, remote='flathub'):
    """Ask libflatpak for a remote's AppStream branch. Blocking; no authority.

    Called when Depot is opened on a computer that has no metadata yet, which
    is the case on a first boot and after one where the network was not up
    when `luma-depot-appstream.timer` fired. It downloads display data and
    installs nothing: the system helper's own polkit action for an AppStream
    update is allowed to an active session without a prompt, and the
    installation gate is untouched and elsewhere.

    Returns True when the metadata is there afterwards.
    """

    try:
        installation = configured_installation(remote=remote)
        installation.update_appstream_sync(remote, architecture, None)
    except (GLib.Error, OSError, ValueError, AttributeError, TypeError):
        return cache_directory(architecture) is not None
    forget_cache()
    return cache_directory(architecture) is not None
