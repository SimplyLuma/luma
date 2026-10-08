# SPDX-License-Identifier: Apache-2.0
"""Kodi, through its local JSON-RPC connection.

Kodi 21 does not publish MPRIS, and its Flatpak may not own an MPRIS name.
It does keep a JSON-RPC connection on 127.0.0.1:9090 by default ("Allow
remote control from applications on this system"), which pushes playback
notifications and accepts playback commands. Only this computer can reach
it. Artwork comes from Kodi's own thumbnail cache on disk, so showing it
never fetches anything from the network.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import logging
import re
import socket
import threading
import time
import urllib.parse
from pathlib import Path

from gi.repository import GLib

from .mpris import NO_TRACK, State, Track

log = logging.getLogger(__name__)

HOST, PORT = "127.0.0.1", 9090
RECONNECT_SECONDS = 5
POSITION_REFRESH_SECONDS = 10
MARKUP = re.compile(r"\[/?(?:B|I|LIGHT|UPPERCASE|LOWERCASE|CAPITALIZE|CR|COLOR[^\]]*|TABS[^\]]*)\]", re.I)
ITEM_PROPERTIES = ["title", "artist", "album", "showtitle", "season", "episode", "thumbnail", "art", "duration", "file"]
PLAYER_PROPERTIES = ["speed", "time", "totaltime", "canseek", "position", "playlistid"]
THUMBNAIL_ROOTS = (Path.home() / ".var/app/tv.kodi.Kodi/data/userdata/Thumbnails",
                   Path.home() / ".kodi/userdata/Thumbnails")


def clean(text: str) -> str:
    """Kodi labels carry skin markup such as [COLOR red] and [B]."""
    text = MARKUP.sub("", text or "")
    return re.sub(r"\s+", " ", text).strip(" |-·\t")


def split_label(label: str) -> tuple[str, str]:
    """A multi-line add-on label: its first line is the title, the rest a subtitle."""
    lines = [clean(line) for line in MARKUP.sub("", label or "").splitlines()]
    lines = [line for line in lines if line]
    return (lines[0], " · ".join(lines[1:])) if lines else ("", "")


def to_microseconds(value: dict | None) -> int:
    if not isinstance(value, dict):
        return 0
    return ((int(value.get("hours", 0)) * 3600 + int(value.get("minutes", 0)) * 60 + int(value.get("seconds", 0))) * 1000
            + int(value.get("milliseconds", 0))) * 1000


def from_microseconds(value: int) -> dict:
    milliseconds = max(0, value) // 1000
    return {"hours": milliseconds // 3_600_000, "minutes": milliseconds // 60_000 % 60,
            "seconds": milliseconds // 1000 % 60, "milliseconds": milliseconds % 1000}


def image_url(value: str) -> str:
    """Kodi's image://<quoted url>/ wrapper around the real image address."""
    if value.startswith("image://"):
        return urllib.parse.unquote(value[len("image://"):].rstrip("/"))
    return value


def cached_art(cached: str) -> str:
    for root in THUMBNAIL_ROOTS:
        path = root / cached
        if cached and path.is_file():
            return path.as_uri()
    return ""


def track_from(item: dict, properties: dict) -> Track:
    title, subtitle = split_label(item.get("title") or item.get("label") or "")
    artists = [clean(a) for a in item.get("artist") or [] if clean(a)]
    album = clean(item.get("album") or "")
    show = clean(item.get("showtitle") or "")
    if show:
        season, episode = item.get("season", -1), item.get("episode", -1)
        if isinstance(season, int) and isinstance(episode, int) and season >= 0 and episode >= 0:
            album = f"Season {season}, episode {episode}"
        artists = artists or [show]
    if not artists and subtitle:
        artists = [subtitle]
    identity = hashlib.sha1(f"{item.get('file', '')}|{item.get('label', '')}|{item.get('title', '')}".encode()).hexdigest()
    return Track(track_id=f"/org/projectluma/MediaBridge/kodi/{identity[:16]}", title=title or "Kodi",
                 artists=artists, album=album, length_us=to_microseconds(properties.get("totaltime")))


