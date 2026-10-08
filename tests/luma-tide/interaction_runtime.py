#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real input on v70 song buttons, queue/search rows and source actions.

v70 data-tplay is single-click playback, not the retired select/double-click
list. Its source face has a 9px state mark; Sources uses Sync/More instead of a
hover-only eye. Keep playback isolation, keyboard, filtering and state checks
on these actual port controls and the retained visibility store contract.
"""
from unittest.mock import patch
from lumaui_runtime import click, count, key, named, settle, test_application, titles, walk, xdo
from gi.repository import Gtk
from luma_tide.model import MediaMetadata, SourceState
from luma_tide.presentation import next_queue
from luma_tide.ui import TideWindow
from luma_tide.ui_parts import SourceFace

with test_application('org.projectluma.TideInteractionTest') as (app, root):
    local = app.store.add_source('This device', 'local-folder', root, local=True)
    server = app.store.add_source('Navidrome', 'subsonic', 'https://music.example.test', local=False)
    for index in range(1, 4):
        app.store.upsert_copy(local.id, MediaMetadata(
            uri=(root / f'{index}.flac').as_uri(), content_digest=str(index) * 64,
            title=f'Song {index}', artist='Artist', album='Record', track_number=index,
            duration_ns=60_000_000_000))
    remote, _ = app.store.upsert_copy(server.id, MediaMetadata(
        uri='https://music.example.test/rest/stream?id=9', content_digest='', source_item_id='9',
        title='Server song', artist='Artist', album='Record', track_number=4,
        duration_ns=60_000_000_000))
    window = TideWindow(app); window.set_title('TideInteractionRuntimeTest')
    window.set_default_size(1280, 800); window.present()
    settle(lambda: count(window) == 4 and window.get_width() > 0)
    window.show_view('songs')
    def row(value):
        return next(item for item in window._rows if item.song.title == value)
    click(window, row('Song 1').play)
    settle(lambda: app.controller.snapshot.track is not None and row('Song 1').playing)
    assert app.controller.snapshot.track.title == 'Song 1'
    assert app.controller.snapshot.queue_length == 4
    xdo('mousemove', '--sync', '0', '0')
    settle(lambda: row('Song 1').index.get_visible_child_name() == 'playing')
    before = app.controller.snapshot
    click(window, row('Song 1').play)
    settle(lambda: not window.player.playing)
    assert app.controller.snapshot.track.id == before.track.id
    assert app.controller.snapshot.position_ns == before.position_ns
    assert app.controller.snapshot.queue_length == before.queue_length
    key(window, row('Song 3').play, 'Return')
    settle(lambda: app.controller.snapshot.track.title == 'Song 3' and window.player.playing)
    key(window, row('Song 2').play, 'space')
    settle(lambda: app.controller.snapshot.track.title == 'Song 2' and window.player.playing)

    # Queue and search controls use the same one-activation contract.
    window.toggle_queue()
    settle(lambda: window.pane.shown and window.pane_kind == 'queue')
    following = next_queue(window.player)[1]
    target = named(window, 'td-queue-' + following)
    assert target is not None
    click(window, target if isinstance(target, Gtk.Button) else target.button)
    settle(lambda: app.controller.snapshot.track.id == following)
    window.pane.close()
    window.open_search(); window.search_item.entry.set_text('Song 2')
    settle(lambda: len(window._rows) == 1 and row('Song 2') is not None)
    click(window, row('Song 2').play)
    settle(lambda: app.controller.snapshot.track.title == 'Song 2')
    window.search_item.entry.set_text('')
    settle(lambda: window.query == '' and len(window._rows) == 4)
    key(window, None, 'Escape')
    settle(lambda: not window.search.get_visible(), what='Escape closes search')
    settle(lambda: len(window._rows) == 4)
    window.open_sources()

    def source_face():
        button = named(window, 'td-source-' + server.id)
        return next(item for item in walk(button) if isinstance(item, SourceFace))
    settle(lambda: source_face().get_mapped())
    assert source_face().side == 32 and source_face().dot == 9
    assert source_face().state == 'ok'
    for state, mark in ((SourceState.SYNCING, 'busy'), (SourceState.OFFLINE, 'bad'), (SourceState.ONLINE, 'ok')):
        app.store.set_source_state(server.id, state); window.refresh_library()
        settle(lambda mark=mark: source_face().state == mark, what='source state mark')
        assert source_face().dot == 9

    # Hiding only changes visibility, never the cached records or state mark.
    app.store.set_source_shown(server.id, False); window.refresh_library()
    settle(lambda: count(window) == 3)
    assert 'Server song' not in titles(window) and app.store.track(remote.id).title == 'Server song'
    assert window.library.source(server.id).state == 'ok' and source_face().state == 'ok'
    app.store.set_source_shown(server.id, True); window.refresh_library()
    settle(lambda: count(window) == 4)

    # More stays pointer/keyboard reachable; Menu and Shift-F10 invoke the
    # same actual source menu, without activating the row or playback.
    before = app.controller.snapshot
    def menu_visible():
        return any(isinstance(widget, Gtk.Label) and widget.get_text() == 'Remove'
                   and widget.get_mapped() for widget in walk(window))
    with patch.object(window, '_source_menu', wraps=window._source_menu) as menu:
        click(window, named(window, 'td-source-more-' + server.id))
        settle(menu_visible, what='source menu')
        menu.assert_called_once_with(server.id)
        key(window, None, 'Escape'); settle(lambda: not menu_visible(), what='closing source menu')
        menu.reset_mock()
        key(window, named(window, 'td-source-more-' + server.id), 'Return')
        settle(menu_visible, what='keyboard More')
        menu.assert_called_once_with(server.id)
        key(window, None, 'Escape'); settle(lambda: not menu_visible())
        menu.reset_mock()
        key(window, named(window, 'td-source-' + server.id), 'shift+F10')
        settle(menu_visible, what='source context key')
        menu.assert_called_once_with(server.id)
        assert window.pane_kind == 'sources' and window.source_id is None
        assert app.controller.snapshot == before
        key(window, None, 'Escape'); settle(lambda: not menu_visible())
print('PASS: real mouse/Enter/Space playback, pause state, queue/search, source states and retained filters, mouse/keyboard source menus')
