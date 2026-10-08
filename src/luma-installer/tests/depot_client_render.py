#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Render Depot's schema 4 states to PNG, off screen, from labelled fixtures.

Run under a private display and session bus, for example:

    dbus-run-session -- sh -c 'gtk4-broadwayd :17 & sleep 1;
        GDK_BACKEND=broadway BROADWAY_DISPLAY=:17 python3 depot_client_render.py OUT'

Everything it shows is fixture data made here: a catalogue signed with the
RFC 8032 test key, reviews served from a local socket, screenshots taken from
Depot's own placeholder artwork. Nothing is installed, no remote is added, no
real catalogue, Hub or account is contacted, and every XDG directory is a
temporary one. Flathub metadata already cached on the machine may fill in
summaries for listed apps; that is public data.
"""

import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else 'depot-render').resolve()
OUT.mkdir(parents=True, exist_ok=True)
SRC = Path(__file__).resolve().parents[2]
WORK = Path(tempfile.mkdtemp(prefix='depot-render-'))
for name in ('XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
    os.environ[name] = str(WORK / name.lower())
    Path(os.environ[name]).mkdir(parents=True)
os.environ['LUMA_DEPOT_DATA_DIRECTORY'] = str(SRC / 'luma-installer/data')
os.environ['LUMA_DEPOT_CATALOG_KEY'] = str(WORK / 'depot-catalog.pub')
os.environ['LUMA_DEPOT_CATALOG_URL'] = 'https://127.0.0.1:9/catalog-4.json'
os.environ['LUMA_DEPOT_EVENTS_URL'] = 'https://127.0.0.1:9/api/depot/events'
os.environ['LUMA_DEPOT_FLATPAKREPO'] = str(WORK / 'luma.flatpakrepo')
os.environ['LUMA_DEPOT_STYLE_PATH'] = str(SRC / 'luma-depot/data/depot.css')
sys.path[:0] = [str(SRC / 'luma-installer'), str(SRC / 'luma-depot'), str(SRC / 'luma-installer/tests')]

import minisign_signer as signer  # noqa: E402

SHA = {}


def media(name):
    source = SRC / 'luma-depot/data/screenshots' / name
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    folder = Path(os.environ['XDG_CACHE_HOME']) / 'luma/depot/media'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (digest + '.svg')).write_bytes(content)
    return {'url': f'https://dl.simplyluma.com/media/fixture/{name}', 'sha256': digest}


SHOTS = [dict(media(f'placeholder-{n}.svg'), caption=f'Fixture screenshot {n}', width=1600, height=1000)
         for n in (1, 2, 3)]
DEV = {'id': 'dev_projectluma', 'name': 'Project Luma', 'verified': 'organization'}


def luma_app(identifier, name, summary, categories, **extra):
    entry = {
        'id': identifier, 'app_id': f'org.projectluma.{name}', 'source_id': f'org.projectluma.{name}',
        'name': name, 'tier': 'luma', 'visibility': 'public', 'developer': DEV, 'summary': summary,
        'description': summary + ' This listing is a render fixture: the words, numbers and '
                                 'reviews on this page were written for the test.',
        'categories': categories, 'backend': 'flatpak', 'repository': 'luma', 'branch': 'stable',
        'architectures': ['x86_64', 'aarch64'], 'distribution': 'publisher', 'qualification': 'admitted',
        'qualification_reason': 'Fixture', 'reference_url': f'https://simplyluma.com/apps/{identifier}',
        'homepage': f'https://simplyluma.com/apps/{identifier}', 'license': 'Apache-2.0',
        'release': {'version': '0.1.0', 'date': '2026-09-30', 'download_bytes': 41000000,
                    'installed_bytes': 120000000, 'notes': 'First release.'},
        'permissions': [{'key': 'files.portal', 'level': 'standard'}], 'sign_in': 'none',
        'support_url': 'https://hub.simplyluma.com/forum', 'privacy_url': 'https://simplyluma.com/privacy',
    }
    entry.update(extra)
    return entry


def catalogue():
    seed = json.loads((SRC / 'luma-installer/data/depot-catalog-4.json').read_text())
    seed['generated_at'] = '2026-09-30T18:00:00Z'
    seed['applications'] += [
        luma_app('canvas', 'Canvas', 'Design pages, posters and social graphics.', ['create'],
                 screenshots=SHOTS, rating={'average': 4.6, 'count': 128},
                 installs={'total': 5231, 'last_30_days': 812},
                 source_url='https://github.com/ProjectLuma/Designer',
                 permissions=[{'key': 'files.documents', 'level': 'sensitive'},
                              {'key': 'files.pictures', 'level': 'sensitive', 'access': 'read'},
                              {'key': 'network', 'level': 'standard'},
                              {'key': 'files.portal', 'level': 'standard'}]),
        luma_app('write', 'Write', 'Documents that look right everywhere.', ['office'],
                 rating={'average': 5.0, 'count': 2}, sign_in='luma',
                 permissions=[{'key': 'files.documents', 'level': 'sensitive'},
                              {'key': 'files.portal', 'level': 'standard'}]),
        luma_app('grid', 'Grid', 'Spreadsheets for numbers you care about.', ['office']),
        luma_app('stage', 'Stage', 'Presentations with nothing in the way.', ['office']),
        luma_app('reel', 'Reel', 'Cut video on a timeline that stays out of your way.', ['video'],
                 tier='verified', developer={'id': 'dev_fixture', 'name': 'Fixture Films', 'verified': 'individual'},
                 sign_in='required-third-party', rating={'average': 3.0, 'count': 2},
                 permissions=[{'key': 'devices.microphone', 'level': 'sensitive'},
                              {'key': 'files.videos', 'level': 'sensitive'},
                              {'key': 'network', 'level': 'standard'}],
                 permission_changes=[{'key': 'devices.microphone', 'change': 'added', 'level': 'sensitive'}]),
        luma_app('session', 'Session', 'Record and arrange music.', ['audio']),
    ]
    return json.dumps(seed).encode()


CONTENT = catalogue()
(WORK / 'depot-catalog.pub').write_bytes(signer.public_key_file())
cache = Path(os.environ['XDG_CACHE_HOME']) / 'luma/depot'
cache.mkdir(parents=True, exist_ok=True)
(cache / 'catalog-4.json.minisig').write_bytes(signer.signature_file(CONTENT))
(cache / 'catalog-4.json').write_bytes(CONTENT)
(WORK / 'luma.flatpakrepo').write_text(
    '[Flatpak Repo]\nTitle=Luma (render fixture)\nUrl=https://dl.simplyluma.com/repo/\n'
    'GPGKey=' + 'A' * 64 + '\n')

REVIEWS = {
    'reel': {'rating': {'average': 3.0, 'count': 2}, 'reviews': [
        {'id': 'r1', 'rating': 3, 'title': 'Promising', 'body': 'Fixture review: exports are slow for now.',
         'author': {'name': 'Fixture reviewer'}, 'version': '0.1.0', 'installed_on_luma': True,
         'created_at': '2026-09-30T12:00:00Z'}]},
    'canvas': {'rating': {'average': 4.6, 'count': 128}, 'next_cursor': 'fixture:2', 'reviews': [
        {'id': 'f1', 'rating': 5, 'title': 'Finally a layout tool that feels native',
         'body': 'Guides snap where I expect and the export sheet remembers my sizes. '
                 'Fixture review written for this render.',
         'author': {'name': 'Fixture reviewer'}, 'version': '0.1.0', 'installed_on_luma': True,
         'created_at': '2026-09-30T12:00:00Z',
         'reply': {'body': 'Thank you. Remembered export sizes came from notes like this one.',
                   'developer': {'name': 'Project Luma'}, 'created_at': '2026-10-01T09:00:00Z'}},
        {'id': 'f2', 'rating': 4, 'title': 'Great, wants more templates',
         'body': 'Everything I made looked good first time. More poster templates, please.',
         'author': {'name': 'Second fixture reviewer'}, 'version': '0.1.0', 'installed_on_luma': False,
         'created_at': '2026-09-29T18:30:00Z'},
    ]},
}


class Hub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        slug = self.path.split('/api/depot/apps/', 1)[-1].split('/', 1)[0]
        body = json.dumps(REVIEWS.get(slug, {'rating': {'average': 0, 'count': 0}, 'reviews': []})).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Hub)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ['LUMA_DEPOT_HUB_URL'] = f'http://127.0.0.1:{server.server_address[1]}'

import gi  # noqa: E402
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402
from unittest import mock  # noqa: E402

from luma_installer import depot_reviews  # noqa: E402
depot_reviews.HUB_URL = os.environ['LUMA_DEPOT_HUB_URL']
from luma_depot import native, window as depot_window  # noqa: E402
from luma_depot.providers import InstalledApp, Permission  # noqa: E402
from gi.repository import Gio  # noqa: E402


def settle(seconds=1.2):
    until = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < until:
        while context.iteration(False):
            pass
        time.sleep(0.01)


#: 2 renders the same layout at twice the pixels, the way a HiDPI screen shows it.
SCALE = max(1, min(4, int(os.environ.get('DEPOT_RENDER_SCALE', '1'))))


def scroll(win, fraction):
    """Scroll every scrolled window in ``win`` to ``fraction`` of its length."""
    def walk(widget):
        while widget is not None:
            if isinstance(widget, Gtk.ScrolledWindow):
                adjustment = widget.get_vadjustment()
                settle(0.3)
                adjustment.set_value((adjustment.get_upper() - adjustment.get_page_size()) * fraction)
            walk(widget.get_first_child())
            widget = widget.get_next_sibling()
    walk(win.get_first_child())


def capture(win, name):
    # A frame may not be ready yet on a slow display server: wait for one.
    for _attempt in range(20):
        settle(0.6)
        probe = Gtk.Snapshot.new()
        first = win.get_first_child()
        Gtk.WidgetPaintable.new(first).snapshot(probe, first.get_width(), first.get_height())
        if probe.to_node() is not None:
            break
    # The toplevel draws nothing through a paintable; its first child is the
    # window's own content host, which also carries any open dialog.
    root = win.get_first_child()
    width, height = root.get_width(), root.get_height()
    snapshot = Gtk.Snapshot.new()
    if SCALE != 1:
        snapshot.scale(SCALE, SCALE)
    Gtk.WidgetPaintable.new(root).snapshot(snapshot, width, height)
    node = snapshot.to_node()
    if node is None:
        raise SystemExit(f'nothing drew for {name}')
    texture = win.get_renderer().render_texture(node, None)
    path = OUT / f'{name}.png'
    texture.save_to_png(str(path))
    print(f'rendered {path.name} {width * SCALE}x{height * SCALE}')


class Provisioning:
    """The banner's view of a first-boot run, as it looks halfway through."""
    active = True
    visible = True

    def headline(self):
        return 'Installing the apps you chose · 2 of 3'

    def detail(self):
        return 'Now installing Grid. You can keep using this computer.'

    def dismiss(self):
        self.visible = False


