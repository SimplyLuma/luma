# SPDX-License-Identifier: Apache-2.0
"""Subsonic/OpenSubsonic protocol client for Tide's Navidrome sources.

The client implements the published Subsonic REST API and its OpenSubsonic
extensions using only the Python standard library. It holds a password only in
memory, for as long as its owner keeps the client.

Every authenticated URL is built for one request with a fresh salt. None is
persisted, logged, or published. Redirects are refused so a token never follows
a server to another scheme or host, and messages raised from here never carry a
request URL.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

API_VERSION = "1.16.1"
CLIENT_NAME = "Tide"
USER_AGENT = "Tide/0.1 (Project Luma)"
TIMEOUT_SECONDS = 20
# Navidrome queues artwork work and answers 429 after a one-minute backlog; a
# shorter client timeout would abandon requests the server is still doing.
ARTWORK_TIMEOUT_SECONDS = 75
PAGE_SIZE = 500
# Bounds a server that ignores paging so a sync always ends.
MAX_PAGES = 4000
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_ARTWORK_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
FREE_SPACE_RESERVE = 256 * 1024 * 1024

_SECRET_PARAMETER = re.compile(r"([?&](?:u|t|s|p|apiKey)=)[^&#\s]*", re.IGNORECASE)


class SubsonicError(Exception):
    """A failure whose message is safe to show the user."""


class InvalidAddress(SubsonicError):
    pass


class InsecureTransport(SubsonicError):
    pass


class Unreachable(SubsonicError):
    pass


class CertificateError(SubsonicError):
    pass


class AuthenticationFailed(SubsonicError):
    pass


class NotAuthorized(SubsonicError):
    pass


class NotFound(SubsonicError):
    pass


class ProtocolError(SubsonicError):
    pass


class StorageFull(SubsonicError):
    pass


class Throttled(SubsonicError):
    """The server asked Tide to slow down (HTTP 429 or 503)."""


class Cancelled(SubsonicError):
    pass


def redact(text: str) -> str:
    """Remove Subsonic credential parameters from text that may hold a URL."""
    return _SECRET_PARAMETER.sub(r"\1[redacted]", text)


def normalize_address(text: str) -> str:
    """Reduce what a person types or pastes to the server's base URL.

    A bare host means HTTPS. A pasted web-app link keeps only its origin and
    base path: Navidrome's player lives below `/app` and its API below `/rest`.
    """
    raw = text.strip()
    if not raw:
        raise InvalidAddress("Enter the server’s address.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urllib.parse.urlsplit(raw)
        port = parts.port
    except ValueError:
        raise InvalidAddress("That address isn’t valid.") from None
    if parts.scheme.casefold() not in ("http", "https"):
        raise InvalidAddress("Use an address that starts with https:// or http://.")
    if parts.username or parts.password:
        raise InvalidAddress("Enter the username and password in their own fields.")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise InvalidAddress("That address has no server name.")
    path = parts.path.rstrip("/")
    for suffix in ("/app", "/rest"):
        if path.casefold().endswith(suffix):
            path = path[: -len(suffix)]
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunsplit((parts.scheme.casefold(), netloc, path, "", ""))


def is_encrypted(address: str) -> bool:
    return urllib.parse.urlsplit(address).scheme == "https"


def has_scheme(text: str) -> bool:
    """Whether the person typed http:// or https:// rather than a bare host."""
    return "://" in text.strip()


@dataclass(frozen=True, slots=True)
class ServerInfo:
    server_type: str
    server_version: str
    api_version: str
    open_subsonic: bool
    extensions: frozenset[str]

    @property
    def display_name(self) -> str:
        known = {"navidrome": "Navidrome", "gonic": "gonic", "subsonic": "Subsonic"}
        return known.get(self.server_type.casefold(), "Music server")


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def _error_for_code(code: int) -> SubsonicError:
    if code == 40:
        return AuthenticationFailed("The username or password is incorrect.")
    if code == 41:
        return AuthenticationFailed(
            "This account signs in through LDAP, which can’t use Subsonic token sign-in."
        )
    if code in (42, 43, 44):
        return AuthenticationFailed("The server doesn’t accept Tide’s sign-in method.")
    if code == 50:
        return NotAuthorized("This account isn’t allowed to do that on the server.")
    if code == 70:
        return NotFound("The server couldn’t find that item.")
    if code == 20:
        return ProtocolError("The server needs a newer version of Tide.")
    if code == 30:
        return ProtocolError("The server is too old for Tide.")
    return ProtocolError(f"The server reported an error (code {code}).")


