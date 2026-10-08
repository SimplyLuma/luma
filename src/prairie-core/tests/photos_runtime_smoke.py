#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

"""Fedora GTK runtime smoke for the shared responsive Photos application."""

from __future__ import annotations

import argparse
import binascii
import gc
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import GLib, Gdk, Gtk
from luma_appkit import AppWindow, MediaGrid, MediaTile

from prairie_apps.photos import (
    LUMA_PLATFORM_AVAILABLE,
    PhotosApplication,
    PhotosWindow,
)
from prairie_apps.photos_backend import PhotoLibrary


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    scanlines = b"".join(b"\0" + b"\x68\x8f\xc8" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )


def settle(seconds: float = 0.35) -> None:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def until(predicate, description: str, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        settle(0.025)
        if predicate():
            return
    raise AssertionError(f'Photos did not settle: {description}')


def grids(window):
    child = window.photo_body.get_first_child()
    while child is not None:
        if isinstance(child, MediaGrid):
            yield child
        child = child.get_next_sibling()


def destination(window, key):
    row = window.sidebar.list.get_first_child()
    while row is not None:
        if getattr(row, 'destination', None) == key:
            return row
        row = row.get_next_sibling()
    raise AssertionError(f'Missing sidebar destination {key}')


def activate_destination(window, key):
    window.sidebar.list.emit('row-activated', destination(window, key))
    until(lambda: window.state.view == key and
          window.stack.get_visible_child_name() == 'library' and
          window.title.get_label() == ('Favorites' if key == 'fav' else 'Library'), key)


def assert_square_grid(window):
    grid = next(grids(window))
    tile = grid.get_first_child()
    assert isinstance(tile, MediaTile)
    until(lambda: tile.get_width() > 0, 'allocated photo mosaic')
    # MediaGrid cells include rounded row heights and their share of the
    # gutters. Allow two pixels for GTK's whole-pixel row allocation.
    left, right, bottom = tile._insets
    photo_width = tile.get_width() - left - right
    photo_height = tile.get_height() - bottom
    assert photo_width > 0 and photo_height > 0
    assert abs(photo_width - photo_height) <= 2, (
        f'Photo mosaic is not square: {photo_width}x{photo_height}; '
        f'grid={grid.get_width()} tile={tile.get_width()}x{tile.get_height()} '
        f'insets={tile._insets} columns={grid.columns}')
    assert any(isinstance(controller, Gtk.DragSource) and
               controller.get_actions() == Gdk.DragAction.COPY
               for controller in grid.observe_controllers())
    return grid, tile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--expected-compact', choices=('true', 'false'), required=True)
    parser.add_argument('--expected-decorated', choices=('true', 'false'), required=True)
    parser.add_argument('--require-luma-platform', action='store_true')
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix='luma-photos-smoke-') as temporary:
        root = Path(temporary)
        # The RPM uses a private bus/display; isolate every app store as well.
        for variable, folder in (('HOME', 'home'), ('XDG_DATA_HOME', 'data'),
                                 ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                                 ('XDG_STATE_HOME', 'state')):
            path = root / folder
            path.mkdir()
            os.environ[variable] = str(path)
        os.environ['LUMA_PHOTOS_NON_UNIQUE'] = '1'
        os.environ.pop('LUMA_PHOTOS_FIXTURE', None)
        pictures = root / 'Pictures'
        pictures.mkdir()
        fixture = pictures / 'Generated fixture.png'
        fixture.write_bytes(_png(12, 8))
        library = PhotoLibrary(root / 'library.sqlite3')
        source = library.add_source(pictures, 'Pictures')
        result = library.scan_source(source.id)
        assert result.added == 1

        application = PhotosApplication()
        application.set_default()
        assert application.register(None)
        window = PhotosWindow(application, library=library)
        try:
            window.set_default_size(args.width, args.height)
            window.present()
            until(lambda: window.state is not None and len(window.state.records) == 1,
                  'scanned photo library')
            assert not getattr(window, 'last_error', None)
            record = window.state.records[0]
            assert record.display_name == fixture.name
            assert (record.width, record.height) == (12, 8)
            assert record.available
            # GTK may return fresh wrappers: exercise the actual sidebar signal
            # after collection, not the app's destination callback.
            gc.collect()
            activate_destination(window, 'fav')
            assert not window.state.records
            # v70 replaces the actionable legacy EmptyPage with a quiet hint.
            hint = window.photo_body.get_first_child()
            assert isinstance(hint, Gtk.Label)
            assert hint.get_text() == 'No photos match.'
            assert hint.get_next_sibling() is None, 'Empty Favorites invented an action'
            activate_destination(window, 'library')
            assert len(window.state.records) == 1
            record = window.state.records[0]
            grid, tile = assert_square_grid(window)
            # Exercise the controller's actual prepare signal and both formats.
            drag = next(controller for controller in grid.observe_controllers()
                        if isinstance(controller, Gtk.DragSource))
            provider = drag.emit('prepare', tile.get_width() / 2, tile.get_height() / 2)
            assert provider is not None
            assert provider.ref_formats().contain_mime_type('text/uri-list')
            assert provider.ref_formats().contain_gtype(Gdk.FileList.__gtype__)
            provider = window._prepare_file_drag(None, 0, 0, record.id)
            assert provider is not None
            assert provider.ref_formats().contain_mime_type('text/uri-list')
            assert provider.ref_formats().contain_gtype(Gdk.FileList.__gtype__)
            window.open_photo(record.id)
            until(lambda: getattr(window, 'viewport', None) is not None and
                  window.viewport.get_width() > 0, 'native photo viewer')
            assert window.stack.get_visible_child_name() == 'viewer'
            assert not window.page.get_child_visible(), 'Library page remained in viewer'
            window.set_default_size(args.width, args.height + 20)
            settle()
            assert not window.page.get_child_visible(), 'Resize restored the library toolbar inside viewer'
            assert window.stack.get_visible_child_name() == 'viewer'
            window.set_default_size(args.width, args.height)
            settle()
            phone = args.expected_compact == 'true'
            # v71: the photo's actions are the corner pill on a computer, the bar on a phone.
            if not phone:
                assert window.corner.get_parent() is window.corner_slot
            assert window.action_center.bar.get_visible()
            assert not window.details.shown, 'Information must start closed'
            # v70 uses a contained viewer and one action center, without the old
            # desktop toolbar/filmstrip/footer. Check its native allocations.
            assert window.viewport.get_height() > 0
            viewport_bounds = window.viewport.compute_bounds(window.viewer_slot)[1]
            assert viewport_bounds.get_width() > 0 and viewport_bounds.get_height() > 0
            assert viewport_bounds.get_y() <= 1, 'Black viewport must cover the full stage'
            until(lambda: window.viewport.get_content_bounds()[2] > 0 and
                  window.viewport.get_content_bounds()[3] > 0,
                  'decoded photo in native viewport')
            x,y,width,height = window.viewport.get_content_bounds()
            # v71 phone: edge to edge on black between the title island and the filmstrip.
            top, side, bottom = (170, 0, 168) if phone else (88, 32, 96)
            assert x >= side and y >= top, (x,y,top)
            assert x + width <= window.viewport.get_width() - side + 1
            assert y + height <= window.viewport.get_height() - bottom + 1
            if phone:
                assert window._island.get_visible(), 'phone viewer has its title island'
                assert window.strip_slot.get_visible(), 'phone viewer has its filmstrip'
                assert window._go_back()
            else:
                back = window.action_center.bar_row.get_first_child()
                assert isinstance(back, Gtk.Button)
                assert back.bar_item.icon == 'chevron-left'
                back.emit('clicked')
            until(lambda: window.stack.get_visible_child_name() == 'library' and
                  len(window.state.records) == 1, 'return to photo library')
            # A computer's library has its view island (Years to All, photo size); a phone has none.
            view = window.corner_slot.get_child()
            assert (view is None) if phone else (view.get_name() == 'ph-view')
            if not phone:
                assert window.search_item.entry is not None
            assert_square_grid(window)
            if args.require_luma_platform:
                assert LUMA_PLATFORM_AVAILABLE
                assert isinstance(window, AppWindow)
            expected_compact = args.expected_compact == 'true'
            print('photos-runtime-layout: '
                  f'allocated={window.get_width()}x{window.get_height()} '
                  f'appkit-titlebar={window.title_bar.get_visible()} '
                  f'toggle={window.toggle.get_visible()} '
                  f'sidebar={window.sidebar.get_visible()} info={window.details.shown}')
            # Creator review keeps sidebar access in the application menu at
            # every width; its standalone control must remain hidden.
            assert not window.toggle.get_visible()
            assert window.sidebar.get_visible() is (not expected_compact)
            sidebar_command = window.commands.get('photos.sidebar')
            assert sidebar_command.icon == 'panel-left'
            if not expected_compact:
                sidebar_command.execute()
                until(lambda: not window.toggle.shown and not window.sidebar.get_mapped(), 'menu hides sidebar')
                sidebar_command.execute()
                until(lambda: window.toggle.shown and window.sidebar.get_mapped(), 'menu restores sidebar')
                assert not window.toggle.get_visible()
            expected_decorated = args.expected_decorated == 'true'
            assert window.title_bar.get_visible() is expected_decorated
            assert window.get_decorated() is expected_decorated

            window._favourite(record.id, True)
            until(lambda: library.asset(record.id).favorite, 'saved favourite')
            album = library.create_album('Smoke Album')
            library.add_to_album(album.id, (record.id,))
            assert library.assets(album_id=album.id)[0].id == record.id
            activate_destination(window, 'fav')
            assert len(window.state.records) == 1
            window.open_photo(record.id)
            until(lambda: window.stack.get_visible_child_name() == 'viewer', 'favourite viewer')
            assert window._key_pressed(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0))
            until(lambda: window.stack.get_visible_child_name() == 'library', 'Escape closes viewer')
            assert window.state.view == 'fav' and window.title.get_label() == 'Favorites'
            window.open_photo(record.id)
            until(lambda: window.stack.get_visible_child_name() == 'viewer', 'reopened favourite')
            activate_destination(window, 'library')
            assert window.state.viewer is None and window.state.view == 'library'
            assert window.stack.get_visible_child_name() == 'library'
        finally:
            window.close()
            settle(0.1)

    print('photos-runtime-smoke: '
          f'width={args.width} height={args.height} '
          f'compact={str(expected_compact).lower()} '
          f'luma={str(LUMA_PLATFORM_AVAILABLE).lower()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
