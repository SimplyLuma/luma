# SPDX-License-Identifier: Apache-2.0
"""Chat: one merged feed from the channels a person pastes, read-only, with sending behind sign-in.

- Twitch: anonymous IRC over WebSocket (wss://irc-ws.chat.twitch.tv, a ``justinfan`` nick, tags on).
- Kick: the public Pusher socket; the chatroom id comes from kick.com/api/v2/channels/<slug>.
- YouTube needs sign-in to read (the Data API's liveChatMessages); TikTok has no public chat API.
  Both are refused with words, and sending on Twitch and YouTube waits for the signed-in path.

Parsing is pure (tested from recorded frames); the sockets use libsoup on the GLib main loop and
are open only while the panel asks (shown, or live).
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

TWITCH_IRC = "wss://irc-ws.chat.twitch.tv:443"
#: Kick's public Pusher app (key, cluster). Kick rotates it; $STREAMY_KICK_PUSHER="key:cluster" or
#: oauth.json {"kick": {"pusher_key", "pusher_cluster"}} overrides it without a release.
KICK_PUSHER_APP = ("32cbd69e4b950bf97679", "us2")
KICK_CHANNEL_API = "https://kick.com/api/v2/channels/{slug}"
#: Messages the feed keeps (older ones scroll away).
KEEP = 500


@dataclass
class ChatMessage:
    platform: str  # twitch | kick | youtube
    channel: str
    author: str
    text: str
    color: str = ""
    badges: list = field(default_factory=list)  # "broadcaster", "moderator", "vip", "subscriber", …
    at: float = 0.0
    id: str = ""
    kind: str = "message"  # message | notice


@dataclass(frozen=True)
class Source:
    platform: str
    channel: str  # a Twitch login or a Kick slug

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.channel}"


class LinkError(ValueError):
    """Why a pasted link can't be read, in words."""


_SLUG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,39}$")


def parse_link(text: str) -> Source:
    """A pasted channel or chat link (or "twitch.tv/name", "kick.com/name") as a Source."""
    text = text.strip()
    if not text:
        raise LinkError("Paste a Twitch or Kick channel link")
    url = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
    host = (url.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in url.path.split("/") if p]
    if host.endswith("twitch.tv"):
        if parts and parts[0] in ("popout", "embed") and len(parts) > 1:
            parts = parts[1:]
        if not parts or not _SLUG.match(parts[0]) or parts[0] in ("directory", "videos", "settings"):
            raise LinkError("That Twitch link doesn't name a channel")
        return Source("twitch", parts[0].lower())
    if host.endswith("kick.com"):
        if parts and parts[0] == "popout" and len(parts) > 1:
            parts = parts[1:]
        if not parts or not _SLUG.match(parts[0]):
            raise LinkError("That Kick link doesn't name a channel")
        return Source("kick", parts[0].lower())
    if host.endswith("youtube.com") or host == "youtu.be":
        raise LinkError("YouTube chat needs you to sign in to YouTube (Destinations)")
    if host.endswith("tiktok.com"):
        raise LinkError("TikTok has no public chat to read yet")
    raise LinkError("Streamy reads Twitch and Kick chat from a link")


# ── Twitch IRC ──

def twitch_hello(channel: str, nick: str | None = None) -> list[str]:
    """The lines an anonymous reader sends after connecting."""
    nick = nick or f"justinfan{random.randint(10000, 99999)}"
    return ["CAP REQ :twitch.tv/tags twitch.tv/commands", "PASS SCHMOOPIIE", f"NICK {nick}", f"JOIN #{channel}"]