patches = [
    mock.patch.object(native, 'inventory', return_value=()),
    mock.patch.object(native, 'installed_flatpak_refs', return_value={}),
    mock.patch.object(native.NativeInstallation, '_check_updates', return_value={}),
    mock.patch('luma_installer.depot_flatpak.luma_remote_available', return_value=True),
]
for patch in patches:
    patch.start()

scheme = os.environ.get('DEPOT_RENDER_SCHEME', 'light')
suffix = '' if scheme == 'light' else '-dark'
if scheme == 'dark':
    # The kit asks the appearance service which surface a person chose, and only
    # falls back to the desktop when nobody has. Say both, the way a dark Luma
    # session does: without the choice, libadwaita goes dark while the kit's
    # tokens stay light, and the render is neither.
    os.environ['GSETTINGS_BACKEND'] = 'memory'
    from gi.repository import Gio  # noqa: E402
    Gio.Settings.new('org.gnome.desktop.interface').set_string('color-scheme', 'prefer-dark')
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup('org.project_luma.shell-state', True) is not None:
        Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment', 'dark')
app = depot_window.DepotApplication()
app.register(None)
Adw.StyleManager.get_default().set_color_scheme(
    Adw.ColorScheme.FORCE_DARK if scheme == 'dark' else Adw.ColorScheme.FORCE_LIGHT)


