# SPDX-License-Identifier: Apache-2.0
"""The Chat panel: every pasted channel's chat in one feed, a platform badge on each line.

Sources are kept in <data>/chat.json; their sockets are open only while the panel is shown or
something is live (and for 10 s after, so flipping tabs doesn't reconnect).

    panel = ChatPanel(center=window.center, host=window.host, path=data_dir() / "chat.json", outputs=outputs)
    DockTile("chat", "Chat", "message-circle", panel, phone="drawer", actions=panel.actions)
"""
from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from luma_appkit import (  # noqa: E402
    AppIcon, ChatFeed, ChatLine, IconOnlyButton, ListEmptyState, NavigationList, PanelField, PanelHeading, RowAction, SidebarRow,
    TextField, Toast,
)
from luma_appkit.bar_panel import panel_list  # noqa: E402

from . import chat  # noqa: E402
from .chat import ChatMessage, Connection, Feed, LinkError, Source  # noqa: E402
from .destinations_panel import logos_dir  # noqa: E402

LINGER_S = 10
SHOWN = 200  # lines drawn; the feed keeps more
PLATFORM_NAMES = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}


def badge(platform: str, size: int = 16) -> Gtk.Widget:
    from luma_appkit.svg_icons import SvgIcon

    path = logos_dir() / f"{platform}.svg"
    icon = AppIcon(picture=SvgIcon(str(path), size), name=PLATFORM_NAMES.get(platform, platform), size=size)
    icon.update_property([Gtk.AccessibleProperty.LABEL], [PLATFORM_NAMES.get(platform, platform)])
    return icon


def small_badge(platform: str) -> Gtk.Widget:
    """A platform's logo in ChatLine's 16 px slot."""
    from luma_appkit.svg_icons import SvgIcon

    image = Gtk.Image(paintable=SvgIcon(str(logos_dir() / f"{platform}.svg"), 16), pixel_size=16)
    image.update_property([Gtk.AccessibleProperty.LABEL], [PLATFORM_NAMES.get(platform, platform)])
    return image


def line_for(message: ChatMessage) -> Gtk.Widget:
    return ChatLine(message.author, message.text, lead=small_badge(message.platform) if message.kind != "notice" else None,
                    color=message.color, badges=message.badges,
                    kind=message.kind if message.kind in ("message", "action", "notice") else "message",
                    time=message.at or None)