def _unescape_tag(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append({"s": " ", ":": ";", "\\": "\\", "r": "\r", "n": "\n"}.get(value[i + 1], value[i + 1]))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_irc(line: str) -> dict:
    """One IRC line: {"tags", "prefix", "command", "params"}."""
    tags, prefix = {}, ""
    if line.startswith("@"):
        raw, _, line = line[1:].partition(" ")
        for item in raw.split(";"):
            key, _, value = item.partition("=")
            tags[key] = _unescape_tag(value)
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    params = line.split()
    command = params.pop(0) if params else ""
    if trailing is not None:
        params.append(trailing)
    return {"tags": tags, "prefix": prefix, "command": command, "params": params}


def twitch_frame(frame: str) -> tuple[list[ChatMessage], list[str]]:
    """A WebSocket frame from Twitch: (messages, lines to answer with)."""
    messages, replies = [], []
    for line in frame.split("\r\n"):
        if not line:
            continue
        irc = parse_irc(line)
        command = irc["command"]
        if command == "PING":
            replies.append("PONG :" + (irc["params"][0] if irc["params"] else "tmi.twitch.tv"))
        elif command == "PRIVMSG" and len(irc["params"]) == 2:
            tags = irc["tags"]
            text = irc["params"][1]
            kind = "message"
            if text.startswith("\x01ACTION ") and text.endswith("\x01"):
                text, kind = text[8:-1], "action"
            badges = [b.split("/")[0] for b in tags.get("badges", "").split(",") if b]
            author = tags.get("display-name") or irc["prefix"].split("!")[0]
            sent = tags.get("tmi-sent-ts", "")
            messages.append(ChatMessage("twitch", irc["params"][0].lstrip("#"), author, text, tags.get("color", ""),
                                        badges, int(sent) / 1000 if sent.isdigit() else time.time(),
                                        tags.get("id", ""), kind))
        elif command == "USERNOTICE" and irc["tags"].get("system-msg"):
            messages.append(ChatMessage("twitch", irc["params"][0].lstrip("#"), "", irc["tags"]["system-msg"],
                                        at=time.time(), id=irc["tags"].get("id", ""), kind="notice"))
        elif command == "CLEARCHAT" or command == "CLEARMSG":
            messages.append(ChatMessage("twitch", irc["params"][0].lstrip("#") if irc["params"] else "", "",
                                        "", at=time.time(), id=irc["tags"].get("target-msg-id", ""), kind="clear"))
    return messages, replies


# ── Kick (Pusher) ──

def kick_pusher_url() -> str:
    import os

    key, cluster = KICK_PUSHER_APP
    override = os.environ.get("STREAMY_KICK_PUSHER", "")
    if ":" in override:
        key, cluster = override.split(":", 1)
    else:
        from .accounts import config_file

        found = config_file().get("kick") or {}
        key, cluster = found.get("pusher_key", key), found.get("pusher_cluster", cluster)
    return f"wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0-rc2&flash=false"


def kick_subscribe(chatroom_id: int) -> str:
    return json.dumps({"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"}})


def kick_chatroom(channel_json: dict) -> int:
    """The chatroom id in kick.com/api/v2/channels/<slug>'s answer."""
    room = channel_json.get("chatroom") or {}
    if not isinstance(room, dict) or not isinstance(room.get("id"), int):
        raise LinkError("Kick didn't say where this channel's chat is")
    return room["id"]


def kick_frame(frame: str, channel: str) -> tuple[list[ChatMessage], list[str]]:
    """A Pusher frame from Kick: (messages, frames to answer with)."""
    try:
        event = json.loads(frame)
    except ValueError:
        return [], []
    name = event.get("event", "")
    if name == "pusher:ping":
        return [], [json.dumps({"event": "pusher:pong", "data": {}})]
    if name not in ("App\\Events\\ChatMessageEvent", "App\\Events\\MessageDeletedEvent"):
        return [], []
    data = event.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return [], []
    if not isinstance(data, dict):
        return [], []
    if name == "App\\Events\\MessageDeletedEvent":
        return [ChatMessage("kick", channel, "", "", id=str((data.get("message") or {}).get("id", "")),
                            kind="clear")], []
    sender = data.get("sender") or {}
    identity = sender.get("identity") or {}
    badges = [b.get("type", "") for b in identity.get("badges", []) if isinstance(b, dict)]
    text = re.sub(r"\[emote:\d+:([^\]]+)\]", r"\1", str(data.get("content", "")))
    return [ChatMessage("kick", channel, sender.get("username", ""), text, identity.get("color", ""), badges,
                        time.time(), str(data.get("id", "")))], []


# ── The feed ──

class Feed:
    """Every message from every connected source, newest last; listeners hear each new one."""

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []
        self.sources: dict[str, Source] = {}
        self.states: dict[str, tuple[str, str]] = {}  # source key -> (state, words)
        self.listeners: list[Callable[[ChatMessage | None], None]] = []

    def add(self, message: ChatMessage) -> None:
        if message.kind == "clear":
            self.messages = [m for m in self.messages if not (message.id and m.id == message.id)]
            self._tell(None)
            return
        self.messages.append(message)
        del self.messages[:-KEEP]
        self._tell(message)

    def set_state(self, source: Source, state: str, words: str = "") -> None:
        self.states[source.key] = (state, words)
        self._tell(None)

    def _tell(self, message: ChatMessage | None) -> None:
        for listener in list(self.listeners):
            listener(message)


# ── Sockets (GLib main loop) ──