def new_window(width, height):
    win = depot_window.DepotWindow(app)
    win.set_default_size(width, height)
    win.present()
    settle(2.0)
    assert win.catalogue is not None and win.catalogue_error == '', win.catalogue_error
    return win


def installed(win, identifier, **fields):
    app_entry = win.catalogue.find('catalog:' + identifier)
    win.installed['catalog:' + identifier] = InstalledApp('catalog:' + identifier, fields.pop('version', '0.1.0'),
                                                          120000000, app=app_entry, **fields)


only = set(sys.argv[2:])


def wanted(name):
    return not only or name in only


if wanted('home'):
    win = new_window(1280, 1180)
    win.go('home')
    capture(win, 'home-collections-1280' + suffix)
    win.provisioning = Provisioning()
    win.render()
    win.content_scroll.get_vadjustment().set_value(0)
    capture(win, 'home-first-boot-banner-1280' + suffix)
    win.close()

if wanted('app'):
    win = new_window(1024, 2100)
    win.go('app', 'catalog:canvas')
    settle(1.5)
    capture(win, 'app-canvas-luma-tier-1024' + suffix)
    win.close()

if wanted('narrow'):
    win = new_window(360, 2300)
    win.go('app', 'catalog:canvas')
    settle(1.5)
    capture(win, 'app-canvas-360' + suffix)
    win.close()

if wanted('review') and not suffix:
    with mock.patch.object(depot_reviews, 'device_token', return_value='A' * 43), \
            mock.patch('luma_depot.reviews.is_enrolled', return_value=True):
        win = new_window(1024, 1000)
        win.go('app', 'catalog:canvas')
        settle(1.2)
        from luma_depot.reviews import ReviewComposer
        composer = ReviewComposer(win, win.catalogue.find('catalog:canvas'), win.reviews.client, None,
                                  '0.1.0', True, on_saved=lambda: None)
        composer._rate(4)
        composer.title.set_text('Finally a layout tool that feels native')
        composer.text.get_buffer().set_text('Guides snap where I expect. Fixture text for the render.')
        composer.present(win)
        capture(win, 'review-composer-1024')
        win.close()

