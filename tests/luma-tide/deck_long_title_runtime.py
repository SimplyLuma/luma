#!/usr/bin/python3
"""Long track and album names must leave the desktop player controls usable."""
from lumaui_runtime import settle, test_application, window_id, xdo
from luma_tide.model import MediaMetadata
from luma_tide.ui import TideWindow


with test_application('org.projectluma.TideLongTitleTest') as (app, root):
    source = app.store.add_source('Music', 'local-folder', root, local=True)
    title = ('05. The Happiest Days of Our Lives ' + 'Live Broadcast at O2 Arena Prague ' * 5).strip()
    track, _copy = app.store.upsert_copy(source.id, MediaMetadata(
        uri=(root / 'long.flac').as_uri(), content_digest='c' * 64,
        title=title, artist='Roger Waters and the Very Long Ensemble',
        album='This Is Not A Drill: Live Broadcast at O2 Arena Prague ' * 3,
        duration_ns=180_000_000_000))
    app.controller.replace_queue([track.id], start=0, play=True)
    window = TideWindow(app)
    window.present()
    settle(lambda: window.deck.get_visible() and window.deck.title.get_width() > 0)
    for width in (1252, 1440, 1600):
        window.unmaximize()
        window.set_default_size(width, 740)
        xdo('windowsize', '--sync', window_id(window), str(width), '740')
        settle(lambda: abs(window.get_surface().get_width() - width) <= 2
               and width - 50 <= window.layer.get_width() <= width,
               what='requested player viewport')
        window._size_deck()
        layer_width = window.layer.get_width()
        expected_deck = min(1180, max(250, layer_width - 300)) + 36
        settle(lambda: window.deck.get_width() == expected_deck,
               what='responsive deck allocation')
        assert window.deck.title.get_label() == title
        transport_ok, transport = window.deck.transport.compute_bounds(window.deck)
        title_ok, title_box = window.deck.title_action.compute_bounds(window.deck)
        artist_ok, artist = window.deck.artist_credit.compute_bounds(window.deck)
        end_ok, end = window.deck.end.compute_bounds(window.deck)
        assert all((transport_ok, title_ok, artist_ok, end_ok))
        assert title_box.get_x() + title_box.get_width() + 8 <= transport.get_x(), width
        assert artist.get_x() + artist.get_width() + 8 <= transport.get_x(), width
        assert end.get_x() + end.get_width() <= window.deck.get_width(), width
        assert window.deck.album_credit.get_visible() == (width >= 1500), width
        if window.deck.album_credit.get_visible():
            album_ok, album = window.deck.album_credit.compute_bounds(window.deck)
            assert album_ok
            assert album.get_x() + album.get_width() + 8 <= transport.get_x(), width
        print(width, 'title', round(title_box.get_width()),
              'transport starts', round(transport.get_x()), flush=True)

print('PASS: long title and album leave desktop player controls unobstructed')
