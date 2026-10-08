#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed v70 columns align, real records stay correct, and phone rows fit.

v70 replaces configurable legacy columns/copy menus/playlist choosers with
fixed Title/Album/Time, inline Love, album Play, source facts and artist tiles.
Copy choice, queue order and playlists remain real controller/store contracts
and are asserted here as well as in the model tests.
"""
from lumaui_runtime import count, named, settle, test_application, walk, window_id, xdo
from gi.repository import Gtk
from luma_tide.model import MediaMetadata
from luma_tide.ui import TideWindow
from luma_tide.ui_parts import SongRow


def left(window, widget):
    ok, bounds = widget.compute_bounds(window)
    assert ok
    return bounds.get_x()


def rows(window):
    return sorted((widget for widget in walk(window.page) if isinstance(widget, SongRow)),
                  key=lambda row: row.song.number)


def aligned(window):
    header = named(window, 'td-table-header')
    settle(lambda: header.get_mapped() and header.get_width() > 0 and bool(rows(window)))
    first = rows(window)[0]
    cells = list(TideWindow._children(first))
    settle(lambda: all(cell.get_mapped() and cell.get_width() > 0 for cell in cells),
           what='allocated song cells')
    assert len(cells) == len(header.cells) == 5
    assert [column.key for column in header.columns] == [None, 'title', 'album', None, 'time']
    for column, cell, heading in zip(header.columns, cells, header.cells):
        actual, expected = left(window, cell), left(window, heading)
        assert abs(actual - expected) <= 2, f'{column.key}: row {actual}, header {expected}'
    assert first.time.get_label() == '3:01'
    return first


with test_application('org.projectluma.TideColumnsTest') as (app, root):
    local = app.store.add_source('This device', 'local-folder', root, local=True)
    tracks = []
    for index in range(1, 5):
        track, _ = app.store.upsert_copy(local.id, MediaMetadata(
            uri=(root / f'{index}.flac').as_uri(), content_digest=str(index) * 64,
            recording_id=f'recording-{index}', title=f'Song {index}', artist='Harbor Lights',
            album='Record', album_artist='Harbor Lights', track_number=index,
            duration_ns=(180 + index) * 1_000_000_000))
        tracks.append(track)
    window = TideWindow(app); window.set_default_size(1280, 800); window.present()
    settle(lambda: count(window) == 4 and window.get_width() > 0)
    window.show_view('songs')
    first = aligned(window)
    assert first.credit.get_label() == 'Record · Harbor Lights'
    assert first.song.library_sources == ('This device',)
    assert len(app.store.copies_for_track(first.song.id)) == 1

    server = app.store.add_source('Navidrome', 'subsonic', 'https://music.example.test', local=False)
    _, remote_copy = app.store.upsert_copy(server.id, MediaMetadata(
        uri='https://music.example.test/rest/stream?id=1', content_digest='', source_item_id='1',
        recording_id='recording-1', title='Song 1', artist='Harbor Lights', album='Record',
        album_artist='Harbor Lights', track_number=1, duration_ns=181_000_000_000))
    window.refresh_library()
    settle(lambda: set(window.library.song(tracks[0].id)[1].library_sources) == {'This device', 'Navidrome'})
    first = aligned(window)
    assert len(app.store.copies_for_track(first.song.id)) == 2
    assert len(app.store.copies_for_track(rows(window)[1].song.id)) == 1
    assert dict(window.library.source(local.id).facts)['Songs'] == '4'
    assert dict(window.library.source(server.id).facts)['Songs'] == '1'

    # Copy switching preserves the playing record, queue and seek position.
    app.controller.replace_queue([tracks[1].id, tracks[0].id], start=0)
    assert len(app.store.copies_for_track(app.controller.snapshot.track.id)) == 1
    app.controller.next()
    assert len(app.store.copies_for_track(app.controller.snapshot.track.id)) == 2
    app.controller.seek(12_000_000_000)
    before = app.controller.snapshot
    app.controller.switch_copy(remote_copy)
    assert app.controller.snapshot.track.id == before.track.id
    assert app.controller.snapshot.position_ns == before.position_ns
    assert app.controller.snapshot.queue_position == before.queue_position
    assert app.controller.snapshot.copy.id == remote_copy

    # Inline Love acts on the real subject, including a rebound row.
    first = rows(window)[0]
    first.love.set_active(True)
    assert app.store.track(first.song.id).favorite
    settle(lambda: rows(window)[0].song.loved)
    rows(window)[0].love.set_active(False)
    assert not app.store.track(tracks[0].id).favorite
    settle(lambda: not rows(window)[0].song.loved)
    first = rows(window)[0]
    album, second = window.library.song(tracks[1].id)
    first.set_subject(album, second, window.player)
    first.love.set_active(True)
    assert app.store.track(tracks[1].id).favorite and not app.store.track(tracks[0].id).favorite
    settle(lambda: window.library.song(tracks[1].id)[1].loved)

    window.open_album('Record', 'Harbor Lights')
    settle(lambda: window.view == 'album' and len(rows(window)) == 4)
    assert all(not row.show_album and not hasattr(row, 'credit') for row in rows(window))
    assert all(row.time.get_visible() for row in rows(window))
    app.controller.replace_queue([], play=False)
    settle(lambda: window.player.song_id is None, what='cleared player UI')
    named(window, 'td-play-album').emit('clicked')
    assert [entry.track.id for entry in app.store.queue()] == [track.id for track in app.store.tracks(album='Record', artist='Harbor Lights')]
    playlist = app.store.create_playlist('Evening')
    app.store.set_playlist_tracks(playlist, [tracks[0].id])
    assert [track.id for track in app.store.playlist_tracks(playlist)] == [tracks[0].id]
    print('font:', rows(window)[0].time.get_pango_context().get_font_description().get_family())

    window.show_view('artists')
    releases = next(releases for artist, releases in window.library.artists if artist == 'Harbor Lights')
    assert len(releases) == len(app.store.albums(artist='Harbor Lights'))
    named(window, 'td-artist-Harbor Lights').emit('clicked')
    assert window.view == 'album' and window.trail.current.title == 'Record'
    window.open_sources()
    assert named(window, 'td-source-' + local.id) is not None and named(window, 'td-source-' + server.id) is not None
    window.unmaximize(); window.set_default_size(420, 720)
    # Xvfb has no window manager to apply a mapped window's resize request.
    xdo('windowsize', '--sync', window_id(window), '420', '720')
    settle(lambda: window.get_surface().get_width() == 420,
           what='420px phone window including its kit frame')
    window.pane.close()
    window.open_album('Record', 'Harbor Lights')
    settle(lambda: all(row.get_mapped() for row in rows(window)))
    assert all(row.time.get_visible() for row in rows(window))
    window.show_view('songs')
    settle(lambda: len(rows(window)) == 4 and all(row.get_mapped() for row in rows(window)))
    assert named(window, 'td-table-header') is None, 'phone list should use its compact rows'
    assert all(row.time.get_visible() for row in rows(window))
    assert count(window) == 4 and app.store.playlist_tracks(playlist)[0].id == tracks[0].id
print('PASS: fixed aligned columns, real source/copy counts and switching, rebound favorites, album queue, playlist integrity, artist releases and phone layouts')
