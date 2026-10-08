# SPDX-License-Identifier: Apache-2.0
"""A small in-process Subsonic server for protocol and adapter tests.

It speaks only the endpoints Tide uses, checks token authentication exactly as
a server must, and records every request so tests can prove what left Tide.
"""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99"
    b"=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def song(index: int, **overrides: object) -> dict:
    value = {
        "id": f"song-{index}",
        "isDir": False,
        "title": f"Song {index}",
        "album": f"Album {index // 10}",
        "artist": "Fixture Band",
        "displayAlbumArtist": "Fixture Band",
        "track": index % 10 + 1,
        "discNumber": 1,
        "year": 2024,
        "genre": "Ambient",
        # Like Navidrome: every song has its own cover ID, and an album ID.
        "coverArt": f"mf-{index}",
        "albumId": f"album-{index // 10}",
        "size": 2048,
        "contentType": "audio/flac",
        "suffix": "flac",
        "duration": 180,
        "bitRate": 900,
        "samplingRate": 48000,
        "channelCount": 2,
        "type": "music",
        "replayGain": {"trackGain": -6.5},
    }
    value.update(overrides)
    return value


class FakeSubsonic:
    def __init__(self, *, username: str = "listener", password: str = "correct horse", songs: int = 3) -> None:
        self.username = username
        self.password = password
        self.songs = [song(index) for index in range(songs)]
        self.requests: list[str] = []
        self.scan = {"scanning": False, "count": songs, "lastScan": "2026-09-01T10:00:00Z"}
        self.ignore_offset = False
        self.empty_search = False
        self.forbid_download = False
        self.redirect_to: str | None = None
        self.truncate_download = False
        self.hold_download: threading.Event | None = None
        self.hold_search: threading.Event | None = None
        self.search_started = threading.Event()
        self.not_subsonic = False
        self.artwork_status = 200
        self.artwork_requests = 0
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                fixture.handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def close(self) -> None:
        for hold in (self.hold_download, self.hold_search):
            if hold is not None:
                hold.set()
        self.server.shutdown()
        self.server.server_close()

    def body(self, handler: BaseHTTPRequestHandler, status: int, content_type: str, data: bytes, length: int | None = None) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data) if length is None else length))
        handler.end_headers()
        handler.wfile.write(data)

    def respond(self, handler: BaseHTTPRequestHandler, payload: dict | None = None, error: tuple[int, str] | None = None) -> None:
        response: dict = {"status": "ok", "version": "1.16.1", "type": "navidrome",
                          "serverVersion": "0.58.0", "openSubsonic": True}
        if error is not None:
            response.update(status="failed", error={"code": error[0], "message": error[1]})
        elif payload:
            response.update(payload)
        self.body(handler, 200, "application/json", json.dumps({"subsonic-response": response}).encode())

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        self.requests.append(handler.path)
        parts = urllib.parse.urlsplit(handler.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parts.query).items()}
        endpoint = parts.path.rsplit("/", 1)[-1].removesuffix(".view")
        if self.not_subsonic:
            self.body(handler, 200, "text/html", b"<html>router</html>")
            return
        if self.redirect_to is not None:
            handler.send_response(302)
            handler.send_header("Location", f"{self.redirect_to}{handler.path}")
            handler.end_headers()
            return
        if not parts.path.startswith("/rest/"):
            self.body(handler, 404, "text/plain", b"not found")
            return
        expected = hashlib.md5((self.password + query.get("s", "")).encode()).hexdigest()
        if query.get("u") != self.username or query.get("t") != expected or "p" in query:
            self.respond(handler, error=(40, "Wrong username or password"))
            return
        if endpoint == "ping":
            self.respond(handler)
        elif endpoint == "getOpenSubsonicExtensions":
            self.respond(handler, {"openSubsonicExtensions": [{"name": "transcodeOffset", "versions": [1]}]})
        elif endpoint == "getScanStatus":
            self.respond(handler, {"scanStatus": dict(self.scan)})
        elif endpoint == "search3":
            self.search_started.set()
            if self.hold_search is not None:
                self.hold_search.wait(10)
            size = int(query.get("songCount", 20))
            offset = 0 if self.ignore_offset else int(query.get("songOffset", 0))
            page = [] if self.empty_search else self.songs[offset:offset + size]
            self.respond(handler, {"searchResult3": {"song": page}})
        elif endpoint == "getAlbumList2":
            names = sorted({item["album"] for item in self.songs})
            offset = int(query.get("offset", 0))
            albums = [{"id": name, "name": name} for name in names][offset:offset + int(query.get("size", 10))]
            self.respond(handler, {"albumList2": {"album": albums}})
        elif endpoint == "getAlbum":
            self.respond(handler, {"album": {"id": query["id"], "song": [item for item in self.songs if item["album"] == query["id"]]}})
        elif endpoint == "getCoverArt":
            self.artwork_requests += 1
            if self.artwork_status != 200:
                self.body(handler, self.artwork_status, "text/plain", b"Too Many Requests")
            else:
                self.body(handler, 200, "image/png", PNG)
        elif endpoint in ("download", "stream"):
            if endpoint == "download" and self.forbid_download:
                self.respond(handler, error=(50, "User not authorized"))
                return
            if self.hold_download is not None:
                self.hold_download.wait(10)
            item = next((entry for entry in self.songs if entry["id"] == query.get("id")), None)
            if item is None:
                self.respond(handler, error=(70, "Song not found"))
                return
            data = f"{endpoint}:{item['id']}".encode().ljust(item["size"], b"\0")
            if self.truncate_download:
                self.body(handler, 200, "audio/flac", data[: len(data) // 2], length=len(data))
            else:
                self.body(handler, 200, "audio/flac", data)
        else:
            self.respond(handler, error=(0, f"unknown endpoint {endpoint}"))
