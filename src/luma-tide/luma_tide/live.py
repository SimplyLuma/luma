# SPDX-License-Identifier: Apache-2.0
"""Adapt Tide's existing stores/controller to the LumaUI presentation.

Library loading runs in the window's worker. This adapter adds no schema or
sync machinery. Secrets and stream URLs never enter presentation values.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import urlsplit

from .model import RepeatMode, SourceState
from .playback import PlaybackState
from .presentation import Album, Library, MusicSource, Player, Song
from .subsonic import CertificateError, Unreachable, has_scheme, is_encrypted, normalize_address
from .remote import accepts_plaintext
from .recency import AlbumRecency


def album_id(title, artist):
    return 'live-' + hashlib.sha256((title + '\0' + artist).encode()).hexdigest()[:20]


class LiveLibrary:
    fixture = False
    view = 'albums'
    album_id = None

    def __init__(self, application):
        self.app = application
        self.store, self.controller = application.store, application.controller
        self._library = Library()
        self._album_loves = set()
        self._stops = []
        self._queue_revision = -1
        self._queue_ids = ()
        self._recency = AlbumRecency(self.store.path.parent / 'album-history.json')
        self._history_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tide-history')
        self._last_playing_track = None
        self._history_lock = threading.RLock()
        self._closed = False

    @property
    def player(self):
        value = self.controller.snapshot
        if self._queue_revision != self.store.queue_revision:
            self._queue_ids = tuple(item.track.id for item in self.store.queue())
            self._queue_revision = self.store.queue_revision
        return Player(value.track.id if value.track else None, value.state == PlaybackState.PLAYING,
                      value.position_ns // 1_000_000_000, round(value.volume * 100), value.shuffle,
                      value.repeat != RepeatMode.OFF, self._queue_ids,
                      'This computer', (('This computer', 'monitor', 'System audio output'),))

    def load(self):
        sources = self.store.sources()
        signed_out = {s.id for s in sources if s.state == SourceState.AUTH_REQUIRED}
        tracks = self.store.tracks()
        if signed_out:
            tracks = [track for track in tracks if any(
                copy.source_id not in signed_out or copy.offline_ready
                for copy in self.store.copies_for_track(track.id))]
        groups = defaultdict(list)
        for track in tracks:
            groups[track.album, track.album_artist].append(track)
        artwork = {(title, artist): uri for title, artist, _year, _count, uri in self.store.albums()}
        albums = []
        for (title, artist), items in groups.items():
            identifier = album_id(title, artist)
            songs = tuple(Song(track.id, track.title, identifier, track.duration_ns // 1_000_000_000,
                               track.track_number or index + 1, track.favorite)
                          for index, track in enumerate(items))
            albums.append(Album(identifier, title, artist, max(t.year for t in items), songs,
                                artwork.get((title, artist))))
        rows = []
        track_sources = defaultdict(list)
        for source in sources:
            source_tracks = self.store.tracks(source_id=source.id, shown_only=False)
            count = len(source_tracks)
            for track in source_tracks:
                if source.name not in track_sources[track.id]:
                    track_sources[track.id].append(source.name)
            remote = source.kind == 'subsonic'
            state = 'ok' if source.state == SourceState.ONLINE else 'busy' if source.state == SourceState.SYNCING else 'bad'
            subtitle = ('Signed out' if source.state == SourceState.AUTH_REQUIRED else
                        f'Syncing · {count:,} songs' if source.state == SourceState.SYNCING else
                        f'Up to date · {count:,} songs' if source.state == SourceState.ONLINE else
                        f'Out of reach · {count:,} songs')
            if remote:
                facts = [('Server', urlsplit(source.uri).netloc), ('Account', source.account or ''),
                         ('Songs', f'{count:,}')]
                if source.last_seen:
                    import datetime
                    facts.append(('Last sync', datetime.datetime.fromtimestamp(source.last_seen).strftime('%b %-d, %-I:%M %p')))
                if source.auth_ref and source.state != SourceState.AUTH_REQUIRED:
                    facts.append(('Password', 'In your keyring'))
            else:
                facts = [('Folder', source.uri), ('Songs', f'{count:,}'), ('Last sync', 'Watching for changes')]
            rows.append(MusicSource(source.id, source.name, 'server' if remote else 'monitor', state,
                                    subtitle, tuple(facts), remote))
        albums = [replace(album, songs=tuple(
            replace(song, library_sources=tuple(track_sources[song.id]))
            for song in album.songs)) for album in albums]
        offline = self.store.offline_summary()
        if offline.complete or offline.pending or offline.failed:
            subtitle = f'Downloading {offline.pending} songs' if offline.pending else f'Kept offline · {offline.complete} songs'
            rows.append(MusicSource('downloads', 'Downloads', 'download', 'busy' if offline.pending else 'ok',
                                    subtitle, (('Kept offline', f'{offline.complete} songs · {offline.bytes:,} bytes'),
                                               ('Downloading', f'{offline.pending} songs'))))
        try:
            self._recency.load()
        except (OSError, ValueError):
            logging.getLogger(__name__).exception('Cannot read Tide album playback history')
        self._library = Library(self._recency.order(albums), tuple(rows))
        return self._library

    def order_albums(self, library):
        albums = self._recency.order(library.albums)
        return replace(library, albums=albums) if albums != library.albums else library

    def source_address(self, identifier):
        """Edit the saved URI, including its scheme/path, not the display host."""
        return self.store.source(identifier).uri

    def subscribe(self, callback):
        def changed(value):
            with self._history_lock:
                if self._closed:
                    return
                if value.track and value.state == PlaybackState.PLAYING and value.track.id != self._last_playing_track:
                    self._last_playing_track = value.track.id
                    identifier = album_id(value.track.album, value.track.album_artist)
                    future = self._history_worker.submit(self._recency.record, identifier)
                    def recorded(done):
                        try:
                            done.result()
                        except (OSError, ValueError):
                            logging.getLogger(__name__).exception('Cannot save Tide album playback history')
                        else:
                            with self._history_lock:
                                if not self._closed:
                                    callback(self.player)
                    future.add_done_callback(recorded)
                callback(self.player)
        stop = self.controller.subscribe(changed)
        self._stops.append(stop)
        return stop

    def play(self, album, song=None, shuffle=False):
        if not album.songs:
            return
        if song is None and self.controller.snapshot.track and self.controller.snapshot.track.id in {s.id for s in album.songs} and not shuffle:
            self.toggle()
            return
        import random
        song = song or (random.choice(album.songs) if shuffle else album.songs[0])
        if self.controller.snapshot.track and song.id == self.controller.snapshot.track.id and not shuffle:
            self.toggle()
            return
        ids = [s.id for s in album.songs]
        self.controller.set_shuffle(shuffle)
        self.controller.replace_queue(ids, start=ids.index(song.id), play=True)

    def toggle(self):
        self.controller.toggle()

    def step(self, delta):
        (self.controller.previous if delta < 0 else self.controller.next)()

    def seek(self, seconds):
        self.controller.seek(max(0, int(seconds)) * 1_000_000_000)

    def set_volume(self, value):
        self.controller.set_volume(max(0, min(100, value)) / 100)

    def shuffle(self):
        self.controller.set_shuffle(not self.controller.snapshot.shuffle)

    def repeat(self):
        self.controller.set_repeat(RepeatMode.OFF if self.controller.snapshot.repeat != RepeatMode.OFF else RepeatMode.ALL)

    def love(self, song_id):
        track = self.store.track(song_id)
        self.store.set_favorite(song_id, not track.favorite)
        self.controller.refresh_metadata()

    def love_album(self, identifier):
        album = self._library.album(identifier)
        loved = all(song.loved for song in album.songs)
        for song in album.songs:
            self.store.set_favorite(song.id, not loved)
        self.controller.refresh_metadata()

    def clear_queue(self):
        # Existing queue API; remove from the end to preserve the current index.
        current = self.controller.snapshot.queue_position
        for index in range(self.store.queue_length() - 1, current, -1):
            self.store.remove_queue_item(index)
        self.controller.reload_queue()

    def output(self, name):
        if name != 'This computer':
            raise ValueError('This output is not available')

    def sync(self, identifier):
        if identifier == 'downloads':
            self.app.remote.pump_downloads(self.app._downloads_changed)
        else:
            self.app.scan_source(self.store.source(identifier))

    def show_folder(self, identifier):
        from gi.repository import Gio
        if identifier == 'downloads':
            path = self.app.remote.offline_root
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
        else:
            Gio.AppInfo.launch_default_for_uri(self.store.source(identifier).uri, None)

    def remove_source(self, identifier):
        self.app._remove_source(self.store.source(identifier))

    def sign_out(self, identifier, callback):
        future = self.app.remote.sign_out(identifier)
        def done(result):
            try:
                undo = result.result()
            except Exception:
                callback(None, 'The server was not signed out. Your saved login may still be in the keyring.')
            else:
                from gi.repository import GLib
                GLib.idle_add(self._sign_out_finished, identifier, undo, callback)
        future.add_done_callback(done)

    def _sign_out_finished(self, identifier, undo, callback):
        current = self.controller.snapshot.copy
        if current and current.source_id == identifier and not current.here:
            self.controller.stop()
        callback(undo, '')
        return False

    def undo_sign_out(self, undo, callback):
        from gi.repository import GLib
        self.app.remote.undo_sign_out(undo).add_done_callback(lambda result: GLib.idle_add(self._undone, result, callback))

    def _undone(self, result, callback):
        try:
            result.result()
        except Exception:
            self.app.notify_error('The sign-out could not be undone. Sign in again to reconnect.')
        callback()
        return False

    def connect(self, address, username, password, source_id=None, *, callback, where):
        from gi.repository import GLib
        from luma_appkit import DestructiveDialog
        try:
            normalized = normalize_address(address)
        except ValueError as error:
            callback(str(error))
            return
        current = self.store.source(source_id) if source_id else None
        # Blank on Edit means unchanged: retrieve only on the account worker.
        if not password:
            if current and current.auth_ref:
                future = self.app.remote._accounts.submit(lambda: self.app.remote.credentials().lookup(current.auth_ref))
                def retrieved(result):
                    try:
                        saved = result.result()
                    except Exception:
                        saved = None
                    if not saved:
                        GLib.idle_add(lambda: callback('Enter the account’s password.') or False)
                    else:
                        GLib.idle_add(lambda: self.connect(address, username, saved, source_id, callback=callback, where=where) or False)
                future.add_done_callback(retrieved)
            else:
                callback('Enter the account’s password.')
            return
        def submit(uri, plain=False, fallback=False):
            future = self.app.remote.add_server(uri, username, password, allow_plaintext=plain)
            def finished(result):
                try:
                    source = result.result()
                except (CertificateError, Unreachable):
                    if fallback:
                        GLib.idle_add(lambda: confirm('http://' + uri.removeprefix('https://')) or False)
                    else:
                        callback('The server is out of reach. Check the address and connection.')
                except Exception:
                    callback('The server did not accept the account. Check the sign-in details.')
                else:
                    GLib.idle_add(lambda: self.app.source_added(source, signed_in_again=bool(current)) or False)
                    callback('')
            future.add_done_callback(finished)
        def confirm(uri):
            DestructiveDialog.ask(where, title='Connect without encryption?',
                body='Anyone who can see this connection could capture a sign-in token. Continue only on a network you trust.',
                action='Connect', icon='shield', on_confirm=lambda _: submit(uri, True),
                on_cancel=lambda: callback('Connection cancelled.'))
        if is_encrypted(normalized):
            submit(normalized, fallback=not has_scheme(address))
        elif current and current.uri == normalized and accepts_plaintext(current):
            submit(normalized, True)
        else:
            confirm(normalized)

    def close(self):
        with self._history_lock:
            self._closed = True
            for stop in self._stops:
                stop()
            self._stops.clear()
            self._history_worker.shutdown(wait=False)
