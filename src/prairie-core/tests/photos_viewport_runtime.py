#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Focused native viewer check; the complete package smoke remains separate."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

from photos_runtime_smoke import _png, settle, until
from prairie_apps.photos import PhotosApplication, PhotosWindow
from prairie_apps.photos_backend import PhotoLibrary


def assert_fit(window: PhotosWindow, *, top: int, bottom: int) -> None:
    viewport = window.viewport
    until(lambda: viewport.get_width() > 0 and viewport.get_height() > 0 and
          viewport.get_content_bounds()[2] > 0 and viewport.get_content_bounds()[3] > 0,
          'decoded native photo viewport')
    stage = viewport.compute_bounds(window.viewer_slot)[1]
    assert stage.get_y() <= 1 and stage.get_width() > 0
    assert stage.get_height() >= window.viewer_slot.get_height() - 1
    x, y, width, height = viewport.get_content_bounds()
    assert x >= 32 and y >= top, (x, y, top)
    assert x + width <= viewport.get_width() - 32 + 1
    assert y + height <= viewport.get_height() - bottom + 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--width', type=int, default=1024)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='luma-photos-viewport-') as temporary:
        root = Path(temporary)
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
        (pictures / 'Generated fixture.png').write_bytes(_png(12, 8))
        library = PhotoLibrary(root / 'library.sqlite3')
        source = library.add_source(pictures, 'Pictures')
        assert library.scan_source(source.id).added == 1
        application = PhotosApplication()
        application.set_default()
        assert application.register(None)
        window = PhotosWindow(application, library=library)
        try:
            window.set_default_size(args.width, 800)
            window.present()
            until(lambda: window.state is not None and len(window.state.records) == 1,
                  'single temporary photo')
            record = window.state.records[0]
            window.open_photo(record.id)
            until(lambda: hasattr(window, 'viewport') and
                  window.stack.get_visible_child_name() == 'viewer',
                  'opened viewer after loading adjustments')
            assert_fit(window, top=132 if args.width <= 639 else 88, bottom=96)
            window._begin_edit()
            until(lambda: window.state.editing and window.stack.get_visible_child_name() == 'viewer',
                  'editing stage')
            assert_fit(window, top=132 if args.width <= 639 else 88,
                       bottom=360 if args.width <= 639 else 290)
            window._adjustment_changed('e', 42)
            assert window.state.draft['e'] == 42
            window._rotate()
            assert window.state.draft['rot'] == 270
            window._flip()
            assert window.state.draft['flip']
            window._choose_crop('square')
            assert window.state.draft['crop']=='square'
            crop=window.viewport.get_content_bounds()
            assert abs(crop[2]-crop[3])<=1, f'Crop preview is not square: {crop}'
            window.set_default_size(500 if args.width > 639 else 1024, 800)
            until(lambda: (window.get_current_breakpoint() == window.phone_breakpoint) ==
                  (args.width > 639), 'cross-width breakpoint')
            assert_fit(window, top=132 if args.width > 639 else 88,
                       bottom=360 if args.width > 639 else 290)
            window._cancel()
            until(lambda: not window.state.editing, 'cancelled draft')
            assert not window.state.adjustments.get(record.id), 'Cancel must not save adjustments'
            assert_fit(window, top=132 if args.width > 639 else 88, bottom=96)
        finally:
            window.close()
            settle(0.1)
    print(f'photos-viewport-runtime: width={args.width} decode/fit/edit/resize/cancel PASS')


if __name__ == '__main__':
    main()
