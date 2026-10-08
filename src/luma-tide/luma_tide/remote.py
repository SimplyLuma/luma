# SPDX-License-Identifier: Apache-2.0
"""Navidrome and other Subsonic-compatible servers as Tide sources.

This module owns the remote source lifecycle: verifying an account before it is
saved, keeping the password in the keyring, syncing the server's songs into the
library, resolving a playable URL at the moment of playback, and downloading
copies for offline listening.

What is stored for a remote copy is credential-free: the server's base URL and
the song's ID. An authenticated URL exists only in memory, for one request or
one playback pipeline.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import uuid
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .credentials import CredentialStore, CredentialUnavailable, reference_for
from .model import (
    LibraryStore, MediaMetadata, OfflineItem, OfflineState, Source, SourceState, TrackCopy,
)
from .playback import PlaybackUnavailable
from .subsonic import (
    AuthenticationFailed, Cancelled, ServerInfo, SubsonicClient, SubsonicError, Throttled,
    Unreachable, is_encrypted,
)

_log = logging.getLogger("tide")

KIND = "subsonic"
CAPABILITIES = ("browse", "offline", "play", "stream")
# Recorded when the person accepted an unencrypted (plain HTTP) connection.
PLAINTEXT_CAPABILITY = "unencrypted"
# The name an earlier preview recorded when plain HTTP was limited to private networks.
_LEGACY_PLAINTEXT_CAPABILITY = "unencrypted-private-network"


def accepts_plaintext(source: Source) -> bool:
    return bool({PLAINTEXT_CAPABILITY, _LEGACY_PLAINTEXT_CAPABILITY} & set(source.capabilities))
DOWNLOAD_WORKERS = 2
# Artwork is decoration: fetch gently, one cover per album, and step back as
# soon as the server shows strain rather than queueing more work on it.
ARTWORK_WORKERS = 2
ARTWORK_STRIKES = 2
ARTWORK_BACKOFF_SECONDS = 15 * 60
COVERS_FILE = "covers.json"

Progress = Callable[[str, int, int], None]
Changed = Callable[[], None]


@dataclass(frozen=True, slots=True)
class SyncResult:
    source_id: str
    songs: int
    missing: int
    unchanged: bool
    artwork_failed: int


@dataclass(slots=True)
class SignOutUndo:
    source_id: str
    state: SourceState
    auth_ref: str | None
    account: str
    secret: str | None = field(repr=False)
    client: SubsonicClient | None = field(repr=False)


def copy_uri(address: str, song_id: str) -> str:
    return f"{address}/rest/stream?{urllib.parse.urlencode({'id': song_id})}"


def song_id_from_uri(uri: str) -> str:
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(uri).query).get("id")
    if not values or not values[0]:
        raise ValueError("not a Subsonic copy")
    return values[0]


def _number(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _names(value: object) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(
        str(item.get("name")).strip() for item in value
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    )


def _name_list(value: object) -> tuple[str, ...]:
    """The server's ordered artist credit (OpenSubsonic `artists` or
    `albumArtists`). Empty when the server sent only a single flat string."""
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item.get("name")).strip() for item in value
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    )


def _unpack_secret(secret: str, account: str | None) -> tuple[str | None, str]:
    """A saved sign-in is the password itself. Packaged Tide 2.luma.18 to
    2.luma.20 saved a JSON object holding the username too; both are read."""
    if secret.startswith("{"):
        try:
            payload = json.loads(secret)
            return str(payload["username"]), str(payload["password"])
        except (TypeError, ValueError, KeyError):
            pass
    return account, secret


def song_metadata(address: str, song: dict, artwork_uri: str | None) -> MediaMetadata:
    """Map an OpenSubsonic `Child` to Tide metadata. Durations arrive in
    seconds and bit rates in kbps; Tide stores nanoseconds and bits per second."""
    artist = str(song.get("displayArtist") or song.get("artist") or _names(song.get("artists")) or "")
    album_artist = str(
        song.get("displayAlbumArtist") or _names(song.get("albumArtists")) or ""
    )
    genre = str(song.get("genre") or "")
    if not genre and isinstance(song.get("genres"), list):
        genre = _names(song["genres"]).split(", ")[0]
    replay_gain = song.get("replayGain") if isinstance(song.get("replayGain"), dict) else {}
    track_gain = replay_gain.get("trackGain")
    return MediaMetadata(
        uri=copy_uri(address, str(song["id"])),
        content_digest="",
        title=str(song.get("title") or ""),
        artist=artist,
        album=str(song.get("album") or ""),
        album_artist=album_artist,
        artists=_name_list(song.get("artists")),
        album_artists=_name_list(song.get("albumArtists")),
        duration_ns=_number(song.get("duration")) * 1_000_000_000,
        disc_number=_number(song.get("discNumber")),
        track_number=_number(song.get("track")),
        year=_number(song.get("year")),
        genre=genre,
        format=str(song.get("suffix") or "").upper(),
        bitrate=_number(song.get("bitRate")) * 1000,
        sample_rate=_number(song.get("samplingRate")),
        channels=_number(song.get("channelCount")),
        size=_number(song.get("size")),
        artwork_uri=artwork_uri,
        recording_id=str(song.get("musicBrainzId") or "").strip() or None,
        replaygain_track_gain=float(track_gain) if isinstance(track_gain, (int, float)) else None,
        source_item_id=str(song["id"]),
    )


class RemoteSources:
    """Remote source work runs on one serialized worker; downloads on a small
    pool. Callbacks run on those threads, so a GTK caller must hop back to its
    main context before touching widgets."""

    def __init__(
        self,
        store: LibraryStore,
        credentials: Callable[[], CredentialStore],
        *,
        offline_root: Path | None = None,
        artwork_root: Path | None = None,
        client_factory: Callable[..., SubsonicClient] = SubsonicClient,
    ) -> None:
        self.store = store
        self._credential_factory = credentials
        self._credentials: CredentialStore | None = None
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        self.offline_root = offline_root or data_home / "luma-tide/offline"
        self.artwork_root = artwork_root or cache_home / "luma-tide/remote-artwork"
        self._client_factory = client_factory
        self._clients: dict[str, SubsonicClient] = {}
        self._artist_indexes: dict[str, dict[str, str]] = {}
        self._downloading: dict[str, tuple[str, threading.Event]] = {}
        self._sync_cancel: dict[str, threading.Event] = {}
        self._artwork_resume_at: dict[str, float] = {}
        self._lock = threading.RLock()
        self._closing = threading.Event()
        # Sync and removal are serialized so a source is never removed mid-sync.
        # Account checks run on their own worker, so adding a second server
        # doesn't wait behind a long first sync.
        self._work = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tide-remote"
        )
        self._accounts = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tide-accounts"
        )
        self._downloads = concurrent.futures.ThreadPoolExecutor(
            max_workers=DOWNLOAD_WORKERS, thread_name_prefix="tide-offline"
        )
        store.requeue_interrupted_offline()

    def close(self) -> None:
        self._closing.set()
        with self._lock:
            for _source_id, event in self._downloading.values():
                event.set()
            for event in self._sync_cancel.values():
                event.set()
        self._work.shutdown(wait=False, cancel_futures=True)
        self._accounts.shutdown(wait=False, cancel_futures=True)
        self._downloads.shutdown(wait=False, cancel_futures=True)

    def _submit(self, function, *args) -> concurrent.futures.Future:
        return self._work.submit(function, *args)

    def credentials(self) -> CredentialStore:
        with self._lock:
            if self._credentials is None:
                self._credentials = self._credential_factory()
            return self._credentials

    def sources(self) -> list[Source]:
        return [source for source in self.store.sources() if source.kind == KIND]

    def connected(self, source_id: str) -> bool:
        with self._lock:
            return source_id in self._clients

    def artist_artwork(self, names: list[str], changed=None) -> concurrent.futures.Future:
        """Read portraits through the configured server; never follow external image URLs."""
        return self._submit(self._artist_artwork, names, changed)

    def _artist_artwork(self, names: list[str], changed=None) -> dict[str, str]:
        images = {}
        for source in self.sources():
            if self._closing.is_set():
                break
            folder = self.artwork_root / source.id / "artists"
            wanted = {}
            for name in names:
                digest = hashlib.sha256(name.casefold().encode()).hexdigest()
                path = folder / f"{digest}.image"
                if path.is_file():
                    images.setdefault(name, path.as_uri())
                elif name not in images:
                    wanted[name] = path
            if changed and images:
                changed(dict(images))
            with self._lock:
                client = self._clients.get(source.id)
            if not wanted or client is None:
                continue
            try:
                if source.id not in self._artist_indexes:
                    response = client.request("getArtists").get("artists") or {}
                    index = {}
                    ambiguous = set()
                    for section in response.get("index", []):
                        for artist in section.get("artist", []):
                            name = str(artist.get("name") or "").casefold()
                            cover = str(artist.get("coverArt") or "")
                            if name in index:
                                ambiguous.add(name)
                            elif name and cover:
                                index[name] = cover
                    self._artist_indexes[source.id] = {name: cover for name, cover in index.items() if name not in ambiguous}
                index = self._artist_indexes[source.id]
                failures = 0
                for name, path in wanted.items():
                    if self._closing.is_set():
                        break
                    cover = index.get(name.casefold())
                    if not cover:
                        continue
                    try:
                        client.cover_art(cover, path, size=400)
                        images[name] = path.as_uri()
                        if changed:
                            changed({name: images[name]})
                    except (SubsonicError, OSError):
                        failures += 1
                        if failures >= ARTWORK_STRIKES:
                            break
            except (SubsonicError, OSError, ValueError, TypeError, AttributeError):
                # An optional portrait must not prevent browsing the library.
                continue
        return images

    # -- accounts -----------------------------------------------------------

    def add_server(
        self, address: str, username: str, password: str, *, allow_plaintext: bool
    ) -> concurrent.futures.Future[Source]:
        """Verify the account, then save it. Nothing is stored if verification
        fails. Adding an address Tide already knows signs in to it again."""
        return self._accounts.submit(self._add_server, address, username, password, allow_plaintext)

    def _add_server(
        self, address: str, username: str, password: str, allow_plaintext: bool
    ) -> Source:
        client = self._client_factory(
            address, username, password, allow_plaintext=allow_plaintext
        )
        info = client.ping()
        # Signing in again keeps the source Tide already has for this server,
        # whatever identity an earlier build gave it.
        existing = next((item for item in self.sources() if item.uri == client.address), None)
        source_id = existing.id if existing is not None else self.store.source_id_for(KIND, client.address)
        reference = reference_for(source_id)
        host = urllib.parse.urlsplit(client.address).hostname or client.address
        credentials = self.credentials()
        credentials.store(reference, f"Tide: {client.username} on {host}", password)
        capabilities = CAPABILITIES + (
            () if is_encrypted(client.address) else (PLAINTEXT_CAPABILITY,)
        )
        try:
            source = self.store.add_source(
                self._name_for(info, host, source_id), KIND, client.address,
                local=False, capabilities=capabilities, auth_ref=reference,
                account=client.username, source_id=source_id,
            )
            # A new sign-in may be a different account: sync it from scratch.
            self.store.set_sync_marker(source.id, None)
            self.store.set_source_state(source.id, SourceState.ONLINE)
        except BaseException:
            with contextlib.suppress(Exception):
                credentials.clear(reference)
            raise
        if existing is not None and existing.auth_ref and existing.auth_ref != reference:
            # An earlier build's keyring entry for this server is replaced.
            with contextlib.suppress(Exception):
                credentials.clear(existing.auth_ref)
        _log.info("Signed in to remote source %s", source.id)
        with self._lock:
            self._clients[source.id] = client
        return self.store.source(source.id)

    def test_server(
        self, address: str, username: str, password: str, *, allow_plaintext: bool
    ) -> concurrent.futures.Future[ServerInfo]:
        """Check that the server accepts the account, without saving anything."""
        return self._accounts.submit(
            lambda: self._client_factory(
                address, username, password, allow_plaintext=allow_plaintext
            ).ping()
        )

    def _name_for(self, info: ServerInfo, host: str, source_id: str) -> str:
        existing = next((item for item in self.sources() if item.id == source_id), None)
        if existing is not None:
            return existing.name
        taken = {source.name for source in self.store.sources()}
        name = info.display_name
        return name if name not in taken else f"{name} ({host})"

    def connect(self, source_id: str) -> concurrent.futures.Future[SourceState]:
        """Read the password and check the server; records the outcome as the
        source's state and raises the reason when it is not online."""
        return self._accounts.submit(self._connect_state, source_id)

    def _connect_state(self, source_id: str) -> SourceState:
        self._connect(source_id)
        return SourceState.ONLINE

    def _connect(self, source_id: str) -> SubsonicClient:
        source = self.store.source(source_id)
        with self._lock:
            client = self._clients.get(source_id)
        try:
            if client is None:
                secret = (
                    self.credentials().lookup(source.auth_ref) if source.auth_ref else None
                )
                if not secret:
                    _log.info("No saved sign-in for remote source %s; it needs signing in again", source_id)
                    raise AuthenticationFailed(f"Tide has no saved password for {source.name}.")
                account, password = _unpack_secret(secret, source.account)
                if not account:
                    _log.info("Remote source %s has no account name; it needs signing in again", source_id)
                    raise AuthenticationFailed(f"Tide doesn’t know which account to use on {source.name}.")
                client = self._client_factory(
                    source.uri, account, password, allow_plaintext=accepts_plaintext(source),
                )
                if account != source.account or password != secret:
                    self._normalize_saved_sign_in(source, account, password)
            client.ping()
        except (AuthenticationFailed, CredentialUnavailable) as error:
            _log.info("Remote source %s needs signing in: %s", source_id, type(error).__name__)
            self._forget_client(source_id, SourceState.AUTH_REQUIRED)
            raise
        except Unreachable as error:
            _log.info("Remote source %s is out of reach: %s", source_id, error)
            self._forget_client(source_id, SourceState.OFFLINE)
            raise
        except SubsonicError as error:
            # Includes a certificate this device doesn't trust.
            _log.warning("Remote source %s failed to connect: %s", source_id, error)
            self._forget_client(source_id, SourceState.ERROR)
            raise
        with self._lock:
            self._clients[source_id] = client
        self.store.set_source_state(source_id, SourceState.ONLINE)
        return client

    def _normalize_saved_sign_in(self, source: Source, account: str, password: str) -> None:
        """Rewrite an older saved sign-in as the current one: the account name
        in the library, the password alone in the keyring. Best effort; the
        older form keeps working if this can't be saved."""
        try:
            if account != source.account:
                self.store.set_source_account(source.id, account)
            if source.auth_ref:
                host = urllib.parse.urlsplit(source.uri).hostname or source.uri
                self.credentials().store(source.auth_ref, f"Tide: {account} on {host}", password)
            _log.info("Updated the saved sign-in format for remote source %s", source.id)
        except (CredentialUnavailable, KeyError) as error:
            _log.warning("Couldn't update the saved sign-in for remote source %s: %s", source.id, error)

    def _forget_client(self, source_id: str, state: SourceState) -> None:
        with self._lock:
            self._clients.pop(source_id, None)
        with contextlib.suppress(KeyError):
            self.store.set_source_state(source_id, state)

    def mark_offline(self, source_id: str) -> None:
        """The network went away; a later connect restores the source."""
        self._forget_client(source_id, SourceState.OFFLINE)

    def sign_out(self, source_id: str) -> concurrent.futures.Future:
        """Forget the login while keeping indexed songs, downloads and artwork.

        The undo credential is held only in memory, never in the backup.
        Account operations serialize reconnects with sign-out.
        """
        return self._accounts.submit(self._sign_out_account, source_id)

    def _sign_out_account(self, source_id: str) -> SignOutUndo:
        with self._lock:
            event = self._sync_cancel.get(source_id)
            if event is not None:
                event.set()
        return self._submit(self._sign_out, source_id).result()

    def _sign_out(self, source_id: str) -> SignOutUndo:
        source = self.store.source(source_id)
        if source.kind != KIND:
            raise ValueError('Only server sources have a login')
        # SQLite's backup API includes WAL data and takes a consistent snapshot.
        folder = self.store.path.parent / 'backups'
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / f'sign-out-{time.time_ns()}-{uuid.uuid4().hex}.db'
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with sqlite3.connect(path) as backup:
                self.store._connection.backup(backup)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        credentials = self.credentials()
        secret = credentials.lookup(source.auth_ref) if source.auth_ref else None
        with self._lock:
            client = self._clients.get(source_id)
        token = SignOutUndo(source_id, source.state, source.auth_ref, source.account, secret, client)
        if source.auth_ref:
            credentials.clear(source.auth_ref)
        try:
            self.store.set_source_state(source_id, SourceState.AUTH_REQUIRED)
        except BaseException:
            if source.auth_ref and secret is not None:
                credentials.store(source.auth_ref, f'Tide: {source.account}', secret)
            raise
        with self._lock:
            self._clients.pop(source_id, None)
            for owner, event in self._downloading.values():
                if owner == source_id:
                    event.set()
        return token

    def undo_sign_out(self, token: SignOutUndo) -> concurrent.futures.Future:
        return self._accounts.submit(self._undo_sign_out_account, token)

    def _undo_sign_out_account(self, token: SignOutUndo) -> None:
        self._submit(self._undo_sign_out, token).result()

    def _undo_sign_out(self, token: SignOutUndo) -> None:
        source = self.store.source(token.source_id)
        if source.state != SourceState.AUTH_REQUIRED or source.auth_ref != token.auth_ref:
            return  # A subsequent edit or reconnect supersedes this undo.
        if token.auth_ref and token.secret is not None:
            self.credentials().store(token.auth_ref, f'Tide: {token.account}', token.secret)
        self.store.set_source_state(source.id, token.state)
        with self._lock:
            if token.client is not None:
                self._clients[source.id] = token.client
        token.secret = None
        token.client = None

    def remove_source(self, source_id: str) -> concurrent.futures.Future[int]:
        """Forget the source, delete its downloads and cached artwork, and clear
        its password. Nothing on the server changes. Returns downloads deleted.

        A sync of the source in progress is cancelled rather than waited for."""
        with self._lock:
            if source_id in self._sync_cancel:
                self._sync_cancel[source_id].set()
        return self._submit(self._remove_source, source_id)

    def _remove_source(self, source_id: str) -> int:
        source = self.store.source(source_id)
        with self._lock:
            self._clients.pop(source_id, None)
            for owner, event in self._downloading.values():
                if owner == source_id:
                    event.set()
        paths = self.store.remove_source(source_id)
        removed = sum(self._delete_download(path) for path in paths)
        for root in (self.offline_root, self.artwork_root):
            target = root / source_id
            if target.is_dir() and target.resolve().is_relative_to(root.resolve()):
                shutil.rmtree(target, ignore_errors=True)
        if source.auth_ref:
            self.credentials().clear(source.auth_ref)
        return removed

    # -- sync -----------------------------------------------------------------

    def sync(self, source_id: str, progress: Progress | None = None) -> concurrent.futures.Future[SyncResult]:
        return self._submit(self._sync, source_id, progress)

    def _sync(self, source_id: str, progress: Progress | None) -> SyncResult:
        cancel = threading.Event()
        with self._lock:
            self._sync_cancel[source_id] = cancel
            if self._closing.is_set():
                cancel.set()
        try:
            return self._sync_songs(source_id, progress, cancel)
        finally:
            with self._lock:
                if self._sync_cancel.get(source_id) is cancel:
                    del self._sync_cancel[source_id]

    def _sync_songs(
        self, source_id: str, progress: Progress | None, cancel: threading.Event
    ) -> SyncResult:
        client = self._connect(source_id)
        source = self.store.source(source_id)
        seen: set[str] = set()
        # One cover per album: a server may give every song its own cover ID
        # (Navidrome does) even though an album's songs share one picture.
        album_covers: dict[str, str] = {}
        covers: dict[str, str] = {}
        try:
            marker, count = client.scan_marker()
            # Without its cover list (a cleared cache, or a library synced by an
            # earlier Tide) an unchanged library still needs one full read.
            covers_known = (self.artwork_root / source_id / COVERS_FILE).is_file()
            if marker is not None and marker == source.sync_marker and covers_known:
                failed = self._fetch_artwork(client, source_id, self._saved_covers(source_id), progress, cancel)
                return SyncResult(source_id, count, 0, True, failed)
            self.store.set_source_state(source_id, SourceState.SYNCING)
            for page in client.songs(cancel):
                items = []
                for song in page:
                    artwork = None
                    cover_id = str(song.get("coverArt") or "")
                    if cover_id:
                        album_key = str(song.get("albumId") or "")
                        if album_key:
                            cover_id = album_covers.setdefault(album_key, cover_id)
                        artwork = self._artwork_path(source_id, cover_id)
                        covers[artwork.name] = cover_id
                    items.append(song_metadata(client.address, song, artwork.as_uri() if artwork else None))
                for _track_id, copy_id in self.store.upsert_copies(source_id, items):
                    seen.add(copy_id)
                if progress is not None:
                    progress("songs", len(seen), max(count, len(seen)))
        except Cancelled:
            with contextlib.suppress(KeyError):
                self.store.set_source_state(source_id, SourceState.ONLINE)
            raise
        except Unreachable:
            self._forget_client(source_id, SourceState.OFFLINE)
            raise
        except AuthenticationFailed:
            self._forget_client(source_id, SourceState.AUTH_REQUIRED)
            raise
        except SubsonicError:
            self.store.set_source_state(source_id, SourceState.ERROR)
            raise
        missing = self.store.finish_source_scan(source_id, seen)
        # The songs are synced; artwork that doesn't arrive now is retried on a
        # later connect without re-reading the library.
        self.store.set_sync_marker(source_id, marker)
        self._save_covers(source_id, covers)
        artwork_failed = self._fetch_artwork(client, source_id, covers, progress, cancel)
        if cancel.is_set():
            raise Cancelled("Cancelled.")
        return SyncResult(source_id, len(seen), missing, False, artwork_failed)

    def _artwork_path(self, source_id: str, cover_id: str) -> Path:
        digest = hashlib.sha256(cover_id.encode("utf-8")).hexdigest()[:40]
        return self.artwork_root / source_id / f"{digest}.image"

    def _saved_covers(self, source_id: str) -> dict[str, str]:
        try:
            saved = json.loads((self.artwork_root / source_id / COVERS_FILE).read_text())
        except (OSError, ValueError):
            return {}
        return {str(name): str(cover) for name, cover in saved.items()} if isinstance(saved, dict) else {}

    def _save_covers(self, source_id: str, covers: dict[str, str]) -> None:
        folder = self.artwork_root / source_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = folder / f".{COVERS_FILE}.{os.getpid()}.part"
        partial.write_text(json.dumps(covers, sort_keys=True))
        os.replace(partial, folder / COVERS_FILE)

    def _fetch_artwork(
        self, client: SubsonicClient, source_id: str, covers: dict[str, str],
        progress: Progress | None, cancel: threading.Event,
    ) -> int:
        """Fetch covers not yet on this device. Returns how many are still missing.

        A throttled answer, or repeated timeouts, ends the attempt for this
        source for a while: a busy server gets no new queue from Tide.
        """
        folder = self.artwork_root / source_id
        wanted = [(cover_id, folder / name) for name, cover_id in covers.items()
                  if not (folder / name).is_file()]
        if not wanted or time.monotonic() < self._artwork_resume_at.get(source_id, 0):
            return len(wanted)
        if progress is not None:
            progress("artwork", 0, len(wanted))
        stop = threading.Event()
        lock = threading.Lock()
        state = {"done": 0, "fetched": 0, "strikes": 0}

        def fetch(cover_id: str, path: Path) -> None:
            if stop.is_set() or cancel.is_set():
                return
            try:
                client.cover_art(cover_id, path)
            except Throttled:
                stop.set()
            except Unreachable:
                with lock:
                    state["strikes"] += 1
                    if state["strikes"] >= ARTWORK_STRIKES:
                        stop.set()
            except (SubsonicError, OSError):
                pass  # this one cover is unusable; the rest may be fine
            else:
                with lock:
                    state["fetched"] += 1
                    state["strikes"] = 0
            with lock:
                state["done"] += 1
                done = state["done"]
            if progress is not None:
                progress("artwork", done, len(wanted))

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=ARTWORK_WORKERS, thread_name_prefix="tide-artwork"
        ) as pool:
            for cover_id, path in wanted:
                pool.submit(fetch, cover_id, path)
        if stop.is_set():
            self._artwork_resume_at[source_id] = time.monotonic() + ARTWORK_BACKOFF_SECONDS
        return len(wanted) - state["fetched"]

    # -- playback ---------------------------------------------------------------

    def resolve(self, copy: TrackCopy) -> str:
        """The URI the player opens: a download when one is on this device,
        otherwise an authenticated stream built for this playback only."""
        if copy.offline_ready:
            return Path(copy.offline_path).as_uri()
        with self._lock:
            client = self._clients.get(copy.source_id)
        if client is None:
            source = self.store.source(copy.source_id)
            if source.state is SourceState.AUTH_REQUIRED:
                raise PlaybackUnavailable(f"Sign in to {source.name} again to play this song.")
            if source.state is SourceState.OFFLINE:
                raise PlaybackUnavailable(f"{source.name} is out of reach.")
            raise PlaybackUnavailable(f"Tide is still connecting to {source.name}.")
        try:
            return client.stream_url(song_id_from_uri(copy.uri))
        except (SubsonicError, ValueError) as error:
            raise PlaybackUnavailable(str(error)) from None

    # -- offline ----------------------------------------------------------------

    def request_offline(self, track_ids: Iterable[str], changed: Changed | None = None) -> int:
        candidates = self.store.offline_candidates(track_ids)
        requested = self.store.request_offline(copy.id for copy in candidates)
        self.pump_downloads(changed)
        return requested

    def pump_downloads(self, changed: Changed | None = None) -> int:
        """Start queued downloads whose server is connected. Returns how many started."""
        started = 0
        if self._closing.is_set():
            return 0
        for item in self.store.offline_items(states=(OfflineState.QUEUED,)):
            with self._lock:
                if item.copy_id in self._downloading or item.source_id not in self._clients:
                    continue
                event = threading.Event()
                self._downloading[item.copy_id] = (item.source_id, event)
            self._downloads.submit(self._download, item, event, changed)
            started += 1
        return started

    def _destination(self, item: OfflineItem) -> Path:
        suffix = re.sub(r"[^a-z0-9]", "", item.format.casefold())[:8] or "audio"
        return self.offline_root / item.source_id / f"{item.copy_id}.{suffix}"

    def _download(self, item: OfflineItem, cancel: threading.Event, changed: Changed | None) -> None:
        destination = self._destination(item)
        try:
            if not self.store.start_offline(item.copy_id):
                return
            if changed is not None:
                changed()
            with self._lock:
                client = self._clients.get(item.source_id)
            if client is None:
                raise Unreachable("The server isn’t connected.")
            size = client.download(
                song_id_from_uri(item.uri), destination, expected_size=item.size, cancel=cancel
            )
            if not self.store.complete_offline(item.copy_id, destination, size):
                destination.unlink(missing_ok=True)  # removed while it downloaded
        except Cancelled:
            if self._closing.is_set():
                self._requeue(item.copy_id)
        except Unreachable:
            # A transient loss: wait for the server to come back.
            self._requeue(item.copy_id)
        except SubsonicError as error:
            self.store.fail_offline(item.copy_id, str(error))
        except OSError:
            self.store.fail_offline(item.copy_id, "Tide couldn’t save the download on this device.")
        finally:
            with self._lock:
                self._downloading.pop(item.copy_id, None)
            if changed is not None:
                changed()

    def _requeue(self, copy_id: str) -> None:
        self.store.requeue_offline(copy_id)

    def remove_offline(self, track_ids: Iterable[str]) -> int:
        """Delete downloads of these tracks from this device. Returns files deleted."""
        track_ids = list(dict.fromkeys(track_ids))
        with self._lock:
            for track_id in track_ids:
                for copy in self.store.copies_for_track(track_id):
                    if copy.id in self._downloading:
                        self._downloading[copy.id][1].set()
        return sum(self._delete_download(path) for path in self.store.remove_offline(track_ids))

    def _delete_download(self, path: str) -> int:
        target = Path(path)
        try:
            if not target.resolve().is_relative_to(self.offline_root.resolve()):
                return 0  # Tide only deletes files it downloaded itself
            target.unlink()
        except FileNotFoundError:
            return 0
        except OSError:
            return 0
        return 1
