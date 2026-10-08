#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Render Depot's store listings from the real catalogue and its real pictures.

    DEPOT_RENDER_SCHEME=light|dark python3 depot_store_render.py OUT MEDIA_SITE [NAME...]

MEDIA_SITE is a site directory holding media/listings/ as enrich-listings.py
wrote it. Every picture is placed in Depot's content-addressed cache under its
digest, exactly as Depot would after downloading and verifying it, so nothing
is fetched. The catalogue is the generated seed, signed with the RFC 8032 test
key. Nothing is installed and no helper runs: install progress and failure are
the window's own states, set the way a running install sets them.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
SITE = Path(sys.argv[2]).resolve()
ONLY = set(sys.argv[3:])
SRC = Path(__file__).resolve().parents[2]
WORK = Path(tempfile.mkdtemp(prefix='depot-store-render-'))
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
import http.server  # noqa: E402
import threading  # noqa: E402


class Hub(http.server.BaseHTTPRequestHandler):
    """Reviews: none yet, as a new listing has."""

    def do_GET(self):
        body = json.dumps({'rating': {'average': 0, 'count': 0}, 'reviews': []}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Hub)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ['LUMA_DEPOT_HUB_URL'] = f'http://127.0.0.1:{server.server_address[1]}'

# Pictures: into the cache under their digest, as a verified download lands.
media_cache = Path(os.environ['XDG_CACHE_HOME']) / 'luma/depot/media'
media_cache.mkdir(parents=True)
for path in (SITE / 'media/listings').rglob('*'):
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        shutil.copyfile(path, media_cache / (digest + path.suffix))

seed = json.loads((SRC / 'luma-installer/data/depot-catalog-4.json').read_text())
seed['generated_at'] = '2026-09-30T18:00:00Z'
CONTENT = json.dumps(seed).encode()
(WORK / 'depot-catalog.pub').write_bytes(signer.public_key_file())
cache = Path(os.environ['XDG_CACHE_HOME']) / 'luma/depot'
(cache / 'catalog-4.json.minisig').write_bytes(signer.signature_file(CONTENT))
(cache / 'catalog-4.json').write_bytes(CONTENT)
(WORK / 'luma.flatpakrepo').write_text(
    '[Flatpak Repo]\nTitle=Luma (render fixture)\nUrl=https://dl.simplyluma.com/repo/\nGPGKey=' + 'A' * 64 + '\n')

import gi  # noqa: E402
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402
from unittest import mock  # noqa: E402

from luma_installer import depot_reviews  # noqa: E402
depot_reviews.HUB_URL = os.environ['LUMA_DEPOT_HUB_URL']
from luma_depot import channels, native, window as depot_window  # noqa: E402
from luma_depot.providers import Progress  # noqa: E402

# Captures must never activate Nick's installed Depot or his separate preview.
depot_window.APP_ID = 'org.projectluma.Depot.LayoutCapture'


def settle(seconds=1.2):
    until = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < until:
        while context.iteration(False):
            pass
        time.sleep(0.01)


def capture(win, name):
    root = win.get_first_child()
    width, height = win.get_size_request()
    root.allocate(width, height, -1, None)
    settle(0.5)
    if name.startswith(('discover-', 'category-media-')):
        widgets, _labels = drawn_labels(win)
        wide_shelves = tuple(widget for widget in widgets
                             if widget.has_css_class('depot-wide-shelf'))
        assert wide_shelves, f'{name} has no adaptive app shelf'
        expect_wide = width >= 1600
        assert any(widget.get_visible() and widget.get_width() > 0 for widget in wide_shelves) == expect_wide, (
            f'{name} did not activate the expected desktop shelf')
    if os.environ.get('DEPOT_RENDER_LAYOUT'):
        drawn_labels(win)
    for _attempt in range(20):
        settle(0.6)
        probe = Gtk.Snapshot.new()
        Gtk.WidgetPaintable.new(root).snapshot(probe, root.get_width(), root.get_height())
        if probe.to_node() is not None:
            break
    snapshot = Gtk.Snapshot.new()
    Gtk.WidgetPaintable.new(root).snapshot(snapshot, root.get_width(), root.get_height())
    node = snapshot.to_node()
    if node is None:
        raise SystemExit(f'nothing drew for {name}')
    path = OUT / f'{name}.png'
    win.get_renderer().render_texture(node, None).save_to_png(str(path))
    print(f'rendered {path.name} {root.get_width()}x{root.get_height()}')


def drawn_labels(win):
    def walk(widget):
        while widget is not None:
            yield widget
            yield from walk(widget.get_first_child())
            widget = widget.get_next_sibling()
    widgets = tuple(walk(win.content))
    if os.environ.get('DEPOT_RENDER_LAYOUT'):
        for widget in widgets:
            if any(widget.has_css_class(name) for name in
                   ('depot-app-card', 'depot-wide-shelf', 'depot-shelf-scroll')):
                print('layout', widget.get_css_classes(), widget.get_width(),
                      widget.get_height(), widget.get_visible())
    return widgets, {widget.get_text() for widget in widgets if isinstance(widget, Gtk.Label)}


