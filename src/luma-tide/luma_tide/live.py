# SPDX-License-Identifier: Apache-2.0
"""Adapt Tide's existing stores/controller to the LumaUI presentation.

Library loading runs in the window's worker. This adapter adds no schema or
sync machinery. Secrets and stream URLs never enter presentation values.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import urlsplit

from .model import RepeatMode, SourceState
from .playback import PlaybackState
from .presentation import Album, Library, MusicSource, Player, Playlist, Song
from .subsonic import CertificateError, Unreachable, has_scheme, is_encrypted, normalize_address
from .remote import accepts_plaintext
from .recency import AlbumRecency


# The last library view, kept so a start draws it at once (metadata only, never audio).
SNAPSHOT_VERSION = 1
SNAPSHOT_SPACING = 30.0


def snapshot_path():
    cache = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
    return os.path.join(cache, 'luma-tide', 'library-view.pickle')


def album_id(title, artist):
    return 'live-' + hashlib.sha256((title + '\0' + artist).encode()).hexdigest()[:20]


class LiveLibrary:
    fixture = False
    keep_snapshots = False  # the window turns this on; tests and tools never write the cache
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
        self._played_input, self._played_output = None, ()

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

    @property
    def playback_error(self):
        """Why the current song can't play (the controller's message), or None."""
        return self.controller.snapshot.error or None

    def load(self):
        sources = self.store.sources()
        signed_out = {s.id for s in sources if s.state == SourceState.AUTH_REQUIRED}
        rows, pairs = self.store.library_listing()
        names = {source.id: source.name for source in sources}
        counts = defaultdict(int)
        track_sources = defaultdict(list)
        for track_id, source_id in pairs:
            counts[source_id] += 1
            name = names.get(source_id)
            if name is not None and name not in track_sources[track_id]:
                track_sources[track_id].append(name)
        if signed_out:
            # A signed-out source's songs show only where another copy plays: a signed-in
            # source's or a finished download (one query, not one per song).
            playable = {track_id for track_id, source_id in pairs if source_id not in signed_out}
            playable |= self.store.offline_ready_tracks()
            rows = [row for row in rows if row['id'] in playable]
        groups = defaultdict(list)
        for row in rows:
            groups[row['album'], row['album_artist']].append(row)
        artwork = {(title, artist): uri for title, artist, _year, _count, uri in self.store.albums()}
        albums = []
        for (title, artist), items in groups.items():
            identifier = album_id(title, artist)
            songs = tuple(Song(row['id'], row['title'], identifier, row['duration_ns'] // 1_000_000_000,
                               row['track_number'] or index + 1, bool(row['favorite']),
                               tuple(track_sources.get(row['id'], ())))
                          for index, row in enumerate(items))
            albums.append(Album(identifier, title, artist, max(row['year'] for row in items), songs,
                                artwork.get((title, artist))))
        rows = []
        for source in sources:
            count = counts[source.id]
            remote = source.kind == 'subsonic'
            state = 'ok' if source.state == SourceState.ONLINE else 'busy' if source.state == SourceState.SYNCING else 'bad'
            remote_sources = getattr(self.app, 'remote', None)
            locked = remote and remote_sources is not None and remote_sources.keyring_locked(source.id)
            subtitle = ('Signed out' if source.state == SourceState.AUTH_REQUIRED else
                        f'Keyring locked · {count:,} songs' if locked else
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
                                    subtitle, tuple(facts), remote,
                                    signed_out=source.state == SourceState.AUTH_REQUIRED))
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
        playlists = tuple(Playlist(identifier, name, tuple(track.id for track in self.store.playlist_tracks(identifier)))
                          for identifier, name, _count in self.store.playlists())
        known = {album.id for album in albums}
        self._library = Library(self._recency.order(albums), tuple(rows), playlists,
                                tuple(identifier for identifier in self._recency.played() if identifier in known))
        self._keep_snapshot(self._library)
        return self._library

    def snapshot(self):
        """The library view the last run read, for the first frame (about 0.1 s for 19k songs
        on a Raspberry Pi 5), or None. The real read follows and replaces it in place."""
        try:
            with open(snapshot_path(), 'rb') as handle:
                version, library = pickle.load(handle)  # Tide's own cache file, written below
        except Exception:  # noqa: BLE001 - absent, old or damaged: read the library as usual
            return None
        return library if version == SNAPSHOT_VERSION and isinstance(library, Library) else None

    def _keep_snapshot(self, library):
        """Write the view at most every SNAPSHOT_SPACING seconds (a sync reads often), atomically."""
        now = time.monotonic()
        if not self.keep_snapshots or now - getattr(self, '_snapshot_at', -SNAPSHOT_SPACING) < SNAPSHOT_SPACING:
            return
        self._snapshot_at = now
        path = snapshot_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            partial = f'{path}.{os.getpid()}.part'
            with open(partial, 'wb') as handle:
                pickle.dump((SNAPSHOT_VERSION, library), handle, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(partial, path)
        except OSError:
            logging.getLogger(__name__).warning('Cannot keep Tide library snapshot', exc_info=True)

    def order_albums(self, library):
        albums = self._recency.order(library.albums)
        played = self._recency.played()
        if played != self._played_input:
            known = {album.id for album in library.albums}
            self._played_input, self._played_output = played, tuple(i for i in played if i in known)
        if albums != library.albums or self._played_output != library.played:
            return replace(library, albums=albums, played=self._played_output)
        return library

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

    def play_ids(self, track_ids, start=0, shuffle=False):
        """Play a list of songs (a playlist, an artist's songs) from `start`."""
        ids = list(track_ids)
        if not ids:
            return
        if shuffle:
            import random
            start = random.randrange(len(ids))
        self.controller.set_shuffle(shuffle)
        self.controller.replace_queue(ids, start=max(0, min(start, len(ids) - 1)), play=True)

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