if wanted('update'):
    win = new_window(1024, 1500)
    installed(win, 'reel', update_version='0.2.0', update_bytes=18000000, managed=True,
              update_summary='Version 0.2.0 · Asks for more: Uses the camera, Records audio',
              permission_changes=(
                  Permission('devices.camera', 'Uses the camera', 'Can see through any camera connected to this computer.',
                             True, 'sensitive', 'added'),
                  Permission('files.videos', 'Reads and changes your Videos', 'Without asking each time.',
                             True, 'sensitive', 'widened'),
                  Permission('network', 'Uses the internet', 'Can send and receive data over any network connection.',
                             False, 'standard', 'removed')),
              permissions=(
                  Permission('devices.microphone', 'Records audio', 'Can listen through the microphone and play sound.',
                             True, 'sensitive'),
                  Permission('files.videos', 'Reads your Videos', 'Without asking each time.', True, 'sensitive')))
    installed(win, 'canvas', update_version='0.1.1', update_bytes=3000000, managed=True,
              update_summary='Version 0.1.1')
    win.go('app', 'catalog:reel')
    settle(1.2)
    capture(win, 'app-reel-update-asks-for-more-1024' + suffix)
    from luma_installer import depot_system_update as su
    from luma_depot.system_updates import FirmwareUpdate
    win.go('updates')
    capture(win, 'updates-service-not-installed-1024' + suffix)
    win.system.state = su.from_values({
        'State': 'staged', 'Channel': 'stable', 'BootedVersion': '1.0.0', 'StagedVersion': '1.0.1',
        'AvailableVersion': '1.0.1', 'NotesUrl': 'https://simplyluma.com/releases/1.0.1',
        'AvailableSummary': 'Fixture release: faster Alt+Tab, sharper previews, and a dock that matches the spec.',
        'DownloadBytes': 214000000, 'Progress': 1.0, 'RollbackAvailable': True,
        'LastCheck': int(time.time()) - 7200}, 'dbus')
    win.firmware.available = True
    win.firmware.updates = (FirmwareUpdate(
        'fixture-device', 'System Firmware', 'Fixture Vendor', '1.21', '1.23',
        'Fixture firmware release for the render.', 'Improves battery charging.\nFixes resume from sleep.',
        'medium', True, True),)
    win.render()
    capture(win, 'updates-system-firmware-apps-1024' + suffix)
    win.system.state = su.from_values({
        'State': 'downloading', 'Channel': 'beta', 'BootedVersion': '1.0.0-beta.3',
        'AvailableVersion': '1.0.0-beta.4', 'Importance': 'security', 'DownloadBytes': 118000000,
        'Progress': 0.42, 'AvailableSummary': 'Fixture security release.'}, 'dbus')
    win.firmware.updates = ()
    win.render()
    capture(win, 'updates-security-downloading-1024' + suffix)
    win.system.state = su.from_values({
        'State': 'idle', 'Channel': 'stable', 'BootedVersion': '1.0.1', 'LastCheck': int(time.time()) - 600,
        'RollbackAvailable': True, 'AvailableChannels': ['stable', 'beta', 'nightly']}, 'dbus')
    win.installed.clear()
    win.render()
    capture(win, 'updates-up-to-date-1024' + suffix)
    win.system.state = su.from_values({
        'State': 'idle', 'Channel': 'stable', 'BootedVersion': '1.0.1', 'RolledBackVersion': '1.0.2',
        'LastCheck': int(time.time()) - 600, 'WaitingVersion': '1.0.3'}, 'dbus')
    win.render()
    capture(win, 'updates-rolled-back-1024' + suffix)
    win.go('preferences')
    capture(win, 'update-preferences-1024' + suffix)
    win.close()

