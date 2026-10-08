# SPDX-License-Identifier: Apache-2.0
"""Real confirmation/Undo actions with fixture-only source mutations."""
from pathlib import Path
import time
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from lumaui_runtime import click as pointer_click, named, settle, test_application, walk
from luma_tide.fixture import FixtureLibrary
from luma_tide.ui import TideWindow

repo = Path(__file__).resolve().parents[2]


def button(window, name):
    item = named(window, name)
    assert item is not None, name
    return item if isinstance(item, Gtk.Button) else item.button


def labelled(window, label):
    return next((item for item in walk(window) if isinstance(item, Gtk.Button)
                 and item.get_label() == label and item.get_mapped()), None)


def click(window, item):
    # Drawers animate their coordinates after mapping. Wait for the actual
    # pointer target to stop moving and remain unobscured before clicking.
    previous, last_changed = None, time.monotonic()
    def stable_target():
        nonlocal previous, last_changed
        ok, bounds = item.compute_bounds(window)
        if not ok or not item.get_mapped():
            return False
        geometry = tuple(round(value, 2) for value in
                         (bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()))
        if geometry != previous:
            previous, last_changed = geometry, time.monotonic()
        picked = window.pick(bounds.get_x() + bounds.get_width() / 2,
                             bounds.get_y() + bounds.get_height() / 2, Gtk.PickFlags.DEFAULT)
        hit = picked is item or (picked is not None and picked.is_ancestor(item))
        return hit and time.monotonic() - last_changed >= .15
    settle(stable_target, what='stable unobscured mouse target ' + item.get_name())
    pointer_click(window, item)


for width, height in ((1180, 740), (390, 820)):
    with test_application(f'org.projectluma.TideSignOutUITest.W{width}') as (app, root):
        source = FixtureLibrary(repo / 'tests/fixtures/tide-v70.json')
        original = source.library
        original_player = source.player
        window = TideWindow(app, source)
        window.set_default_size(width, height)
        window.present()
        settle(lambda: bool(window.library.albums))
        click(window, button(window, 'td-sources-open'))
        settle(lambda: named(window, 'td-source-nd') is not None)
        click(window, button(window, 'td-source-nd'))
        settle(lambda: named(window, 'td-source-sign-out') is not None)

        click(window, button(window, 'td-source-remove'))
        settle(lambda: named(window, 'td-confirm-remove') is not None)
        assert source.library == original, 'opening Remove changed source data'
        click(window, labelled(window, 'Cancel'))
        settle(lambda: named(window, 'td-confirm-remove') is None
               and named(window, 'td-source-sign-out') is not None,
               what='Remove Cancel restores selected source')
        assert source.library == original, 'Remove Cancel changed source data'

        click(window, button(window, 'td-source-sign-out'))
        settle(lambda: named(window, 'td-confirm-sign-out') is not None)
        assert source.library == original, 'opening confirmation changed source data'
        cancel = labelled(window, 'Cancel')
        assert cancel is not None
        click(window, cancel)
        settle(lambda: named(window, 'td-confirm-sign-out') is None
               and named(window, 'td-source-sign-out') is not None,
               what='Sign out Cancel restores selected source')
        assert source.library == original, 'cancel changed source data'

        click(window, button(window, 'td-source-sign-out'))
        settle(lambda: named(window, 'td-confirm-sign-out') is not None)
        click(window, button(window, 'td-confirm-sign-out'))
        settle(lambda: window.library.source('nd').state == 'bad')
        assert source.library.source('dl') == original.source('dl'), 'sign-out changed downloads'
        settle(lambda: labelled(window, 'Undo') is not None, what='sign-out Undo')
        undo = labelled(window, 'Undo')
        ok, bounds = undo.compute_bounds(window)
        assert ok
        picked = window.pick(bounds.get_x() + bounds.get_width() / 2,
                             bounds.get_y() + bounds.get_height() / 2, Gtk.PickFlags.DEFAULT)
        print('UNDO HIT TARGET:', type(picked).__name__, picked.get_name() if picked else None,
              'bounds:', bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height(), flush=True)
        click(window, undo)
        settle(lambda: source.library == original and window.library.source('nd').state == 'ok',
               what='real mouse Undo restores source')
        assert app.store.tracks() == [], 'fixture confirmation wrote into the local store'
        assert source.player == original_player, 'confirmation/Undo changed playback'
        print(f'PASS {width}: actual source/Sign out/Cancel/confirm/Undo mouse actions; downloads and local store preserved')
print('PASS: sign-out UI composition on desktop/phone; all fixture windows closed')
