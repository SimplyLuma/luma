# SPDX-License-Identifier: Apache-2.0
"""Ownership and lifecycle for notifications from the Android system service.

The transport admits system_server before entering here. Host IDs are scoped
to one notification-server generation; they are never application identities.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
import re
import threading


PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
MAX_NOTIFICATIONS = 4096
MAX_ACTIONS = 32
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def text(value: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError("Invalid Android notification text")
    return value


@dataclass(frozen=True)
class Notification:
    package: str
    app_name: str
    summary: str
    body: str
    actions: tuple[tuple[str, str], ...] = ()
    category: str = ""
    urgency: int = 1
    resident: bool = True
    transient: bool = False
    suppress_sound: bool = True
    timeout: int = -1
    image: object = None

    def validate(self) -> None:
        if (not isinstance(self.package, str) or len(self.package) > 255
                or not PACKAGE.fullmatch(self.package)):
            raise ValueError("Invalid Android notification package")
        for value, limit in ((self.app_name, 256), (self.summary, 4096),
                             (self.body, 65536), (self.category, 256)):
            text(value, limit)
        if self.urgency not in (0, 1, 2) or not -1 <= self.timeout <= 86400000:
            raise ValueError("Invalid Android notification policy")
        if len(self.actions) > MAX_ACTIONS:
            raise ValueError("Too many Android notification actions")
        seen = set()
        for action, label in self.actions:
            text(action, 256)
            text(label, 256)
            if not action or action in seen:
                raise ValueError("Invalid Android notification action")
            seen.add(action)
        if self.image is not None:
            image = self.image
            channels = 4 if image.has_alpha else 3
            if (not isinstance(image.width, int) or not isinstance(image.height, int)
                    or not isinstance(image.rowstride, int)
                    or not 0 < image.width <= 2048 or not 0 < image.height <= 2048
                    or image.rowstride < image.width * channels
                    or image.rowstride * image.height > MAX_IMAGE_BYTES
                    or len(image.data) != image.rowstride * image.height):
                raise ValueError("Invalid Android notification image")


class NotificationBridge:
    """Share exactly the Android-owned records with one host notification sink.

    The sink implements notify(record, replacement) and close(id). Listeners
    implement action(id, key, activation_token), dismissed(id), and reset().
    Neither a banner timeout nor producer withdrawal means user dismissal.
    """

    def __init__(self, sink):
        self.sink = sink
        self.records: dict[int, Notification] = {}
        self.tokens: dict[int, str] = {}
        self.listeners = []
        self.lock = threading.RLock()
        self.generation = 0

    def register(self, listener):
        with self.lock:
            self.listeners.append(listener)

    def unregister(self, listener):
        with self.lock:
            if listener in self.listeners:
                self.listeners.remove(listener)

    def post(self, record: Notification, replaces: int = 0) -> int:
        record.validate()
        with self.lock:
            old = self.records.get(replaces)
            # An app cannot replace another app's record, including native
            # records or IDs recycled after a notification-server restart.
            replacement = replaces if old and old.package == record.package else 0
            if not replacement and len(self.records) >= MAX_NOTIFICATIONS:
                return 0
            generation = self.generation
            notification_id = int(self.sink.notify(record, replacement))
            if generation != self.generation or notification_id <= 0:
                return 0
            if replacement and replacement != notification_id:
                self.records.pop(replacement, None)
                self.tokens.pop(replacement, None)
            self.records[notification_id] = record
            self.tokens.pop(notification_id, None)
            return notification_id

    def withdraw(self, notification_id: int):
        with self.lock:
            if notification_id not in self.records:
                return
            self.records.pop(notification_id)
            self.tokens.pop(notification_id, None)
            self.sink.close(notification_id)

    def activation_token(self, notification_id: int, token: str):
        with self.lock:
            if notification_id in self.records:
                self.tokens[notification_id] = text(token, 4096)

    def action(self, notification_id: int, action: str):
        with self.lock:
            record = self.records.get(notification_id)
            if record is None or action not in {key for key, _ in record.actions}:
                return
            token = self.tokens.pop(notification_id, "")
            listeners = tuple(self.listeners)
        for listener in listeners:
            listener.action(notification_id, action, token)

    def closed(self, notification_id: int, reason: int):
        with self.lock:
            owned = self.records.pop(notification_id, None)
            self.tokens.pop(notification_id, None)
            listeners = tuple(self.listeners)
        if owned is not None and reason == 2:
            for listener in listeners:
                listener.dismissed(notification_id)

    def server_changed(self):
        with self.lock:
            self.generation += 1
            self.records.clear()
            self.tokens.clear()
            listeners = tuple(self.listeners)
        for listener in listeners:
            listener.reset()


def escaped_body(record: Notification) -> str:
    # Android CharSequences arrive as plain strings. Do not reinterpret app
    # text as host notification markup or allow it to inject links/images.
    return html.escape(record.body, quote=False)