class Kodi:
    """Keeps a connection to Kodi and turns it into Player updates."""

    bus_suffix, identity, desktop_entry = "kodi", "Kodi", "tv.kodi.Kodi"

    def __init__(self, on_present, on_state) -> None:
        self.on_present, self.on_state = on_present, on_state  # called on the main loop
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, callable] = {}
        self._stop = threading.Event()
        self._player_id: int | None = None
        self._state = State()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kodi", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    # ── Connection ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                connection = socket.create_connection((HOST, PORT), timeout=2)
            except OSError:
                self._stop.wait(RECONNECT_SECONDS)
                continue
            connection.settimeout(POSITION_REFRESH_SECONDS)
            with self._lock:
                self._socket = connection
            log.info("connected to Kodi")
            GLib.idle_add(lambda: (self.on_present(True), False)[1])
            self._request("Player.GetActivePlayers", None, self._players)
            try:
                self._read(connection)
            finally:
                with self._lock:
                    self._socket = None
                    self._pending.clear()
                connection.close()
                self._player_id = None
                self._state = State()
                GLib.idle_add(lambda: (self.on_present(False), False)[1])
                log.info("Kodi went away")
            self._stop.wait(RECONNECT_SECONDS)

    def _read(self, connection: socket.socket) -> None:
        decoder = json.JSONDecoder()
        text = codecs.getincrementaldecoder("utf-8")("replace")  # a character may span two reads
        buffer = ""
        while not self._stop.is_set():
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                if self._state.status == "Playing":
                    self._refresh(item=False)  # keep the position honest
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += text.decode(chunk)
            while buffer:
                buffer = buffer.lstrip()
                try:
                    message, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    break  # the rest of this message has not arrived yet
                buffer = buffer[end:]
                if isinstance(message, dict):
                    self._dispatch(message)
            if len(buffer) > 4 * 1024 * 1024:
                return  # a stream this broken is reconnected, not parsed

    def _request(self, method: str, params: dict | None, callback=None) -> None:
        with self._lock:
            if self._socket is None:
                return
            self._next_id += 1
            if callback is not None:
                self._pending[self._next_id] = callback
            body = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
            if params is not None:
                body["params"] = params
            try:
                self._socket.sendall(json.dumps(body).encode())
            except OSError:
                self._pending.pop(self._next_id, None)

    def _dispatch(self, message: dict) -> None:
        if "id" in message:
            with self._lock:
                callback = self._pending.pop(message["id"], None)
            if callback is not None and "result" in message:
                callback(message["result"])
            return
        method = message.get("method", "")
        data = (message.get("params") or {}).get("data") or {}
        if method in ("Player.OnPlay", "Player.OnResume", "Player.OnAVStart", "Player.OnAVChange",
                      "Player.OnSpeedChanged", "Player.OnPropertyChanged"):
            player = (data.get("player") or {}).get("playerid")
            if isinstance(player, int):
                self._player_id = player
            self._refresh(item=method != "Player.OnSpeedChanged")
        elif method == "Player.OnPause":
            self._refresh(item=False)
        elif method == "Player.OnSeek":
            self._refresh(item=False, seeked=True)
        elif method == "Player.OnStop":
            self._player_id = None
            self._publish(State())
        elif method == "System.OnQuit":
            self.stop_connection()

    def stop_connection(self) -> None:
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    # ── State ──────────────────────────────────────────────────────────────

    def _players(self, result) -> None:
        players = [p for p in result or [] if isinstance(p, dict) and p.get("type") in ("video", "audio")]
        if players:
            self._player_id = players[0]["playerid"]
            self._refresh()

    def _refresh(self, *, item: bool = True, seeked: bool = False) -> None:
        player = self._player_id
        if player is None:
            self._request("Player.GetActivePlayers", None, self._players)
            return

        def properties(result) -> None:
            state = State(status="Playing" if result.get("speed", 0) else "Paused",
                          track=self._state.track, position_us=to_microseconds(result.get("time")),
                          rate=1.0, can_seek=bool(result.get("canseek")),
                          can_go_next=result.get("playlistid", -1) >= 0,
                          can_go_previous=result.get("playlistid", -1) >= 0)
            if item:
                self._request("Player.GetItem", {"playerid": player, "properties": ITEM_PROPERTIES},
                              lambda value: self._item(value, result, state, seeked))
            else:
                length = to_microseconds(result.get("totaltime"))
                if length and state.track.track_id != NO_TRACK:
                    state.track = Track(**{**state.track.__dict__, "length_us": length})
                self._publish(state, seeked=seeked)
        self._request("Player.GetProperties", {"playerid": player, "properties": PLAYER_PROPERTIES}, properties)

    def _item(self, value, player_properties: dict, state: State, seeked: bool) -> None:
        item = (value or {}).get("item") or {}
        track = track_from(item, player_properties)
        if track.track_id == self._state.track.track_id:
            track.art_url = self._state.track.art_url
        state.track = track
        self._publish(state, seeked=seeked)
        thumbnail = image_url((item.get("art") or {}).get("thumb") or item.get("thumbnail") or "")
        if thumbnail and not track.art_url:
            self._art(track.track_id, thumbnail, retries=2)

    def _art(self, track_id: str, url: str, retries: int) -> None:
        def found(result) -> None:
            textures = (result or {}).get("textures") or []
            uri = cached_art(textures[0].get("cachedurl", "")) if textures else ""
            if uri and self._state.track.track_id == track_id:
                state = State(**{**self._state.__dict__})
                state.track = Track(**{**self._state.track.__dict__, "art_url": uri})
                self._publish(state)
            elif not uri and retries > 0:
                # Kodi caches a thumbnail shortly after it first shows it.
                threading.Timer(3, lambda: self._art(track_id, url, retries - 1)).start()
        self._request("Textures.GetTextures", {"properties": ["cachedurl"],
                                               "filter": {"field": "url", "operator": "is", "value": url}}, found)

    def _publish(self, state: State, *, seeked: bool = False) -> None:
        state.measured_at = time.monotonic()
        self._state = state
        GLib.idle_add(lambda: (self.on_state(state, seeked), False)[1])

    # ── Commands from the desktop ──────────────────────────────────────────

    def _command(self, method: str, **params) -> None:
        if self._player_id is not None:
            self._request(method, {"playerid": self._player_id, **params})

    def play(self) -> None:
        self._command("Player.PlayPause", play=True)

    def pause(self) -> None:
        self._command("Player.PlayPause", play=False)

    def play_pause(self) -> None:
        self._command("Player.PlayPause")

    def stop(self) -> None:
        self._command("Player.Stop")

    def next(self) -> None:
        self._command("Player.GoTo", to="next")

    def previous(self) -> None:
        self._command("Player.GoTo", to="previous")

    def seek_by(self, offset_us: int) -> None:
        self._command("Player.Seek", value={"seconds": int(offset_us / 1e6)})

    def seek_to(self, position_us: int) -> None:
        self._command("Player.Seek", value={"time": from_microseconds(position_us)})