if wanted('updates'):
    # Every state the Updates page can be in, so none of them is ever drawn
    # for the first time on somebody's computer. Fixture values only.
    from luma_installer import depot_system_update as su
    now = int(time.time())
    HOST = 'https://dl.fixture.example/os/repo'
    GRAPH = 'https://dl.fixture.example/os/graph/stable.json'
    KEY = 'A1B2C3D4E5F60718'
    SUMMARY = ('Fixture release: a quieter dock, sharper window previews, and a fix for external '
               'displays waking from sleep.')

    def base(**extra):
        values = {
            'Channel': 'stable', 'BootedVersion': '1.0.0', 'Managed': True, 'LastCheck': now - 2400,
            'LastCheckAttempt': now - 2400, 'LastCheckReason': 'up-to-date', 'AutomaticDownload': True,
            'RepositoryUrl': HOST, 'GraphUrl': GRAPH, 'SignatureVerified': True, 'SigningKeyId': KEY,
            'AvailableChannels': ['stable', 'beta'], 'RollbackAvailable': True,
            'NotesUrl': 'https://simplyluma.com/releases/1.0.1',
        }
        values.update(extra)
        return su.from_values(values, 'dbus')

    states = [
        ('up-to-date', base(State='idle', BootedVersion='1.0.1', NotesUrl='')),
        ('checking', base(State='checking', LastCheckReason='')),
        ('available', base(State='available', AvailableVersion='1.0.1', AvailableSummary=SUMMARY,
                           DownloadBytes=214000000, LastCheckReason='newest-eligible')),
        ('available-metered', base(State='available', AvailableVersion='1.0.1', AvailableSummary=SUMMARY,
                                   DownloadBytes=214000000, Metered=True, LastCheckReason='newest-eligible')),
        ('downloading', base(State='downloading', AvailableVersion='1.0.1', AvailableSummary=SUMMARY,
                             DownloadBytes=214000000, Progress=0.42, LastCheckReason='newest-eligible')),
        ('staged', base(State='staged', StagedVersion='1.0.1', AvailableVersion='1.0.1',
                        AvailableSummary=SUMMARY, DownloadBytes=214000000, Progress=1.0,
                        LastCheckReason='newest-eligible')),
        ('staged-security', base(State='staged', StagedVersion='1.0.1', AvailableVersion='1.0.1',
                                 Importance='security', AvailableSummary='Fixture security release.',
                                 Progress=1.0, LastCheckReason='newest-eligible')),
        ('ignored', base(State='staged', StagedVersion='1.0.1', AvailableVersion='1.0.1',
                         AvailableSummary=SUMMARY, Progress=1.0, IgnoredVersion='1.0.1',
                         LastCheckReason='newest-eligible')),
        ('download-off', base(State='available', AvailableVersion='1.0.1', AvailableSummary=SUMMARY,
                              DownloadBytes=214000000, AutomaticDownload=False,
                              LastCheckReason='newest-eligible')),
        ('error', base(State='error', LastCheck=0, LastCheckReason='network',
                       LastError='Luma couldn’t reach the update server.', SignatureVerified=False)),
        ('rolled-back', base(State='idle', RolledBackVersion='1.0.2', RolledBackAt=now - 600,
                             WaitingVersion='1.0.3', LastCheckReason='rollout-not-reached')),
        ('barrier-blocked', base(State='barrier-blocked', WaitingVersion='1.0.1',
                                 LastCheckReason='barrier-blocked')),
        ('preview-enrolled', base(State='idle', Channel='beta', BootedVersion='1.1.0-beta.2',
                                  PreviewEnrolled=True, AvailableChannels=['stable', 'beta', 'nightly'],
                                  LastCheckReason='up-to-date',
                                  RepositoryUrl='https://dl.fixture.example/os/preview/<credential>/repo')),
        ('preview-hub-nightly', base(State='idle', Channel='nightly', BootedVersion='1.1.0-nightly.20260917.1',
                                     PreviewEnrolled=True, PreviewSource='hub',
                                     AvailableChannels=['stable', 'beta', 'nightly'], LastCheckReason='up-to-date',
                                     RepositoryUrl='https://dl.fixture.example/os/preview/<credential>/repo')),
        ('preview-staff-media-nightly', base(State='available', Channel='nightly',
                                       BootedVersion='1.0.0-nightly.20260915.9',
                                       AvailableVersion='1.0.0-nightly.20260917.1', AvailableSummary=SUMMARY,
                                       PreviewEnrolled=True, PreviewSource='staff-media',
                                       AvailableChannels=['stable', 'beta', 'nightly'],
                                       LastCheckReason='newest-eligible',
                                       RepositoryUrl='https://dl.fixture.example/os/preview/<credential>/repo')),
        ('switching-to-nightly', base(State='idle', Channel='stable', LastCheckReason='up-to-date')),
        ('public-nightly', base(State='available', Channel='nightly', BootedVersion='1.0.0-nightly.20260916.3',
                                AvailableVersion='1.0.0-nightly.20260917.1', AvailableSummary=SUMMARY,
                                AvailableChannels=['stable', 'beta', 'nightly'], LastCheckReason='newest-eligible')),
        # Degraded states are drawn too: each must be as finished as the happy
        # path, with the reason and the way forward, never a bare "unavailable".
        ('unmanaged', base(State='idle', Managed=False, Channel='', Adoptable=True,
                           UnmanagedReason='other-origin', LastCheck=0, SignatureVerified=False)),
        ('unmanaged-no-remote', base(State='idle', Managed=False, Channel='', Adoptable=False,
                                     UnmanagedReason='no-remote', RepositoryUrl='', GraphUrl='',
                                     LastCheck=0, SignatureVerified=False)),
        ('unmanaged-no-key', base(State='idle', Managed=False, Channel='', Adoptable=False,
                                  UnmanagedReason='no-key', LastCheck=0, SignatureVerified=False)),
        ('unmanaged-no-image-system', base(State='idle', Managed=False, Channel='', Adoptable=False,
                                           UnmanagedReason='no-image-system', RepositoryUrl='',
                                           LastCheck=0, SignatureVerified=False)),
        ('adopting', base(State='downloading', Managed=False, Channel='', Adoptable=True,
                          UnmanagedReason='other-origin', AvailableVersion='1.0.1',
                          AvailableSummary=SUMMARY, DownloadBytes=214000000, Progress=0.18)),
        ('not-installed', su.NOT_INSTALLED),
    ]
    win = new_window(1024, 1500)
    win.go('updates')
    for name, state in states:
        win.system.state = state
        win.system.busy = 'Check' if name == 'checking' else 'SetChannel' if name == 'switching-to-nightly' else ''
        win.system.busy_channel = 'nightly' if name == 'switching-to-nightly' else ''
        win.pending_channel = 'beta' if name == 'unmanaged' else 'stable'
        win.firmware.available = False
        win.installed.clear()
        win.render()
        capture(win, f'updates-{name}-1024' + suffix)
    win.system.state = states[5][1]
    win.go('preferences')
    capture(win, 'updates-preferences-apps-1024' + suffix)
    win.close()
    win = new_window(420, 1700)
    win.system.state = states[2][1]
    win.go('updates')
    capture(win, 'updates-available-420' + suffix)
    win.close()

