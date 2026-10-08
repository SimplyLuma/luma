#!/usr/bin/python3
"""Exercise grouped search and source choice without showing a window."""
from __future__ import annotations

from lumaui_runtime import named, pump, settle, test_application
from luma_tide.model import MediaMetadata
from luma_tide.ui import TideWindow


with test_application('org.projectluma.TideSearchPanelTest') as (app, root):
    source = app.store.add_source('Music', 'local-folder', root, local=True)
    app.store.upsert_copy(source.id, MediaMetadata(
        uri=(root / 'one.flac').as_uri(), content_digest='a' * 64,
        title='Blue Morning', artist='Blue Notes', album='Blue Sky',
        duration_ns=60_000_000_000))
    window = TideWindow(app)
    settle(lambda: len(window.library.albums) == 1)
    window.open_search()
    assert window.search.get_parent() is window.corner.normal
    window.search_item.entry.set_text('blue')
    settle(lambda: window.query == 'blue' and window.view == 'songs')
    assert window.trail.current.title == 'Results'
    assert window._rows[0].song.title == 'Blue Morning'
    assert len(window._rows) == 1
    window.close_search()
    assert window.view == 'albums' and not window.search.get_visible()
    window.open_sources()
    window._add_source()
    assert window.source_id == 'add'
    assert window.server_address.get_visible() and window.connect_button.get_visible()
    window._add_server()
    pump()
    assert window.source_id == 'add-server' and window.server_address.text == ''

print('PASS: inline grouped search and folder/server source choice')