class NothingInstalled:
    installed = {}
    desktop_files = {}
    entries = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def identities(self):
        return {}


for patch in (
        mock.patch.object(native, 'inventory', return_value=()),
        mock.patch.object(native, 'installed_flatpak_refs', return_value={}),
        mock.patch.object(native.NativeInstallation, '_check_updates', return_value={}),
        mock.patch('luma_installer.depot_flatpak.luma_remote_available', return_value=True),
        # This render host is not a Luma computer with snapd and the helper:
        # show every channel as a Luma computer would.
        mock.patch.object(channels, 'available', return_value=(True, '')),
        mock.patch.object(channels, 'State', NothingInstalled),
        mock.patch.object(native, 'architecture', return_value='x86_64')):
    patch.start()

scheme = os.environ.get('DEPOT_RENDER_SCHEME', 'light')
suffix = '' if scheme == 'light' else '-dark'
if scheme == 'dark':
    os.environ['GSETTINGS_BACKEND'] = 'memory'
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
    win.set_size_request(width, height)
    win.present()
    settle(2.5)
    assert win.catalogue is not None and win.catalogue_error == '', win.catalogue_error
    return win


def wanted(name):
    return not ONLY or name in ONLY


def listing(win, identifier, name):
    app_entry = win.catalogue.find('catalog:' + identifier)
    if os.environ.get('DEPOT_RENDER_DEBUG'):
        for shot in app_entry.screenshots:
            print('shot', shot.sha256[:12], repr(win.media.path(shot.url, shot.sha256)))
    win.go('app', 'catalog:' + identifier)
    settle(3.0)  # the pictures come from the cache on a worker thread
    win.render()
    if os.environ.get('DEPOT_RENDER_DEBUG'):
        def walk(widget, depth=0):
            while widget is not None:
                if isinstance(widget, Gtk.Picture):
                    print('shot picture', widget.get_paintable(), widget.get_width(), widget.get_height(),
                          widget.get_mapped())
                walk(widget.get_first_child(), depth + 1)
                widget = widget.get_next_sibling()
        settle(1.0)
        walk(win.get_first_child())
    capture(win, name + suffix)


if wanted('discover'):
    width = int(os.environ.get('DEPOT_RENDER_WIDTH', '1280'))
    height = int(os.environ.get('DEPOT_RENDER_HEIGHT', '1180'))
    win = new_window(width, height)
    win.go('home')
    settle(3.0)
    win.render()
    drawn, labels = drawn_labels(win)
    assert any(widget.has_css_class('depot-feature') for widget in drawn), 'Discover feature did not render'
    assert {'Made for Luma', 'Explore apps', 'Categories'} <= labels, labels
    capture(win, f'discover-{width}' + suffix)
    win.close()

for state, argument, expected in (
        ('category-media', 'category:Media', {'Media', 'Made for Luma'}),
        ('detail-tide', 'app', {'Tide', 'Description', 'Details', 'No ratings yet'}),
        ('search-tide', 'search', {'1 result', 'for “tide”', 'Tide'}),
        ('updates', 'updates', {'Apps', 'Every app is up to date'})):
    if wanted(state):
        width = int(os.environ.get('DEPOT_RENDER_WIDTH', '1180'))
        height = int(os.environ.get('DEPOT_RENDER_HEIGHT', '900'))
        win = new_window(width, height)
        win.go(argument, 'catalog:tide' if state == 'detail-tide' else
               'tide' if state == 'search-tide' else '')
        settle(3.0)
        win.render()
        _drawn, labels = drawn_labels(win)
        assert expected <= labels, f'{state} did not render required content: {expected - labels}'
        capture(win, f'{state}-{width}' + suffix)
        win.close()

for identifier in ('nordvpn', 'termius', 'claude', 'gimp', 'mega', 'figma', 'notes', 'photos'):
    if wanted(identifier):
        win = new_window(1180, 1180)
        listing(win, identifier, f'listing-{identifier}')
        win.close()

if wanted('progress'):
    win = new_window(1180, 900)
    job = depot_window.Job(app_id='catalog:nordvpn', kind='install', cancellable=Gio.Cancellable())
    job.progress = Progress('catalog:nordvpn', 0.42, 25_000_000, 59_600_000, 'Downloading')
    win.jobs['catalog:nordvpn'] = job
    listing(win, 'nordvpn', 'install-progress-nordvpn')
    win.close()

if wanted('failure'):
    win = new_window(1180, 900)
    job = depot_window.Job(app_id='catalog:termius', kind='install', cancellable=Gio.Cancellable())
    job.failed = ('Termius couldn’t be installed because its app source could not be reached. '
                  'Check your connection and try again.')
    job.detail = ('error: cannot install "termius-app": Post "https://api.snapcraft.io/v2/snaps/refresh": '
                  'dial tcp: lookup api.snapcraft.io: Temporary failure in name resolution')
    win.jobs['catalog:termius'] = job
    listing(win, 'termius', 'install-failure-termius')
    win.close()

shutil.rmtree(WORK, ignore_errors=True)