if wanted('attention'):
    # The Updates page, attention first: nothing pending, a system update ready,
    # a security hardware update, an app update that failed, and all of it at once.
    from luma_installer import depot_app_history as history
    from luma_installer import depot_system_update as su
    from luma_depot.system_updates import FirmwareUpdate
    from luma_depot.window import Job
    now = int(time.time())
    for existing in (history.state_path(),):
        existing.unlink(missing_ok=True)
    history.record(history.Entry('catalog:canvas', 'Canvas', '0.1.0', '0.1.1', now - 3 * 3600, automatic=True,
                                 from_commit='a' * 64, to_commit='b' * 64,
                                 notes='Smoother pen strokes and a faster export to PDF.'))
    history.record(history.Entry('catalog:reel', 'Reel', '0.1.9', '0.2.0', now - 26 * 3600,
                                 from_commit='c' * 64, to_commit='d' * 64))
    history.record(history.Entry('catalog:write', 'Write', '1.3.0', '1.4.0', now - 5 * 86400, automatic=True))

    def os_state(**extra):
        values = {'State': 'idle', 'Channel': 'nightly', 'BootedVersion': '1.0.0-nightly.20260917.1',
                  'Managed': True, 'LastCheck': now - 1500, 'LastCheckAttempt': now - 1500,
                  'LastCheckReason': 'up-to-date', 'AutomaticDownload': True,
                  'RepositoryUrl': 'https://dl.fixture.example/os/repo', 'SignatureVerified': True,
                  'SigningKeyId': 'A1B2C3D4E5F60718', 'AvailableChannels': ['stable', 'beta', 'nightly'],
                  'RollbackAvailable': True}
        values.update(extra)
        return su.from_values(values, 'dbus')

    dbx = FirmwareUpdate('fixture-dbx', 'UEFI dbx', 'Linux Foundation', '20230314', '20250507',
                         'UEFI Secure Boot Forbidden Signature Database', 'Adds hashes of vulnerable boot loaders.',
                         'high', True, True, protocols=('org.uefi.dbx',), plugin='uefi_dbx')
    modem = FirmwareUpdate('fixture-modem', 'Fibocom L850-GL', 'Fibocom', '18500.5001.00.05.27.12',
                           '18500.5001.00.05.27.16', 'Modem firmware', 'Improves LTE band handover.',
                           'medium', False, True, icons=('modem',), plugin='modem_manager')

    def show(win, name, *, fractions=(0.0,)):
        win.render()
        for fraction in fractions:
            scroll(win, fraction)
            capture(win, f'updates-attention-{name}{"" if not fraction else "-" + str(int(fraction * 100))}-1024' + suffix)

    win = new_window(1024, 1500)
    win.go('updates')
    win.firmware.available = True
    win.firmware.updates = ()
    installed(win, 'canvas', version='0.1.1', managed=True, commit='b' * 64)
    installed(win, 'reel', version='0.2.0', managed=True, commit='d' * 64)
    win.system.state = os_state()
    show(win, 'nothing-pending', fractions=(0.0, 0.5))
    win.system.state = os_state(State='staged', StagedVersion='1.0.0-nightly.20260918.1',
                                AvailableVersion='1.0.0-nightly.20260918.1', Progress=1.0,
                                AvailableSummary='Fixture nightly: a calmer Updates page.')
    show(win, 'os-ready')
    win.system.state = os_state()
    win.firmware.updates = (dbx, modem)
    show(win, 'firmware-security')
    win.firmware.updates = ()
    installed(win, 'canvas', version='0.1.1', managed=True, commit='b' * 64,
              update_version='0.1.2', update_bytes=3000000, update_summary='Version 0.1.2')
    win.jobs['catalog:canvas'] = Job('catalog:canvas', 'update', Gio.Cancellable(),
                                     failed='Canvas couldn\u2019t update because its app source could not be '
                                            'reached. Check your connection and try again.',
                                     detail='flatpak-error-quark: While fetching https://dl.fixture.example: timeout (4)')
    show(win, 'app-failed')
    win.system.state = os_state(State='available', AvailableVersion='1.0.0-nightly.20260918.1',
                                DownloadBytes=214000000, Metered=True,
                                AvailableSummary='Fixture nightly: a calmer Updates page.')
    win.firmware.updates = (dbx,)
    h = history.load()
    history.pause(h, h.entries[1])
    win._forget_history_cache()
    show(win, 'mixed', fractions=(0.0, 0.45))
    win.close()

