#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Tide must stay responsive with a server-sized library.

Builds a 20,000-song library, opens the real window, plays a song from the
library, runs playback position updates, and syncs another 20,000 songs on a
worker thread while the main loop keeps a 16 ms timer. It reports timings and
fails when the window would feel frozen.

Run under any GTK display, for example Broadway or Xvfb:
    PYTHONPATH=src/luma-tide:tests/luma-tide python3 tests/luma-tide/large_library_runtime.py
"""
import concurrent.futures
import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from luma_appkit import add_style_sheet, install_appkit, install_lumaui  # noqa: E402
import luma_tide
from lumaui_runtime import count, settle
from luma_tide.application import TideWindow  # noqa: E402
from luma_tide.model import LibraryStore, MediaMetadata  # noqa: E402
from luma_tide.resources import stylesheet_path
from luma_tide.playback import PlaybackController  # noqa: E402
from test_playback import FakeEngine  # noqa: E402

SONGS = int(os.environ.get("TIDE_LARGE_SONGS", "20000"))
# Generous bounds for a shared builder; the report shows the real numbers.
BUDGET = {"open": 8.0, "artists": 1.5, "albums": 1.5, "play": 2.0, "tick": 0.25, "sync_stall": 0.5, "background_refresh_stall": 0.5,
          "refresh": 3.0, "filter_stall": 0.5, "filter_seconds": 5.0,
          "search_stall": 0.5, "search_seconds": 5.0}


def songs(source_id: str, root: Path, start: int, count: int) -> list[MediaMetadata]:
    return [
        MediaMetadata(
            uri=f"https://music.example.test/rest/stream?id={index}", content_digest="",
            source_item_id=f"song-{index}", title=f"Song {index}", artist=f"Artist {index // 20}",
            album=f"Album {index // 12}", album_artist=f"Artist {index // 20}",
            duration_ns=180_000_000_000, track_number=index % 12 + 1, year=2000 + index % 25,
        )
        for index in range(start, start + count)
    ]


def drain(limit: float = 30.0) -> float:
    """Run the main loop until idle; returns seconds taken."""
    context = GLib.MainContext.default()
    started = time.monotonic()
    while context.pending():
        context.iteration(False)
        if time.monotonic() - started > limit:
            raise AssertionError("main loop never became idle")
    return time.monotonic() - started


def timed(label: str, function, results: dict) -> None:
    started = time.monotonic()
    function()
    drain()
    results[label] = time.monotonic() - started


def main() -> None:
    results: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
            os.environ[key] = str(root / key)
        os.environ['GSETTINGS_BACKEND'] = 'memory'
        store = LibraryStore(root / "library.db")
        source = store.add_source("Server", "subsonic", "https://music.example.test", local=False)
        for start in range(0, SONGS, 500):
            store.upsert_copies(source.id, songs(source.id, root, start, 500))

        app = Adw.Application(application_id="org.projectluma.TideLargeTest",
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        assert app.register(None)
        install_appkit()
        install_lumaui()
        add_style_sheet(str(stylesheet_path()))
        app.store = store
        app.controller = PlaybackController(store, FakeEngine())
        # No server: artist portraits (2.luma.27) resolve to none at once.
        def no_artwork(_names, _changed=None):
            future = concurrent.futures.Future()
            future.set_result({})
            return future
        app.remote = SimpleNamespace(connected=lambda _source_id: True,
                                     sources=lambda: [],
                                     artist_artwork=no_artwork)
        app.can_make_offline = lambda ids: False
        app.has_offline = lambda ids: False
        app.notify_error = lambda message: None
        app._syncing = set()

        window = None

        def open_window() -> None:
            nonlocal window
            window = TideWindow(app)
            window.present()
            settle(lambda: count(window) == SONGS, timeout=30, what='initial library')

        timed("open", open_window, results)
        window.show_view('songs')
        assert len(window._rows) < 100, 'a server-sized Songs view allocated every row'
        timed("artists", lambda: window.show_view('artists'), results)
        assert 0 < window._tile_next < 128, 'Artists allocated the full server library'
        timed("albums", lambda: window.show_view('albums'), results)
        assert 0 < window._tile_next < 128, 'Albums allocated the full server library'
        window.show_view('songs')

        first = store.tracks(limit=1)[0]
        timed("play", lambda: window.play_song(*window.library.song(first.id)), results)
        assert app.controller.snapshot.track.id == first.id

        ticks = []
        for position in range(1, 21):
            started = time.monotonic()
            app.controller._on_engine_event("position", position * 500_000_000)
            drain()
            ticks.append(time.monotonic() - started)
        results["tick"] = max(ticks)

        # A sync on a worker while the main loop keeps a 16 ms timer.
        gaps = []
        last = [time.monotonic()]

        def beat() -> bool:
            now = time.monotonic()
            gaps.append(now - last[0])
            last[0] = now
            return GLib.SOURCE_CONTINUE

        finished = threading.Event()

        def sync() -> None:
            for start in range(SONGS, SONGS * 2, 500):
                store.upsert_copies(source.id, songs(source.id, root, start, 500))
                GLib.idle_add(lambda: window.library_changed() or GLib.SOURCE_REMOVE)
            finished.set()

        timer = GLib.timeout_add(16, beat)
        worker = threading.Thread(target=sync)
        worker.start()
        context = GLib.MainContext.default()
        while not finished.is_set() or context.pending():
            context.iteration(True)
        worker.join()
        GLib.source_remove(timer)
        results["sync_stall"] = max(gaps) if gaps else 0.0

        # The refresh a sync asks for reads on a worker; the window keeps beating.
        gaps.clear()
        last[0] = time.monotonic()
        timer = GLib.timeout_add(16, beat)
        previous = window.library
        window.library_changed(soon=True)
        deadline = time.monotonic() + 60
        while count(window) != SONGS * 2 or window.library is previous:
            context.iteration(True)
            assert time.monotonic() < deadline, "the background refresh never arrived"
        drain()
        GLib.source_remove(timer)
        results["background_refresh_stall"] = max(gaps) if gaps else 0.0

        def refresh():
            previous = window.library
            window.refresh_library()
            settle(lambda: window.library is not previous and count(window) == SONGS * 2,
                   timeout=30, what='full refresh')
        timed("refresh", refresh, results)

        # Show only a small local source, then everything again, with a
        # source hidden so the filter query really runs.
        local = store.add_source("Here", "local-folder", root / "music", local=True)
        store.upsert_copies(local.id, [
            MediaMetadata(uri=(root / f"music/{index}.flac").as_uri(), content_digest=f"{index:064d}",
                          title=f"Here {index}", duration_ns=1)
            for index in range(300)
        ])
        gaps.clear()
        started = last[0] = time.monotonic()
        timer = GLib.timeout_add(16, beat)
        store.show_only_source(local.id)
        window.refresh_library()
        deadline = time.monotonic() + 60
        while count(window) != 300:
            context.iteration(True)
            assert time.monotonic() < deadline, "the filtered library never arrived"
        results["filter_seconds"] = time.monotonic() - started
        store.show_all_sources()
        window.refresh_library()
        while count(window) != SONGS * 2 + 300:
            context.iteration(True)
            assert time.monotonic() < deadline + 60, "showing all sources never arrived"
        drain()
        GLib.source_remove(timer)
        results["filter_stall"] = max(gaps) if gaps else 0.0
        # Search groups matches while song rows stay bounded to the viewport.
        gaps.clear()
        started = last[0] = time.monotonic()
        timer = GLib.timeout_add(16, beat)
        window.open_search()
        for typed in ("S", "So", "Son", "Song 1"):
            window.search_item.entry.set_text(typed)
        deadline = time.monotonic() + 60
        while window.query != 'Song 1' or not window._rows:
            context.iteration(True)
            assert time.monotonic() < deadline, "search results never arrived"
        results["search_seconds"] = time.monotonic() - started
        drain()
        GLib.source_remove(timer)
        results["search_stall"] = max(gaps) if gaps else 0.0
        assert window.view == 'songs' and window.trail.current.title == 'Results'
        assert all('song 1' in row.song.title.casefold() for row in window._rows)
        assert len(window._rows) < 100
        window.search_item.entry.set_text('')
        visible = count(window) - 300
        print("timings:", ", ".join(f"{name} {value:.3f}s" for name, value in results.items()), flush=True)
        assert visible == SONGS * 2, f"library shows {visible} songs, expected {SONGS * 2}"

        window.close_resources()
        window.close()
        app.controller.close()
        store.close()

    report = ", ".join(f"{name} {value:.3f}s" for name, value in results.items())
    # A shared builder busier than its cores stretches every wall-clock gap;
    # the budget stretches with it, and never below the idle budget.
    load = max(1.0, os.getloadavg()[0] / (os.cpu_count() or 1))
    over = [name for name, value in results.items() if value > BUDGET[name] * load]
    if load > 1.0:
        print(f"builder load {load:.2f}x its cores; budgets scaled to match", flush=True)
    if over:
        raise AssertionError(f"Tide stalled with {SONGS * 2:,} songs ({', '.join(over)}): {report}")
    print(f"PASS: Tide stays responsive with {SONGS * 2:,} songs: {report}")


if __name__ == "__main__":
    main()