def _raise_for_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("subsonic-response"), dict):
        raise ProtocolError("That address doesn’t answer like a Navidrome or Subsonic server.")
    response = payload["subsonic-response"]
    if response.get("status") != "ok":
        error = response.get("error") if isinstance(response.get("error"), dict) else {}
        try:
            code = int(error.get("code", 0))
        except (TypeError, ValueError):
            code = 0
        raise _error_for_code(code)
    return response


class SubsonicClient:
    """One account on one server. Methods block; call them off the UI thread."""

    def __init__(
        self,
        address: str,
        username: str,
        password: str,
        *,
        allow_plaintext: bool = False,
        timeout: float = TIMEOUT_SECONDS,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.address = normalize_address(address)
        self.username = username.strip()
        if not self.username:
            raise AuthenticationFailed("Enter the account’s username.")
        if not password:
            raise AuthenticationFailed("Enter the account’s password.")
        self._password = password
        self.allow_plaintext = allow_plaintext
        self.timeout = timeout
        context = ssl_context or ssl.create_default_context()
        self._opener = urllib.request.build_opener(
            _RefuseRedirects(), urllib.request.HTTPSHandler(context=context)
        )

    # -- transport and authentication -------------------------------------

    def check_transport(self) -> None:
        """Refuse plain HTTP unless the person accepted an unencrypted connection."""
        if not is_encrypted(self.address) and not self.allow_plaintext:
            raise InsecureTransport("This server connection isn’t encrypted.")

    def _auth(self) -> dict[str, str]:
        salt = secrets.token_hex(12)
        # The protocol mandates MD5; it is not a security primitive Tide chose.
        token = hashlib.md5(
            (self._password + salt).encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return {"u": self.username, "t": token, "s": salt, "v": API_VERSION, "c": CLIENT_NAME}

    def url(self, endpoint: str, **params: object) -> str:
        """An authenticated URL for one request. Never store or log it."""
        query = urllib.parse.urlencode(
            {**self._auth(), "f": "json", **{k: v for k, v in params.items() if v is not None}}
        )
        return f"{self.address}/rest/{endpoint}?{query}"

    def stream_url(self, song_id: str) -> str:
        """The original file, untranscoded, so the player can seek within it."""
        self.check_transport()
        return self.url("stream", id=song_id, format="raw")

    # -- requests ---------------------------------------------------------

    def _open(
        self, endpoint: str, params: dict[str, object], cancel: threading.Event | None,
        timeout: float | None = None,
    ):
        if cancel is not None and cancel.is_set():
            raise Cancelled("Cancelled.")
        self.check_transport()
        request = urllib.request.Request(
            self.url(endpoint, **params),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"},
        )
        try:
            return self._opener.open(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as error:
            status = error.code
            location = error.headers.get("Location", "") if error.headers else ""
            error.close()
            if 300 <= status < 400:
                target = urllib.parse.urlsplit(urllib.parse.urljoin(self.address + "/", location))
                where = f"{target.scheme}://{target.netloc}" if target.netloc else "another address"
                raise ProtocolError(
                    f"The server redirected Tide to {where}. Add that address instead."
                ) from None
            if status in (429, 503):
                raise Throttled("The server is busy. Tide will try again later.") from None
            if status in (401, 403):
                raise AuthenticationFailed("The server refused the sign-in.") from None
            if status == 404:
                raise ProtocolError(
                    "That address doesn’t answer like a Navidrome or Subsonic server."
                ) from None
            raise ProtocolError(f"The server answered with HTTP status {status}.") from None
        except urllib.error.URLError as error:
            raise self._connection_error(error.reason) from None
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            raise self._connection_error(error) from None

    @staticmethod
    def _connection_error(reason: object) -> SubsonicError:
        if isinstance(reason, ssl.SSLCertVerificationError):
            return CertificateError(
                "The server’s certificate isn’t trusted by this device, so Tide didn’t connect."
            )
        if isinstance(reason, ssl.SSLError):
            return CertificateError("A secure connection to the server couldn’t be established.")
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return Unreachable("The server didn’t answer in time.")
        if isinstance(reason, socket.gaierror):
            return Unreachable("Tide couldn’t find a server at that address.")
        if isinstance(reason, ConnectionRefusedError):
            return Unreachable("The server refused the connection. Check the address and port.")
        return Unreachable("Tide couldn’t reach the server.")

    def _read(self, response, limit: int) -> bytes:
        try:
            data = response.read(limit + 1)
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            raise self._connection_error(error) from None
        if len(data) > limit:
            raise ProtocolError("The server sent more data than Tide expected.")
        return data

    def request(
        self, endpoint: str, cancel: threading.Event | None = None, **params: object
    ) -> dict:
        with self._open(endpoint, params, cancel) as response:
            body = self._read(response, MAX_RESPONSE_BYTES)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProtocolError(
                "That address doesn’t answer like a Navidrome or Subsonic server."
            ) from None
        return _raise_for_payload(payload)

    def ping(self) -> ServerInfo:
        response = self.request("ping")
        open_subsonic = bool(response.get("openSubsonic"))
        extensions: frozenset[str] = frozenset()
        if open_subsonic:
            try:
                listed = self.request("getOpenSubsonicExtensions").get(
                    "openSubsonicExtensions", []
                )
                extensions = frozenset(
                    str(item.get("name")) for item in listed if isinstance(item, dict)
                )
            except SubsonicError:
                pass
        return ServerInfo(
            server_type=str(response.get("type") or "subsonic"),
            server_version=str(response.get("serverVersion") or ""),
            api_version=str(response.get("version") or ""),
            open_subsonic=open_subsonic,
            extensions=extensions,
        )

    def scan_marker(self) -> tuple[str | None, int]:
        """A marker that changes whenever the server's library does, and its size.

        The marker is None when the server can't say, or is scanning now, in
        which case the caller must sync in full.
        """
        try:
            status = self.request("getScanStatus").get("scanStatus", {})
        except (NotAuthorized, NotFound, ProtocolError):
            return None, 0
        try:
            count = max(0, int(status.get("count") or 0))
        except (TypeError, ValueError):
            count = 0
        if status.get("scanning") or not status.get("lastScan"):
            return None, count
        return f"{count}:{status['lastScan']}", count

    # -- library ------------------------------------------------------------

    @staticmethod
    def _playable(song: object) -> bool:
        return (
            isinstance(song, dict)
            and bool(song.get("id"))
            and not song.get("isDir")
            and not song.get("isVideo")
            and song.get("type") not in ("video", "podcast")
        )

    def songs(self, cancel: threading.Event | None = None) -> Iterator[list[dict]]:
        """Every song, a page at a time.

        OpenSubsonic requires an empty `search3` query to return the whole
        library for offline sync. A server that returns nothing to it is walked
        album by album instead.
        """
        seen: set[str] = set()
        offset = 0
        for _page in range(MAX_PAGES):
            result = self.request(
                "search3", cancel, query="", artistCount=0, albumCount=0,
                songCount=PAGE_SIZE, songOffset=offset,
            ).get("searchResult3") or {}
            page = result.get("song") or []
            if not isinstance(page, list):
                raise ProtocolError("The server sent a song list Tide couldn’t read.")
            ids = {str(song.get("id")) for song in page if isinstance(song, dict)}
            if page and ids <= seen:
                break  # the server ignored songOffset and repeated a page
            fresh = [song for song in page if self._playable(song) and str(song["id"]) not in seen]
            seen.update(ids)
            if fresh:
                yield fresh
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        if not seen:
            yield from self._songs_by_album(cancel)

    def _songs_by_album(self, cancel: threading.Event | None) -> Iterator[list[dict]]:
        offset = 0
        seen: set[str] = set()
        for _page in range(MAX_PAGES):
            albums = (
                self.request(
                    "getAlbumList2", cancel, type="alphabeticalByName", size=PAGE_SIZE, offset=offset
                ).get("albumList2") or {}
            ).get("album") or []
            for album in albums:
                if not isinstance(album, dict) or not album.get("id") or album["id"] in seen:
                    continue
                seen.add(album["id"])
                songs = (self.request("getAlbum", cancel, id=album["id"]).get("album") or {}).get(
                    "song"
                ) or []
                playable = [song for song in songs if self._playable(song)]
                if playable:
                    yield playable
            if len(albums) < PAGE_SIZE:
                return
            offset += len(albums)

    # -- files ----------------------------------------------------------------

    def cover_art(self, cover_id: str, destination: Path, *, size: int = 600) -> int:
        return self._fetch_file(
            "getCoverArt", {"id": cover_id, "size": size}, destination,
            limit=MAX_ARTWORK_BYTES, image=True, timeout=ARTWORK_TIMEOUT_SECONDS,
        )

    def download(
        self,
        song_id: str,
        destination: Path,
        *,
        expected_size: int = 0,
        cancel: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Save the original file, falling back to an untranscoded stream when
        the account may stream but not download."""
        try:
            return self._fetch_file(
                "download", {"id": song_id}, destination,
                expected_size=expected_size, cancel=cancel, progress=progress,
            )
        except NotAuthorized:
            return self._fetch_file(
                "stream", {"id": song_id, "format": "raw"}, destination,
                expected_size=expected_size, cancel=cancel, progress=progress,
            )

    def _fetch_file(
        self,
        endpoint: str,
        params: dict[str, object],
        destination: Path,
        *,
        expected_size: int = 0,
        limit: int | None = None,
        image: bool = False,
        cancel: threading.Event | None = None,
        progress: Callable[[int, int], None] | None = None,
        timeout: float | None = None,
    ) -> int:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._open(endpoint, params, cancel, timeout) as response:
            content_type = (response.headers.get("Content-Type") or "").casefold()
            if content_type.startswith(("application/json", "text/xml", "application/xml")):
                body = self._read(response, 1024 * 1024)
                try:
                    _raise_for_payload(json.loads(body))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    match = re.search(rb'code="(\d+)"', body)
                    raise _error_for_code(int(match.group(1)) if match else 0) from None
                raise ProtocolError("The server sent a message instead of the file.")
            if image and not content_type.startswith("image/"):
                raise ProtocolError("The server sent artwork Tide couldn’t read.")
            try:
                declared = int(response.headers.get("Content-Length") or 0)
            except ValueError:
                declared = 0
            total = declared or expected_size
            if total:
                free = shutil.disk_usage(destination.parent).free
                if free < total + FREE_SPACE_RESERVE:
                    raise StorageFull("There isn’t enough free space on this device.")
            partial = destination.with_name(f".{destination.name}.{os.getpid()}.part")
            written = 0
            try:
                with partial.open("wb") as stream:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled("Cancelled.")
                        try:
                            chunk = response.read(CHUNK_BYTES)
                        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
                            raise self._connection_error(error) from None
                        if not chunk:
                            break
                        written += len(chunk)
                        if limit is not None and written > limit:
                            raise ProtocolError("The server sent a larger file than Tide expected.")
                        stream.write(chunk)
                        if progress is not None:
                            progress(written, total)
                    stream.flush()
                    os.fsync(stream.fileno())
                if declared and written != declared:
                    raise Unreachable("The download ended before the whole file arrived.")
                if written == 0:
                    raise ProtocolError("The server sent an empty file.")
                os.replace(partial, destination)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        return written