if wanted('hardware'):
    # Every state of a hardware update: what it still needs before Install, a
    # failure before anything was written (Try Again, Details), one while
    # writing, and offers Luma holds back. Fixture values only; the modem is
    # the kind that failed on the owner's ThinkPad.
    from luma_installer import depot_firmware_safety as safety
    from luma_depot.system_updates import FirmwareUpdate
    modem = FirmwareUpdate('fixture-modem', 'RM520N-GL', 'quectel', 'RM520NGLAAR03A03M4G_04.211.04.211',
                           'RM520NGLAAR03A03M4G_04.220.04.220', 'Firmware for the modem',
                           'Performance improvements.\nStability improvements.', 'high', False, True,
                           protocols=('com.qualcomm.firehose',), icons=('modem',), plugin='modem_manager',
                           guids=('595c3b9c-1f4a-541d-a14b-5af13b24c988',), vendor_ids=('PCI:0x1EAC',))
    system = FirmwareUpdate('fixture-system', 'System Firmware', 'LENOVO', '1.20', '1.22', 'System firmware',
                            'Fixes resume from sleep.', 'medium', True, True, protocols=('org.uefi.capsule',),
                            icons=('computer',), plugin='uefi_capsule',
                            guids=('230c8b18-8d9b-53ec-838b-6cfc0383493a',), vendor_ids=('DMI:LENOVO',),
                            size=32 * 1024 * 1024)
    mouse = FirmwareUpdate('fixture-mouse', 'MX Master 3', 'Logitech', 'RQM 65.01.B0010', 'RQM 65.01.B0014',
                           'Mouse firmware', 'Improves scrolling.', 'low', False, False, icons=('input-mouse',),
                           plugin='logitech_hidpp', guids=('11111111-2222-3333-4444-555555555555',),
                           vendor_ids=('USB:0x046D',), removable=True)
    FIXED = {'fwupd_build': '2.1.7-1.luma.1.fc44', 'daemon_ok': True, 'on_battery': False,
             'battery_level': 95, 'uefi': True, 'secure_boot': True, 'signed_loader': True,
             'esp_free': 600 << 20, 'metadata_age_days': 2}
    OWNER_LOG = ('2026-09-18T21:39:30-05:00 fwupd[21560]: FuCommon fu_bytes_set_contents: assertion '
                 "'bytes != NULL' failed\n2026-09-18T21:39:30-05:00 fwupd[21560]: FuPlugin unset plugin "
                 'error in modem_manager(detach)')

    def hardware(win, name, updates, *, host=FIXED, facts=None, failures=None, installing='', phase='',
                 progress=0.0, expand=False):
        firmware = win.firmware
        firmware.available = True
        firmware.problem = ''
        firmware.updates = tuple(updates)
        firmware.host = dict(host)
        firmware.facts = dict(facts or {})
        firmware.failures = dict(failures or {})
        firmware.installing, firmware.phase, firmware.progress = installing, phase, progress
        win.installed.clear()
        win.render()
        if expand:
            def walk(widget):
                while widget is not None:
                    if isinstance(widget, Gtk.Expander) and widget.get_label() == 'Details' and \
                            widget.get_parent() is not None and widget.get_parent().has_css_class('dp-fw-failure'):
                        widget.set_expanded(True)
                    walk(widget.get_first_child())
                    widget = widget.get_next_sibling()
            walk(win.get_first_child())
        capture(win, f'hardware-{name}-{win.get_default_size()[0]}' + suffix)

    win = new_window(1024, 1100)
    win.go('updates')
    win.system.state = None
    settle(1.0)
    win.firmware.attempts = safety.Attempts.load(Path(os.environ['XDG_STATE_HOME']) / 'fixture-attempts.json')
    # Before Install: on battery, a full startup partition, old update information.
    hardware(win, 'preflight-unmet', (modem, system, mouse),
             host=dict(FIXED, on_battery=True, battery_level=22, battery_threshold=30, metadata_age_days=41),
             facts={'fixture-modem': {'modem_connected': True},
                    'fixture-system': {'esp_needed': 64 << 20},
                    'fixture-mouse': {'problems': ['unreachable']}})
    win.firmware.host['esp_free'] = 18 << 20
    hardware(win, 'preflight-unmet-esp', (system,),
             host=dict(FIXED, esp_free=18 << 20), facts={'fixture-system': {'esp_needed': 64 << 20}})
    hardware(win, 'ready-modem-connected', (modem,), facts={'fixture-modem': {'modem_connected': True}})
    hardware(win, 'installing', (modem,), installing='fixture-modem', phase='write', progress=0.46)
    before = safety.explain(0, 'failed to detach: failed to detach using modem_manager: unspecified error',
                            phase='detach', device_name='your mobile broadband modem', log=OWNER_LOG)
    hardware(win, 'failed-before', (modem,), failures={'fixture-modem': before})
    hardware(win, 'failed-before-details', (modem,), failures={'fixture-modem': before}, expand=True)
    during = safety.explain(19, 'failed to write-firmware: timed out', phase='write', device_after='bootloader',
                            device_name='your mobile broadband modem', log=OWNER_LOG)
    hardware(win, 'failed-during', (modem,), failures={'fixture-modem': during})
    after = safety.explain(0, 'failed to attach: device did not come back', phase='attach')
    hardware(win, 'failed-after-restart', (system,), failures={'fixture-system': after})
    charger = safety.explain(12, 'Cannot install update without external power', phase='prepare')
    hardware(win, 'failed-charger', (system,), failures={'fixture-system': charger})
    # Held after two failures that changed nothing, and paused by a known issue.
    key = win.firmware.key(modem)
    for _ in range(2):
        win.firmware.attempts.record(key, 'failed', stage='before', kind='unspecified')
    hardware(win, 'held', (modem,), failures={'fixture-modem': before})
    hardware(win, 'paused-known-issue', (modem, mouse), host=dict(FIXED, fwupd_build='2.1.7-1.fc44'))
    blocked = FirmwareUpdate(**{**modem.__dict__, 'blocked_reason': 'Luma paused this release: it stops some '
                                                                    'modems from connecting.'})
    hardware(win, 'paused-blocked', (blocked,))
    hardware(win, 'orphan-urgent', (), failures={'fixture-modem': during})
    win.close()
    win = new_window(420, 1300)
    win.go('updates')
    win.system.state = None
    settle(1.0)
    win.firmware.attempts = safety.Attempts.load(Path(os.environ['XDG_STATE_HOME']) / 'fixture-attempts-2.json')
    hardware(win, 'failed-before', (modem,), failures={'fixture-modem': before})
    hardware(win, 'preflight-unmet', (modem, system),
             host=dict(FIXED, on_battery=True, battery_level=22, battery_threshold=30),
             facts={'fixture-modem': {'modem_connected': True}})
    win.close()

if wanted('listed'):
    win = new_window(1024, 1300)
    win.go('app', 'catalog:write')
    settle(1.2)
    capture(win, 'app-write-not-enough-ratings-1024' + suffix)
    win.go('app', 'catalog:obsidian')
    settle(2.5)
    capture(win, 'app-obsidian-listed-1024' + suffix)
    win.close()

