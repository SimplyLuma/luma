# SPDX-License-Identifier: Apache-2.0
"""What an application can reach, computed from its Flatpak sandbox metadata.

ADR-028, section 7. The distribution pipeline runs this mapping on every
release and publishes the result in the catalogue; Depot runs the same mapping
on the metadata of an application that is installed here, and on the metadata
of a pending update, so what it shows for an installed app is what that app's
sandbox actually allows on this computer rather than what a catalogue said.

Nothing here is typed in by a developer, and nothing here grants anything: it
reads a keyfile and describes it.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import re

LEVELS = ('standard', 'sensitive', 'high')
_RANK = {level: index for index, level in enumerate(LEVELS)}
KEY = re.compile(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){0,3}\Z')

#: key -> (title, read-only title or None, detail, default level)
VOCABULARY = {
    'network': ('Uses the internet', None,
                'Can send and receive data over any network connection.', 'standard'),
    'files.portal': ('Opens files you choose', None,
                     'Sees only the files and folders you pick for it.', 'standard'),
    'files.documents': ('Reads and changes your Documents', 'Reads your Documents',
                        'Without asking each time.', 'sensitive'),
    'files.pictures': ('Reads and changes your Pictures', 'Reads your Pictures',
                       'Without asking each time.', 'sensitive'),
    'files.music': ('Reads and changes your Music', 'Reads your Music',
                    'Without asking each time.', 'sensitive'),
    'files.videos': ('Reads and changes your Videos', 'Reads your Videos',
                     'Without asking each time.', 'sensitive'),
    'files.downloads': ('Reads and changes your Downloads', 'Reads your Downloads',
                        'Without asking each time.', 'sensitive'),
    'files.home': ('Reads and changes everything in your home folder',
                   'Reads everything in your home folder',
                   'Including other apps’ settings and documents.', 'high'),
    'files.host': ('Reads and changes all files on this computer',
                   'Reads all files on this computer',
                   'Including system files and connected drives.', 'high'),
    'devices.camera': ('Uses the camera', None,
                       'Can see through any camera connected to this computer.', 'sensitive'),
    'devices.microphone': ('Records audio', None,
                           'Can listen through the microphone and play sound.', 'sensitive'),
    'devices.all': ('Uses any connected device', None,
                    'Cameras, game controllers, security keys and other hardware.', 'high'),
    'background': ('Runs in the background', None,
                   'Keeps working after you close its window.', 'sensitive'),
    'notifications': ('Sends notifications', None,
                      'Can show alerts and banners.', 'standard'),
    'location': ('Knows your location', None,
                 'Can ask this computer where it is.', 'sensitive'),
    'system.bus': ('Talks to system services', None,
                   'Can use services that run for the whole computer.', 'high'),
    'session.bus': ('Talks to other apps', None,
                    'Can exchange messages with other running apps.', 'sensitive'),
    'display.x11': ('Can see other X11 windows', None,
                    'Older apps can read what other X11 apps show and type.', 'high'),
    'sandbox.escape': ('Can run commands outside its sandbox', None,
                       'Anything it runs this way has your full access.', 'high'),
    # Recorded by the distribution workstream in ADR-028 section 7.
    'files.other': ('Reads and changes the named folders', 'Reads the named folders',
                    'Without asking each time.', 'sensitive'),
    'files.removable': ('Reads and changes removable drives', 'Reads removable drives',
                        'USB sticks, memory cards and other connected drives.', 'sensitive'),
    'devices.input': ('Reads input devices', None, 'Game controllers and similar devices.', 'sensitive'),
    'devices.usb': ('Uses USB devices', None, 'Talks directly to connected USB hardware.', 'sensitive'),
    'devices.bluetooth': ('Uses Bluetooth directly', None, 'Without going through the system\u2019s Bluetooth settings.', 'sensitive'),
    'devices.smartcard': ('Uses smart cards', None, 'Security cards and card readers.', 'sensitive'),
    'devices.kvm': ('Runs virtual machines', None, 'Uses the processor\u2019s virtualisation directly.', 'high'),
    'printing': ('Prints directly', None, 'Sends documents to printers without the print dialog.', 'standard'),
    'system.ssh-agent': ('Uses your SSH keys', None, 'Can sign in to servers as you.', 'high'),
    'system.gpg-agent': ('Uses your GnuPG keys', None, 'Can sign and decrypt as you.', 'high'),
    'sandbox.devel': ('Can inspect other processes', None, 'Developer mode: it can debug and trace other apps.', 'high'),
}

_FOLDERS = {
    'xdg-documents': 'files.documents', 'xdg-pictures': 'files.pictures',
    'xdg-music': 'files.music', 'xdg-videos': 'files.videos', 'xdg-download': 'files.downloads',
}
# Depots before this one refuse a whole catalogue whose permission keys carry
# a hyphen, so the catalogue spells these two with an underscore on the wire.
VOCABULARY['system.ssh_agent'] = VOCABULARY['system.ssh-agent']
VOCABULARY['system.gpg_agent'] = VOCABULARY['system.gpg-agent']
_REMOVABLE_ROOTS = ('/media', '/run/media', '/mnt')


@dataclass(frozen=True)
class Grant:
    key: str
    level: str
    read_only: bool = False
    names: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        title, read_title, _detail, _level = VOCABULARY.get(
            self.key, ('Uses a capability this version of Depot cannot describe', None, '', 'high'))
        if self.key in ('system.bus', 'session.bus') and '*' in self.names:
            return 'Talks to every system service' if self.key == 'system.bus' else 'Talks to every app'
        return read_title if (self.read_only and read_title) else title

    @property
    def detail(self) -> str:
        detail = VOCABULARY.get(self.key, ('', None, 'Ask its developer what it is for.', ''))[2]
        named = [name.rsplit(':', 1)[0] if self.key.startswith('files.') and name.endswith((':ro', ':rw')) else name
                 for name in self.names if name != '*']
        if named:
            return ', '.join(named[:3]) + (f' and {len(named) - 3} more' if len(named) > 3 else '')
        return detail


@dataclass(frozen=True)
class Change:
    key: str
    change: str  # added | widened | removed (| narrowed, from a catalogue that says so)
    level: str = 'standard'


class _Collector:
    def __init__(self):
        self.grants: dict[str, Grant] = {}

    def add(self, key, *, level=None, read_only=None, name=None):
        level = level or VOCABULARY[key][3]
        current = self.grants.get(key)
        if current is None:
            self.grants[key] = Grant(key, level, bool(read_only), (name,) if name else ())
            return
        # Read-write wins over read, a higher level over a lower one, and names accumulate.
        self.grants[key] = Grant(
            key, LEVELS[max(_RANK[current.level], _RANK[level])],
            current.read_only and bool(read_only) if read_only is not None else current.read_only,
            tuple(sorted(set(current.names) | ({name} if name else set()))))


def _list(value: str) -> list[str]:
    return [item.strip() for item in (value or '').split(';') if item.strip()]


def from_metadata(text: str, app_id: str = '', *, strict: bool = False) -> tuple[Grant, ...]:
    """Map a Flatpak ``metadata`` keyfile to the section 7 vocabulary.

    This follows the distribution pipeline's reference implementation
    (scripts/depot/flatpak-permissions.py) rule for rule, so what Depot computes
    for an installed app matches what the catalogue published for its release.
    """
    parser = configparser.RawConfigParser(strict=strict, interpolation=None, delimiters=('=',))
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        if strict:
            raise ValueError('This application’s permissions cannot be checked.') from error
        return ()
    if strict and (not parser.has_section('Application')
                   or parser.get('Application', 'name', fallback='') != app_id):
        raise ValueError('This permission metadata does not name the application being updated.')
    found = _Collector()
    context = parser['Context'] if parser.has_section('Context') else {}
    shared = _list(context.get('shared', ''))
    sockets = _list(context.get('sockets', ''))
    devices = _list(context.get('devices', ''))
    features = _list(context.get('features', ''))

    if 'network' in shared:
        found.add('network')
    if 'x11' in sockets and 'fallback-x11' not in sockets:
        found.add('display.x11')
    if 'pulseaudio' in sockets:
        found.add('devices.microphone')
    if 'session-bus' in sockets:
        found.add('session.bus', level='high', name='*')
    if 'system-bus' in sockets:
        found.add('system.bus', name='*')
    for socket, key in (('ssh-auth', 'system.ssh-agent'), ('gpg-agent', 'system.gpg-agent'),
                        ('pcsc', 'devices.smartcard'), ('cups', 'printing')):
        if socket in sockets:
            found.add(key)
    if 'all' in devices:
        found.add('devices.all')
        found.add('devices.camera')
    for device, key in (('input', 'devices.input'), ('usb', 'devices.usb'), ('kvm', 'devices.kvm')):
        if device in devices:
            found.add(key)
    if 'devel' in features:
        found.add('sandbox.devel')
    if 'bluetooth' in features:
        found.add('devices.bluetooth')

    for raw in _list(context.get('filesystems', '')):
        if raw.startswith('!'):
            continue
        path, _, mode = raw.partition(':')
        read_only = mode == 'ro'
        scope = path + (':ro' if read_only else ':rw')
        if path in ('host', 'host-os', 'host-etc', 'host-root', '/'):
            found.add('files.host', read_only=read_only, name=scope)
        elif path in ('home', '~', '~/'):
            found.add('files.home', read_only=read_only, name=scope)
        elif path.split('/', 1)[0] in _FOLDERS:
            found.add(_FOLDERS[path.split('/', 1)[0]], read_only=read_only, name=scope)
        elif path == 'xdg-run/pipewire-0':
            found.add('devices.microphone')
        elif path.startswith(_REMOVABLE_ROOTS):
            found.add('files.removable', read_only=read_only, name=scope)
        else:
            found.add('files.other', read_only=read_only, name=scope)

    if parser.has_section('Session Bus Policy'):
        for name, policy in parser.items('Session Bus Policy'):
            policy = policy.strip()
            if policy in ('none', 'see'):
                continue
            if name == 'org.freedesktop.Flatpak' and policy in ('talk', 'own'):
                found.add('sandbox.escape', name=name)
            elif name.startswith('org.freedesktop.portal.'):
                continue
            elif policy == 'own' and app_id and (name == app_id or name.startswith(app_id + '.')
                                                 or name.startswith('org.mpris.MediaPlayer2.')):
                continue
            elif policy == 'own' and name.startswith('org.mpris.MediaPlayer2.'):
                continue
            elif name == 'org.freedesktop.Notifications' and policy == 'talk':
                found.add('notifications')
            else:
                found.add('session.bus', name=name)
    if parser.has_section('System Bus Policy'):
        for name, policy in parser.items('System Bus Policy'):
            policy = policy.strip()
            if policy in ('none', 'see'):
                continue
            if name == 'org.freedesktop.GeoClue2':
                found.add('location')
            else:
                found.add('system.bus', name=name)
    if 'files.host' not in found.grants and 'files.home' not in found.grants:
        found.add('files.portal')
    return order(found.grants.values())


def order(grants) -> tuple[Grant, ...]:
    keys = list(VOCABULARY)
    return tuple(sorted(grants, key=lambda grant: (
        -_RANK.get(grant.level, 2), keys.index(grant.key) if grant.key in keys else len(keys))))


def names_widen(key: str, before, after) -> bool:
    """Filesystem scope and mode must both remain within an existing grant."""
    if not key.startswith('files.'):
        return bool(set(after) - set(before))

    def scope(value):
        path, _, mode = value.rpartition(':')
        if mode not in ('ro', 'rw'):
            path, mode = value, 'rw'
        if path in ('~', '~/', 'home'):
            path = 'home'
        return path.rstrip('/'), mode

    def covers(old, new):
        op, om = scope(old)
        np, nm = scope(new)
        if om == 'ro' and nm != 'ro':
            return False
        if op == np:
            return True
        # Never infer containment across traversal syntax or similarly named roots.
        return '..' not in np.split('/') and np.startswith(op + '/')

    return any(not any(covers(old, new) for old in before) for new in after)


def diff(before, after) -> tuple[Change, ...]:
    """What an update changes, in the pipeline's terms: added, widened, removed."""
    old = {grant.key: grant for grant in before}
    new = {grant.key: grant for grant in after}
    changes = []
    for key, grant in new.items():
        previous = old.get(key)
        if previous is None:
            changes.append(Change(key, 'added', grant.level))
        elif (_RANK[grant.level] > _RANK[previous.level]
              or (previous.read_only and not grant.read_only)
              or names_widen(key, previous.names, grant.names)):
            changes.append(Change(key, 'widened', grant.level))
    for key, grant in old.items():
        if key not in new:
            changes.append(Change(key, 'removed', grant.level))
    rank = {'added': 0, 'widened': 1, 'removed': 2}
    changes.sort(key=lambda c: (rank[c.change], -_RANK.get(c.level, 0), c.key))
    return tuple(changes)


def widens(changes) -> bool:
    """Whether a person must look first. Gaining the file chooser is never a widening."""
    return any(change.change in ('added', 'widened') and change.key != 'files.portal'
               for change in changes)