class ChatPanel(Gtk.Box):
    __gtype_name__ = "StreamyChatPanel"

    def __init__(self, *, center=None, host: Gtk.Widget | None = None, path: Path | None = None, outputs=None,
                 feed: Feed | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("streamy-chat")
        self.center, self.host, self.path, self.outputs = center, host, path, outputs
        self.offline = feed is not None  # a given feed (check runs) never connects
        self.feed = feed or Feed()
        self.connections: dict[str, Connection] = {}
        self._linger = 0
        self._source_rows: dict[str, SidebarRow] = {}
        self.sources_nav = NavigationList("files")
        self.sources_nav.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.append(self.sources_nav)
        self.lines = ChatFeed(max_lines=SHOWN)
        self.lines.set_vexpand(True)
        self.scroll = self.lines
        self._by_id: dict[str, Gtk.Widget] = {}
        self.empty = ListEmptyState("Paste a Twitch or Kick channel link to read its chat")
        self.append(self.empty)
        self.append(self.scroll)
        self.composer = TextField("Message", placeholder="Sign in to Twitch or YouTube to send",
                                  on_activate=self._send)
        for side in ("start", "end", "bottom"):
            getattr(self.composer, f"set_margin_{side}")(8)
        self.append(self.composer)
        self.actions = [IconOnlyButton("plus", "Add a chat", on_click=self.add_menu)]
        if not self.offline:
            for key in self._load():
                platform, _, channel = key.partition(":")
                self.feed.sources[key] = Source(platform, channel)
        self.feed.listeners.append(self._heard)
        self.connect("map", lambda *_a: self._sync())
        self.connect("unmap", lambda *_a: self._sync())
        if outputs is not None:
            outputs.connect("row-state", lambda *_a: self._sync())
        self._sources()
        for message in self.feed.messages[-SHOWN:]:
            self._append(message)
        self._empty()

    # ── Sources ──

    def _load(self) -> list[str]:
        if self.path is None:
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [k for k in data.get("sources", []) if isinstance(k, str) and ":" in k] if isinstance(data, dict) else []

    def _save(self) -> None:
        if self.path is None or self.offline:
            return
        from .model import atomic_write

        atomic_write(self.path, json.dumps({"sources": list(self.feed.sources)}))

    def add_menu(self) -> None:
        field = PanelField("link", "Paste a Twitch or Kick channel link", on_submit=self.add_link)
        self.center.grow("chat-add", panel_list([PanelHeading("Read a chat"), field], label="Read a chat"),
                         entry=field.entry)

    def add_link(self, text: str) -> bool:
        try:
            source = chat.parse_link(text)
        except LinkError as error:
            if self.host is not None:
                Toast.show(self.host, str(error), kind="warning")
            return False
        if self.center is not None:
            self.center.fold()
        if source.key in self.feed.sources:
            return True
        self.feed.sources[source.key] = source
        self._save()
        self._sources()
        self._sync()
        return True

    def remove(self, key: str) -> None:
        connection = self.connections.pop(key, None)
        if connection is not None:
            connection.close()
        self.feed.sources.pop(key, None)
        self.feed.states.pop(key, None)
        self._save()
        self._sources()
        self._empty()

    def _sources(self) -> None:
        listing = self.sources_nav.list
        for key in list(self._source_rows):
            if key not in self.feed.sources:
                listing.remove(self._source_rows.pop(key))
        for key, source in self.feed.sources.items():
            row = self._source_rows.get(key)
            state, words = self.feed.states.get(key, ("off", ""))
            words = words or ("Connects while this panel is open or you're live" if state == "off" else "")
            if row is None:
                row = SidebarRow(source.channel, lead=badge(source.platform, 22), subtitle=words,
                                 trail=RowAction("x", f"Stop reading {source.channel}",
                                                 lambda k=key: self.remove(k), quiet=True))
                row.set_activatable(False)
                self._source_rows[key] = row
                listing.append(row)
            else:
                row.set_subtitle(words)
        self.sources_nav.set_visible(bool(self.feed.sources))

    # ── Connecting: only while shown or live ──

    def _wanted(self) -> bool:
        return self.get_mapped() or (self.outputs is not None and self.outputs.live())

    def _sync(self) -> None:
        if self.offline:
            return
        if self._wanted():
            if self._linger:
                GLib.source_remove(self._linger)
                self._linger = 0
            for key, source in self.feed.sources.items():
                if key not in self.connections:
                    connection = self.connections[key] = Connection(source, self.feed)
                    connection.open()
        elif self.connections and not self._linger:
            self._linger = GLib.timeout_add_seconds(LINGER_S, self._close_all)

    def _close_all(self) -> bool:
        self._linger = 0
        if self._wanted():
            return False
        for connection in self.connections.values():
            connection.close()
        self.connections.clear()
        return False

    # ── The feed ──

    def _heard(self, message: ChatMessage | None) -> None:
        GLib.idle_add(lambda: self._show(message) and False)

    def _append(self, message: ChatMessage) -> None:
        line = line_for(message)
        self.lines.append(line)
        if message.id:
            self._by_id[message.id] = line
            if len(self._by_id) > SHOWN * 2:
                for key in list(self._by_id)[:SHOWN]:
                    del self._by_id[key]

    def _show(self, message: ChatMessage | None) -> None:
        if message is None:  # a state change, or a message removed (a moderator deleted it: dimmed)
            self._sources()
            shown = {m.id for m in self.feed.messages if m.id}
            for key, line in self._by_id.items():
                if key not in shown:
                    line.set_dimmed(True)
            self._empty()
            return
        self._append(message)
        self._empty()

    def _empty(self) -> None:
        has = bool(self.feed.messages)
        self.empty.set_visible(not has)
        self.scroll.set_visible(has)
        if not has and self.feed.sources:
            self.empty.set_label("No messages yet")

    def _send(self, text: str) -> None:
        source = next(iter(self.feed.sources.values()), None)
        if self.host is not None:
            Toast.show(self.host, chat.send(source, text) if source else "Add a chat first", kind="warning")

    def measure(self, root: Gtk.Widget) -> dict:
        from .shots import _box

        return {"sources": [_box(r, root) for r in self._source_rows.values()],
                "lines": [_box(r, root) for r in _children(self.lines.list)][-8:],
                "composer": _box(self.composer, root), "scroll": _box(self.scroll, root)}


def _children(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def fixture_feed() -> Feed:
    """Messages from both platforms for check runs (from the recorded test sessions)."""
    feed = Feed()
    twitch, kick = Source("twitch", "novaplays"), Source("kick", "orbit")
    feed.sources = {twitch.key: twitch, kick.key: kick}
    feed.states = {twitch.key: ("live", "Connected"), kick.key: ("reconnecting", "The chat connection closed")}
    for message in (
        ChatMessage("twitch", "novaplays", "NovaPlays", "Welcome in, starting soon", "#9146FF", ["broadcaster"]),
        ChatMessage("twitch", "novaplays", "Pixel_Fox", "hype; the new overlay looks great", "#1E90FF",
                    ["moderator", "subscriber"]),
        ChatMessage("kick", "orbit", "Orbit", "gg KEKW", "#53FC18", ["moderator"]),
        ChatMessage("twitch", "novaplays", "quiet one", "waves", kind="action"),
        ChatMessage("kick", "orbit", "newviewer", "first time here, how do you get the vertical and the landscape "
                    "canvas going at the same time?"),
        ChatMessage("twitch", "novaplays", "", "12 raiders from Orbit have joined!", kind="notice"),
    ):
        feed.messages.append(message)
    return feed
