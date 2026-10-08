#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Empty/populated library, source creation, search, playlists and visibility.

v70 replaces the legacy stack/sidebar and grouped search with an album grid,
a Sources pane and inline Songs results. Playlists and source visibility remain
store contracts; v70 does not draw their legacy chooser/filter controls.
"""
from lumaui_runtime import count, named, settle, test_application, titles
from gi.repository import Gio, Gtk, Pango
from luma_tide.model import MediaMetadata
from luma_tide.ui import TideWindow

with test_application('org.projectluma.TideEmptyTest') as (app, root):
    commands = []
    window = TideWindow(app)
    for name in ('add-music', 'add-server'):
        action = Gio.SimpleAction.new(name, None)
        def activated(_action, _parameter, name=name):
            commands.append(name)
            if name == 'add-server':
                window.present_source_menu()
        action.connect('activate', activated)
        app.add_action(action)
    window.present()
    settle(lambda: named(window, 'td-album-grid') is not None and window.get_width() > 0)
    assert window.view == 'albums' and count(window) == 0
    assert named(window, 'td-album-grid').get_child_at_index(0) is None
    assert not window.deck.get_visible() and not window.pane.shown
    assert window.now_tab is None

    # Add source offers both the folder picker and the server sign-in form.
    app.activate_action('add-music', None)
    assert commands == ['add-music']
    named(window, 'td-sources-open').emit('clicked')
    add = named(window, 'td-source-add')
    assert add is not None
    (add if isinstance(add, Gtk.Button) else add.button).emit('clicked')
    assert window.pane.shown and window.source_id == 'add'
    settle(lambda: window.server_address.get_mapped() and window.connect_button.get_mapped())
    assert window.server_address.text == '' and window.server_password.text == ''
    window._add_server()
    assert window.server_address.text == '' and window.server_user.text == ''
    assert window.server_password.text == ''
    app.activate_action('add-server', None)
    assert commands == ['add-music', 'add-server'] and window.source_id == 'add'
    window.pane.close()

    source = app.store.add_source('Test music', 'local-folder', root, local=True)
    track, _ = app.store.upsert_copy(source.id, MediaMetadata(
        uri=(root / 'track.flac').as_uri(), content_digest='1' * 64,
        title='Test song', duration_ns=60_000_000_000))
    window.refresh_library()
    settle(lambda: count(window) == 1)
    assert window.view == 'albums'
    assert named(window, 'td-album-grid').get_child_at_index(0) is not None
    # The v70 deck is absent until there is a playing subject.
    assert not window.deck.get_visible()

    window.open_search()
    window.search_item.entry.set_text('no such song')
    settle(lambda: window.query == 'no such song' and window.view == 'songs' and not window._rows)
    assert window.search.get_visible() and count(window) == 1
    assert window.trail.current.title == 'Results'
    window.search_item.entry.set_text('test')
    settle(lambda: len(window._rows) == 1)
    assert window._rows[0].song.id == track.id
    window.search_item.entry.set_text('')
    settle(lambda: window.query == '' and len(window._rows) == 1)
    assert window.view == 'songs' and window.trail.current.title == 'Songs'
    window.show_view('albums')
    assert window.view == 'albums'

    # No v70 playlist page is invented. The existing empty playlist remains
    # empty while browsing the complete library, and no records are lost.
    playlist = app.store.create_playlist('Empty playlist')
    assert app.store.playlist_tracks(playlist) == []
    window.show_view('songs')
    assert len(window._rows) == 1 and window._rows[0].song.id == track.id
    assert app.store.playlist_tracks(playlist) == []

    server = app.store.add_source('Server', 'subsonic', 'https://music.example.test', local=False)
    remote, _ = app.store.upsert_copy(server.id, MediaMetadata(
        uri='https://music.example.test/rest/stream?id=1', content_digest='',
        source_item_id='1', title='Server song', duration_ns=60_000_000_000))
    window.refresh_library()
    settle(lambda: count(window) == 2 and len(window._rows) == 2)
    # Visibility is the same existing store filter, without the retired eye UI.
    app.store.set_source_shown(server.id, False)
    window.refresh_library()
    settle(lambda: titles(window) == ['Test song'])
    assert not app.store.source(server.id).shown
    assert window.library.source(server.id).name == 'Server'
    app.store.show_only_source(server.id)
    window.refresh_library()
    settle(lambda: titles(window) == ['Server song'])
    app.store.set_source_shown(server.id, False)
    window.refresh_library()
    settle(lambda: count(window) == 0 and not window._rows)
    assert window.view == 'songs'
    # Restoring visibility restores every song; hiding never deletes records.
    app.store.show_all_sources()
    window.refresh_library()
    settle(lambda: count(window) == 2 and len(window._rows) == 2)
    assert {song.id for row in window.library.albums for song in row.songs} == {track.id, remote.id}
    assert app.store.playlist_tracks(playlist) == []
print('PASS: empty/populated library, folder/server actions, inline search, empty playlist integrity, source filters and browse restoration')

# Real grid rows must remain allocated and reachable when the window narrows.
# Presence alone missed a BreakpointBin clipping the phone grid to 424 px.
from lumaui_runtime import click, pump, window_id, xdo
import time

with test_application('org.projectluma.TideLibraryGridTest') as (app, root):
    source = app.store.add_source('Grid music', 'local-folder', root, local=True)
    for index in range(12):
        app.store.upsert_copy(source.id, MediaMetadata(
            uri=(root / f'grid-{index}.flac').as_uri(), content_digest=f'{index:064x}',
            title=f'Song {index}', artist=f'Artist {index}', album=f'Album {index}',
            duration_ns=60_000_000_000))
    window = TideWindow(app)
    window.present()
    settle(lambda: len(window.library.albums) == 12)
    for width, height in ((1180, 740), (390, 820), (1180, 740)):
        window.set_size_request(width - 10, height - 10)
        window.set_default_size(width, height)
        xdo('windowsize', '--sync', window_id(window), str(width), str(height))
        settle(lambda: abs(window.get_width() - (width - 10)) <= 2,
               what=f'resized library window requested {width}, got {window.get_width()}')
        for view in ('albums', 'artists'):
            window.show_view(view)
            grid = named(window, 'td-' + ('album' if view == 'albums' else 'artist') + '-grid')
            settle(lambda: grid.get_width() > 0 and grid.get_height() > 0,
                   what='library grid allocation')
            # Allow the frame to apply height-for-width and responsive margins.
            deadline = time.monotonic() + .2
            while time.monotonic() < deadline:
                pump()
                time.sleep(.01)
            assert grid.get_parent().get_height() >= grid.get_height(), (
                view, width, 'grid clipped by parent', grid.get_height(), grid.get_parent().get_height())
            adjustment = window.scroll.get_vadjustment()
            assert adjustment.get_upper() >= grid.get_height(), (view, width, 'short scroll range')
            last = grid.get_child_at_index(11).get_child()
            if view == 'albums':
                expected_album = last.get_name().removeprefix('td-album-')
            else:
                face_slot = last.get_child().get_first_child()
                face = face_slot.cover
                assert face.get_width() == face_slot.get_width() == face.get_height(), (
                    width, 'artist face does not fill its responsive square',
                    face.get_width(), face.get_height(), face_slot.get_width())
                print('PASS: artist face fills responsive square at', width,
                      'with side', face.get_width())
                artist_label = face_slot.get_next_sibling()
                description = artist_label.get_pango_context().get_font_description()
                assert description.get_size() / Pango.SCALE == 13, (
                    width, 'artist name type size', description.to_string())
                print('ARTIST NAME TYPE:', description.to_string())
                artist = last.get_name().removeprefix('td-artist-')
                expected_album = dict(window.library.artists)[artist][-1].id
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
            def last_visible():
                ok, bounds = last.compute_bounds(window.scroll)
                return ok and bounds.get_y() >= 0 and bounds.get_y() + bounds.get_height() <= window.scroll.get_height()
            settle(last_visible, what='last library tile in viewport')
            click(window, last)
            settle(lambda: window.view == 'album', what='last library tile activated')
            assert window.album_id == expected_album, (view, width, 'wrong final tile opened', window.album_id, expected_album)
            print('PASS: final', view, 'tile visible and mouse reachable at', width)
print('PASS: album/artist grid height, scroll range and final-row activation survive desktop/phone/desktop resizing')

# Search callbacks must respect later user navigation and final shutdown.
from lumaui_runtime import key, xdo

with test_application('org.projectluma.TideSearchLifecycleTest') as (app, root):
    source = app.store.add_source('Search music', 'local-folder', root, local=True)
    for index in range(4):
        app.store.upsert_copy(source.id, MediaMetadata(
            uri=(root / f'search-{index}.flac').as_uri(), content_digest=f'{index + 20:064x}',
            title=f'Search song {index}', artist='Search artist', album='Search album',
            duration_ns=60_000_000_000))
    window = TideWindow(app)
    window.present()
    settle(lambda: count(window) == 4)
    click(window, named(window, 'td-search-open'))
    key(window, window.search_item.entry, 'ctrl+a')
    xdo('type', '--clearmodifiers', '--delay', '0', 'Search song 3')
    settle(lambda: window.query == 'Search song 3' and len(window._rows) == 1)
    assert window._rows[0].song.title == 'Search song 3'

    # GtkEditable emits changed synchronously. Navigate before its pending
    # main-loop callback; stale text must not reopen the previous results.
    window.search_item.entry.set_text('Search song 2')
    window.show_view('artists')
    pump()
    assert window.view == 'artists' and window.trail.current.title == 'Artists', 'pending search overrode later navigation'
    assert named(window, 'td-artist-grid') is not None

    query_before_close = window.query
    window.search_item.entry.set_text('Search song 1')
    window.close_resources()
    window.close()
    pump()
    assert window.query == query_before_close, 'search rendered after resource shutdown'
    assert not app.get_windows(), 'closed search window retained by application'
    assert len(app.store.tracks()) == 4, 'search/navigation changed the library'
print('PASS: real keyboard search; pending text cannot override navigation or render after shutdown; library preserved')
