# SPDX-License-Identifier: Apache-2.0
"""A tiny in-process Subsonic server for tests. No real network is used.

`FakeSubsonicServer` answers the handful of endpoints Tide's Subsonic client
speaks, on an ephemeral loopback port, so the client, sync, and error-handling
paths are exercised against real HTTP without a real Navidrome instance.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# A placeholder account that exists only for this in-process fixture.
USERNAME = "listener"
PASSWORD = "tide-test-password"


def _ok(**fields: object) -> dict:
    return {"subsonic-response": {"status": "ok", "version": "1.16.1", **fields}}


def _failed(code: int, message: str) -> dict:
    return {
        "subsonic-response": {
            "status": "failed", "version": "1.16.1",
            "error": {"code": code, "message": message},
        }
    }


class FakeSubsonicServer:
    """Use as a context manager: `with FakeSubsonicServer(songs) as server:`.

    Set `require_album_fallback = True` to make `search3` answer empty, so a
    test exercises the `getAlbumList2`/`getAlbum` walk instead. Set
    `throttle_next = True` to answer the next request with HTTP 503, or stop
    the server (`server.__exit__`) mid-test to simulate it going offline.
    """

    def __init__(self, songs: list[dict] | None = None) -> None:
        self.songs = list(songs or [])
        self.username = USERNAME
        self.password = PASSWORD
        self.require_album_fallback = False
        self.throttle_next = False
        self.scanning = False
        self.scan_count = len(self.songs)
        self.last_scan = "2026-01-01T00:00:00Z"
        self.cover_art: dict[str, tuple[bytes, str]] = {}
        # song id -> (bytes, content type). Set this to make `stream` serve
        # real audio, so a player can actually play from this fixture.
        self.audio: dict[str, tuple[bytes, str]] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:
                server._handle(self)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def address(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeSubsonicServer":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def request_count(self, endpoint: str) -> int:
        with self._lock:
            return sum(1 for name, _query in self.requests if name == endpoint)

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(handler.path)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        endpoint = parts.path.rsplit("/", 1)[-1]
        with self._lock:
            self.requests.append((endpoint, dict(query)))
        if endpoint == "stream" and self.audio and self._authenticated(query):
            self._serve_audio(handler, query)
            return
        body, status, content_type = self._respond(endpoint, query)
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _serve_audio(self, handler: BaseHTTPRequestHandler, query: dict[str, str]) -> None:
        """Serve one song's bytes, honoring a single `Range` request the way
        a real server does -- a player's demuxer routinely asks for a byte
        range rather than reading the whole stream front to back."""
        entry = self.audio.get(query.get("id", ""))
        if entry is None:
            handler.send_response(404)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        data, content_type = entry
        start, end = 0, len(data) - 1
        status = 200
        requested = handler.headers.get("Range", "")
        if requested.startswith("bytes="):
            first, _, last = requested[len("bytes="):].partition("-")
            if first.strip().isdigit():
                start = min(int(first), len(data))
                if last.strip().isdigit():
                    end = min(int(last), len(data) - 1)
                status = 206
        body = data[start:end + 1]
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Accept-Ranges", "bytes")
        if status == 206:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _authenticated(self, query: dict[str, str]) -> bool:
        salt, token = query.get("s"), query.get("t")
        if not salt or not token or query.get("u") != self.username:
            return False
        expected = hashlib.md5(
            (self.password + salt).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return token == expected

    def _respond(self, endpoint: str, query: dict[str, str]) -> tuple[bytes, int, str]:
        if self.throttle_next:
            self.throttle_next = False
            return b"Slow down", 503, "text/plain"
        if not self._authenticated(query):
            return json.dumps(_failed(40, "Wrong username or password.")).encode(), 200, "application/json"
        if endpoint == "ping":
            payload = _ok(type="navidrome", serverVersion="0.53.0", openSubsonic=True)
        elif endpoint == "getScanStatus":
            payload = _ok(scanStatus={
                "scanning": self.scanning, "count": self.scan_count, "lastScan": self.last_scan,
            })
        elif endpoint == "search3":
            if self.require_album_fallback:
                payload = _ok(searchResult3={})
            else:
                offset = int(query.get("songOffset", "0"))
                count = int(query.get("songCount", "500"))
                payload = _ok(searchResult3={"song": self.songs[offset: offset + count]})
        elif endpoint == "getAlbumList2":
            offset = int(query.get("offset", "0"))
            size = int(query.get("size", "500"))
            payload = _ok(albumList2={"album": self._albums()[offset: offset + size]})
        elif endpoint == "getAlbum":
            album_id = query.get("id")
            songs = [song for song in self.songs if str(song.get("albumId")) == album_id]
            payload = _ok(album={"song": songs})
        elif endpoint == "getCoverArt":
            cover_id = query.get("id", "")
            if cover_id in self.cover_art:
                data, content_type = self.cover_art[cover_id]
                return data, 200, content_type
            payload = _failed(70, "Artwork not found.")
        else:
            payload = _failed(0, f"Unknown endpoint {endpoint!r}.")
        return json.dumps(payload).encode(), 200, "application/json"

    def _albums(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for song in self.songs:
            album_id = str(song.get("albumId") or "")
            if album_id and album_id not in seen:
                seen[album_id] = {"id": album_id, "name": song.get("album", "")}
        return list(seen.values())


def song(
    song_id: str, *, title: str, album_id: str, album: str = "Album",
    artist: str = "Artist", cover_art: str | None = None, duration: int = 180,
    artists: list[str] | None = None, album_artists: list[str] | None = None,
) -> dict:
    """A minimal, valid OpenSubsonic `Child` for a playable song.

    `artists`/`album_artists`, when given, are OpenSubsonic's own structural
    per-song artist credit list (`ArtistID3[]`) — the real, ordered multi-
    artist source, as opposed to the single flat `artist` string every
    Subsonic server (OpenSubsonic or not) always sends.
    """
    payload = {
        "id": song_id,
        "title": title,
        "album": album,
        "albumId": album_id,
        "artist": artist,
        "duration": duration,
        "track": 1,
        "suffix": "flac",
        "bitRate": 900,
        "coverArt": cover_art,
    }
    if artists is not None:
        payload["artists"] = [{"id": f"ar-{name}", "name": name} for name in artists]
    if album_artists is not None:
        payload["albumArtists"] = [{"id": f"aa-{name}", "name": name} for name in album_artists]
    return payload
