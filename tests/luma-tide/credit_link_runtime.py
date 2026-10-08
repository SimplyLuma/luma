#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""v70's deck credit buttons navigate without changing playback.

Song-row album/artist text is plain in v70; its clickable credits are on the
attached deck. Keep the real pointer, Tab traversal and Enter checks on those
production controls, rather than reconstructing the retired markup-link row.
"""
from unittest.mock import patch
from lumaui_runtime import click, count, key, pump, settle, test_application, walk, window_id, xdo
from gi.repository import Gtk
from luma_tide.model import MediaMetadata
from luma_tide.ui import TideWindow

with test_application('org.projectluma.TideCreditLinkTest') as (app, root):
    source = app.store.add_source('Test music', 'local-folder', root, local=True)
    track, _ = app.store.upsert_copy(source.id, MediaMetadata(
        uri=(root / '1.flac').as_uri(), content_digest='1' * 64, title='Song 1',
        artist='Radiohead', album='OK Computer', album_artist='Radiohead',
        duration_ns=60_000_000_000))
    window = TideWindow(app)
    window.set_title('CreditLinkRuntimeTest')
    window.set_size_request(1180, 740)
    window.present()
    settle(lambda: count(window) == 1 and window.get_width() > 0)
    # This check exercises both ordinary credit links at the reference width.
    # Xvfb has no window manager to honor a mapped window's size request.
    window.set_default_size(1180, 740)
    xdo('windowsize', '--sync', window_id(window), '1180', '740')
    settle(lambda: 1100 <= window.layer.get_width() <= 1180)
    window.show_view('songs')
    # v70 data-tplay activates on one click. This is the control case proving
    # real pointer events can play the song before the credit-isolation checks.
    click(window, window._rows[0].play)
    settle(lambda: app.controller.snapshot.track is not None and window.deck.title.get_label() == 'Song 1')
    settle(lambda: window.deck.album_credit.get_visible(), what='ordinary album credit')
    assert app.controller.snapshot.track.id == track.id
    initial = app.controller.snapshot
    def credit(value):
        return next(widget for widget in walk(window.deck.credit)
                    if isinstance(widget, Gtk.Button) and isinstance(widget.get_child(), Gtk.Label)
                    and widget.get_child().get_label() == value)
    with patch.object(window.source, 'play', wraps=window.source.play) as play, \
         patch.object(window.source, 'toggle', wraps=window.source.toggle) as toggle:
        click(window, credit('Radiohead'))
        settle(lambda: window.view == 'artists')
        assert window.trail.current.title == 'Artists'
        assert app.controller.snapshot == initial
        play.assert_not_called(); toggle.assert_not_called()
        click(window, credit('OK Computer'))
        settle(lambda: window.view == 'album')
        assert window.trail.current.title == 'OK Computer'
        assert app.controller.snapshot == initial
        play.assert_not_called(); toggle.assert_not_called()

        # Start at the preceding deck title and use real Tab traversal,
        # rather than putting keyboard focus directly on the credit.
        assert window.deck.title_action.grab_focus()
        window_id(window)
        for _ in range(12):
            if window.get_focus() is credit('Radiohead'):
                break
            xdo('key', 'Tab'); pump()
        settle(lambda: window.get_focus() is credit('Radiohead'), timeout=3,
               what='Tab reaching the artist credit')
        key(window, None, 'Return')
        settle(lambda: window.view == 'artists')
        assert app.controller.snapshot == initial
        play.assert_not_called(); toggle.assert_not_called()
        key(window, credit('OK Computer'), 'Return')
        settle(lambda: window.view == 'album')
        assert window.trail.current.title == 'OK Computer'
        assert app.controller.snapshot == initial
        play.assert_not_called(); toggle.assert_not_called()
print('PASS: real song click plays; deck artist/album mouse and keyboard links navigate without playback; Tab reaches the credit')