class Connection:
    """One source's socket: connects, answers pings, reconnects with backoff, closes on ``close()``."""

    def __init__(self, source: Source, feed: Feed) -> None:
        self.source, self.feed = source, feed
        self.socket = None
        self.closed = False
        self.attempt = 0
        self.timer = 0
        self._session = None
        self._cancel = None
        self.chatroom: int | None = None

    def open(self) -> None:
        import gi

        gi.require_version("Soup", "3.0")
        from gi.repository import Gio, Soup

        self.closed = False
        self._session = self._session or Soup.Session(user_agent="Streamy")
        self._cancel = Gio.Cancellable()
        self.feed.set_state(self.source, "connecting", "Connecting…")
        if self.source.platform == "kick" and self.chatroom is None:
            self._find_chatroom()
            return
        url = TWITCH_IRC if self.source.platform == "twitch" else kick_pusher_url()
        message = Soup.Message.new("GET", url)
        self._session.websocket_connect_async(message, None, None, 0, self._cancel, self._connected)

    def _find_chatroom(self) -> None:
        from gi.repository import GLib, Soup

        message = Soup.Message.new("GET", KICK_CHANNEL_API.format(slug=self.source.channel))
        message.get_request_headers().append("Accept", "application/json")

        def read(session, result) -> None:
            try:
                body = session.send_and_read_finish(result).get_data()
                status = message.get_status()
                if status == 404:
                    raise LinkError("Kick has no channel by that name")
                if status != 200:
                    raise LinkError("Kick didn't answer (it may be blocking apps); try again later")
                self.chatroom = kick_chatroom(json.loads(bytes(body)))
            except (LinkError, ValueError, GLib.Error) as error:
                self._failed(str(error) if isinstance(error, LinkError) else "Kick didn't answer")
                return
            if not self.closed:
                self.open()

        self._session.send_and_read_async(message, 0, self._cancel, read)

    def _connected(self, session, result) -> None:
        from gi.repository import GLib, Soup

        try:
            self.socket = session.websocket_connect_finish(result)
        except GLib.Error as error:
            if not self.closed:
                self._failed("Couldn't connect to the chat" if "cancel" not in error.message.lower() else "")
            return
        self.socket.set_keepalive_interval(0)
        self.socket.connect("message", self._message)
        self.socket.connect("closed", self._closed)
        if self.source.platform == "twitch":
            for line in twitch_hello(self.source.channel):
                self.socket.send_text(line)
        self.attempt = 0
        self.feed.set_state(self.source, "live", "Connected")
        _ = Soup  # the type the socket is

    def _closed(self, socket) -> None:
        if self.source.platform == "kick" and 4000 <= socket.get_close_code() < 4100:
            # Pusher refused the app key (4001) or the app (40xx): retrying can't help.
            self.socket = None
            self.feed.set_state(self.source, "error", "Kick's chat service refused Streamy; Streamy needs an update")
            return
        self._failed("The chat connection closed")

    def _message(self, _socket, _kind, data) -> None:
        text = bytes(data.get_data()).decode("utf-8", "replace")
        if self.source.platform == "twitch":
            messages, replies = twitch_frame(text)
        else:
            if '"pusher:connection_established"' in text and self.chatroom is not None:
                self.socket.send_text(kick_subscribe(self.chatroom))
            messages, replies = kick_frame(text, self.source.channel)
        for reply in replies:
            self.socket.send_text(reply)
        for message in messages:
            self.feed.add(message)

    def _failed(self, words: str) -> None:
        from gi.repository import GLib

        self.socket = None
        if self.closed:
            return
        if words.startswith(("Kick has no", "That ")):
            self.feed.set_state(self.source, "error", words)
            return
        self.attempt += 1
        delay = min(2.0 * 1.5 ** (self.attempt - 1), 120.0)
        self.feed.set_state(self.source, "reconnecting", words or "Reconnecting…")
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(int(delay * 1000), self._again)

    def _again(self) -> bool:
        self.timer = 0
        if not self.closed:
            self.open()
        return False

    def close(self) -> None:
        from gi.repository import GLib

        self.closed = True
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self._cancel is not None:
            self._cancel.cancel()
        if self.socket is not None:
            try:
                self.socket.close(1000, None)
            except Exception:
                pass
            self.socket = None
        self.feed.set_state(self.source, "off", "")


def send(source: Source, text: str) -> str:
    """Sending needs the platform's sign-in (Twitch: user:write:chat; YouTube: liveChatMessages.insert).
    Until a signed-in destination is wired to the chat, this says so."""
    if source.platform == "kick":
        return "Kick chat is read-only in Streamy"
    return f"Sign in to {source.platform.capitalize()} in Destinations to send messages"
