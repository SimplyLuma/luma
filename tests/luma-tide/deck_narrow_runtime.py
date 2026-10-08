#!/usr/bin/python3
"""Check the player remains inside its bar on a separate GTK display."""
from __future__ import annotations

from lumaui_runtime import named, settle, test_application, window_id, xdo
from luma_tide.model import MediaMetadata
from luma_tide.ui import TideWindow


with test_application('org.projectluma.TideNarrowDeckTest') as (app, root):
    source = app.store.add_source('Music', 'local-folder', root, local=True)
    track, _copy = app.store.upsert_copy(source.id, MediaMetadata(
        uri=(root / 'one.flac').as_uri(), content_digest='b' * 64,
        title='A long song title for a compact player',
        artist='The Example Ensemble', album='Album Name',
        duration_ns=180_000_000_000))
    app.controller.replace_queue([track.id], start=0, play=True)
    window = TideWindow(app)
    window.present()
    settle(lambda: window.deck.get_visible() and window.get_width() > 0)
    for width in (1024, 820, 650, 500, 390, 360):
        window.set_size_request(width - 10, 730)
        window.set_default_size(width, 740)
        xdo('windowsize', '--sync', window_id(window), str(width), '740')
        settle(lambda: abs(window.get_width() - width) <= 12, what='deck window resize')
        if width <= 559:
            settle(lambda: window.phone and not window.deck.get_visible() and window.mini.get_mapped())
            assert window.mini.play.get_mapped() and window.mini.next.get_mapped()
            assert app.controller.snapshot.track.id == track.id
            continue
        settle(lambda: window.deck.props.width_request <= window.layer.get_width() + 2,
               what='responsive deck width')
        settle(lambda: window.deck.get_width() > 0 and window.deck.title.get_width() > 0,
               what='deck allocation')
        assert window.deck.get_width() <= window.layer.get_width() + 2, (width, window.deck.get_width(), window.layer.get_width())
        assert window.deck.title.get_width() >= 35, (width, window.deck.title.get_width())
        assert window.deck.artist_credit.get_visible(), width
        assert window.deck.artist_credit.get_width() >= 30, (width, window.deck.artist_credit.get_width())
        if width <= 899:
            settle(lambda: not window.deck.album_credit.get_visible(), what='compact deck credit')
            assert not window.deck.album_credit.get_visible(), width
            assert window.deck.body.get_height() >= (84 if width <= 720 else 92)
    window.set_size_request(1170, 730)
    xdo('windowsize', '--sync', window_id(window), '1180', '740')
    settle(lambda: not window.phone)
    window.open_search()
    settle(lambda: window.search_item.entry.get_width() > 0, what='inline search allocation')
    assert window.corner.get_width() <= window.layer.get_width() + 2
    window.show_view('songs')
    header = named(window, 'td-trail')
    settle(lambda: header.title.get_height() > 0 and header.meta_box.get_height() > 0)
    title_ok, title_bounds = header.title.compute_bounds(header)
    meta_ok, meta_bounds = header.meta_box.compute_bounds(header)
    assert title_ok and meta_ok
    assert abs(title_bounds.get_center().y - meta_bounds.get_center().y) <= 2

print('PASS: compact player, inline search and library header geometry')