if wanted('first-party'):
    # ADR-031 states, from a labelled rpm-ostree status fixture: Leaf removed
    # in the booted system, Darkroom's removal and Imager's restore staged.
    status_path = WORK / 'rpm-ostree-status.json'
    status_path.write_text(json.dumps({'transaction': None, 'deployments': [
        {'id': 'luma-fixture-staged.0', 'booted': False, 'staged': True, 'origin': 'luma:luma/1/x86_64/stable',
         'requested-base-removals': ['luma-leaf', 'luma-darkroom'],
         'base-removals': [['luma-leaf-0.1.0-1.luma.2.fc44.noarch', 'luma-leaf', 0, '0.1.0', '1.luma.2.fc44', 'noarch'],
                           ['luma-darkroom-0.1.0-1.luma.3.fc44.noarch', 'luma-darkroom', 0, '0.1.0', '1.luma.3.fc44', 'noarch']]},
        {'id': 'luma-fixture-booted.0', 'booted': True, 'staged': False, 'origin': 'luma:luma/1/x86_64/stable',
         'requested-base-removals': ['luma-leaf', 'luma-imager'],
         'base-removals': [['luma-leaf-0.1.0-1.luma.2.fc44.noarch', 'luma-leaf', 0, '0.1.0', '1.luma.2.fc44', 'noarch'],
                           ['luma-imager-0.1.0-1.luma.2.fc44.noarch', 'luma-imager', 0, '0.1.0', '1.luma.2.fc44', 'noarch']]}]}))
    os.environ.update({
        'LUMA_DEPOT_SYSTEM': 'luma', 'LUMA_DEPOT_RPM_OSTREE_STATUS': str(status_path),
        'LUMA_DEPOT_INSTALLED_PACKAGES': 'luma-tide,luma-darkroom,nautilus,prairie-core-apps,luma-charlie',
        'LUMA_DEPOT_REQUIRED_BY': 'prairie-core-apps:luma-agenda,luma-continuity'})
    win = new_window(1024, 900)
    for identifier in ('tide', 'darkroom', 'filer', 'notes', 'charlie'):
        installed(win, identifier)
    win.go('app', 'catalog:tide')
    capture(win, 'first-party-tide-installed-with-luma-1024' + suffix)
    win._confirm_system_change(win.catalogue.find('catalog:tide'), 'override-remove')
    capture(win, 'first-party-tide-remove-confirmation-1024' + suffix)
    win.close()
    win = new_window(1024, 900)
    for identifier in ('tide', 'darkroom', 'filer', 'notes', 'charlie'):
        installed(win, identifier)
    from luma_depot.window import Job  # noqa: E402
    win.jobs['catalog:tide'] = Job('catalog:tide', 'override-remove', Gio.Cancellable(),
                                   progress=native.Progress('catalog:tide', 0.7, 0, 0, 'Writing the new system'))
    win.go('app', 'catalog:tide')
    capture(win, 'first-party-tide-removing-1024' + suffix)
    win.jobs.clear()
    win.go('app', 'catalog:filer')
    capture(win, 'first-party-filer-not-removable-1024' + suffix)
    win.go('app', 'catalog:notes')
    capture(win, 'first-party-notes-needed-by-other-parts-1024' + suffix)
    win.go('app', 'catalog:darkroom')
    capture(win, 'first-party-darkroom-removal-pending-1024' + suffix)
    win.go('app', 'catalog:leaf')
    capture(win, 'first-party-leaf-removed-1024' + suffix)
    win._confirm_system_change(win.catalogue.find('catalog:leaf'), 'override-reset')
    capture(win, 'first-party-leaf-restore-confirmation-1024' + suffix)
    win.close()
    win = new_window(1024, 900)
    win.go('app', 'catalog:imager')
    capture(win, 'first-party-imager-restore-pending-1024' + suffix)
    from luma_installer import depot_system_update as su  # noqa: E402
    win.system.state = su.from_values({
        'State': 'restart-required', 'Channel': 'stable', 'BootedVersion': '1.0.0', 'StagedVersion': '1.0.0',
        'LastCheck': int(time.time()) - 600}, 'dbus')
    win.go('updates')
    capture(win, 'first-party-updates-restart-to-finish-1024' + suffix)
    win.close()
    win = new_window(360, 900)
    for identifier in ('notes',):
        installed(win, identifier)
    win.go('app', 'catalog:notes')
    capture(win, 'first-party-notes-needed-by-other-parts-360' + suffix)
    win.go('app', 'catalog:leaf')
    capture(win, 'first-party-leaf-removed-360' + suffix)
    win.close()
    win = new_window(1280, 1600)
    win.go('category:Tools')
    capture(win, 'first-party-tools-shelf-1280' + suffix)
    win.close()
    os.environ['LUMA_DEPOT_SYSTEM'] = 'other'
    win = new_window(1024, 900)
    win.go('app', 'catalog:filer')
    capture(win, 'first-party-filer-other-distribution-1024' + suffix)
    win.close()
    for name in ('LUMA_DEPOT_SYSTEM', 'LUMA_DEPOT_RPM_OSTREE_STATUS', 'LUMA_DEPOT_INSTALLED_PACKAGES',
                 'LUMA_DEPOT_REQUIRED_BY'):
        os.environ.pop(name, None)

if wanted('links') and not suffix:
    win = new_window(1024, 760)
    win.open_uri('luma-depot://install/org.projectluma.Canvas')
    settle(1.0)
    capture(win, 'link-install-confirmation-1024')
    win.close()
    win = new_window(1024, 760)
    win.open_uri('appstream://org.example.NotPublished')
    capture(win, 'link-unknown-app-1024')
    win.open_uri('luma-depot://collection/office')
    capture(win, 'link-collection-office-1024')
    win.go('settings')
    capture(win, 'settings-counting-1024')
    win.close()

server.shutdown()
shutil.rmtree(WORK, ignore_errors=True)
