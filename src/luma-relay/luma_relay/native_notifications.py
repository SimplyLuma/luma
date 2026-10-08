from __future__ import annotations

import json
import os
import re
import selectors
import socket
import time
from collections import deque
from pathlib import Path
from threading import Event
from typing import Any, Protocol

from .errors import RelayError


MAX_PACKET_BYTES = 16 * 1024
MAX_TITLE_CHARS = 160
MAX_BODY_CHARS = 4096
NOTIFICATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


class Publisher(Protocol):
    def add(self, notification_id: str, title: str, body: str, urgency: int) -> None: ...

    def remove(self, notification_id: str) -> None: ...


def _plain_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise RelayError("Relay rejected a notification with invalid text.")
    cleaned = "".join(
        character
        for character in value
        if character in "\n\t" or ord(character) >= 0x20
    )
    return cleaned[:limit]


def validate_notification(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RelayError("Relay rejected an unknown notification message.")
    operation = payload.get("operation")
    notification_id = payload.get("id")
    if operation not in {"add", "remove"} or not isinstance(notification_id, str):
        raise RelayError("Relay rejected an incomplete notification message.")
    if not NOTIFICATION_ID.fullmatch(notification_id):
        raise RelayError("Relay rejected an invalid notification identifier.")
    if operation == "remove":
        return {"schema": 1, "operation": "remove", "id": notification_id}
    title = _plain_text(payload.get("title", ""), MAX_TITLE_CHARS)
    body = _plain_text(payload.get("body", ""), MAX_BODY_CHARS)
    if not title and not body:
        raise RelayError("Relay rejected an empty notification.")
    urgency = payload.get("urgency", 1)
    if not isinstance(urgency, int) or urgency not in {0, 1, 2}:
        urgency = 1
    return {
        "schema": 1,
        "operation": "add",
        "id": notification_id,
        "title": title,
        "body": body,
        "urgency": urgency,
    }


class FreedesktopPublisher:
    """Publish as the installed Windows app without exposing D-Bus to Wine."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as error:
            raise RelayError("Luma's native notification service is unavailable.") from error
        self.Gio = Gio
        self.GLib = GLib
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.app_name = str(manifest.get("name", "Windows Application"))[:128]
        self.app_icon = str(manifest.get("icon", "application-x-executable"))[:256]
        app_id = str(manifest["app_id"])
        self.desktop_entry = f"org.projectluma.Relay.Windows.{app_id}"
        self.replacements: dict[str, int] = {}

    def add(self, notification_id: str, title: str, body: str, urgency: int) -> None:
        GLib = self.GLib
        hints = {
            "desktop-entry": GLib.Variant("s", self.desktop_entry),
            "urgency": GLib.Variant("y", urgency),
            "category": GLib.Variant("s", "x-luma.relay.windows"),
        }
        parameters = GLib.Variant(
            "(susssasa{sv}i)",
            (
                self.app_name,
                self.replacements.get(notification_id, 0),
                self.app_icon,
                title or self.app_name,
                body,
                [],
                hints,
                -1,
            ),
        )
        result = self.connection.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            parameters,
            GLib.VariantType.new("(u)"),
            self.Gio.DBusCallFlags.NONE,
            5000,
            None,
        )
        self.replacements[notification_id] = int(result.unpack()[0])

    def remove(self, notification_id: str) -> None:
        native_id = self.replacements.pop(notification_id, None)
        if native_id is None:
            return
        self.connection.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "CloseNotification",
            self.GLib.Variant("(u)", (native_id,)),
            None,
            self.Gio.DBusCallFlags.NONE,
            5000,
            None,
        )


class NotificationBroker:
    def __init__(
        self,
        socket_path: Path,
        manifest: dict[str, Any],
        publisher: Publisher | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.manifest = manifest
        self.publisher = publisher or FreedesktopPublisher(manifest)
        self._recent: deque[float] = deque()

    def _rate_limited(self) -> bool:
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        if len(self._recent) >= 30:
            return True
        self._recent.append(now)
        return False

    @staticmethod
    def _read_packet(connection: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        connection.settimeout(1.0)
        while total <= MAX_PACKET_BYTES:
            block = connection.recv(min(4096, MAX_PACKET_BYTES + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
        if total > MAX_PACKET_BYTES:
            raise RelayError("Relay rejected an oversized notification.")
        return b"".join(chunks)

    def _handle(self, connection: socket.socket) -> None:
        if self._rate_limited():
            return
        try:
            packet = self._read_packet(connection)
            payload = validate_notification(json.loads(packet.decode("utf-8")))
            if payload["operation"] == "remove":
                self.publisher.remove(payload["id"])
            else:
                self.publisher.add(
                    payload["id"], payload["title"], payload["body"], payload["urgency"]
                )
        except Exception:
            # Untrusted application data never terminates the application session.
            return

    def serve(self, stop: Event, ready: Event | None = None) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            listener.listen(8)
            listener.setblocking(False)
            if ready is not None:
                ready.set()
            selector = selectors.DefaultSelector()
            selector.register(listener, selectors.EVENT_READ)
            while not stop.is_set():
                for key, _mask in selector.select(timeout=0.25):
                    connection, _address = key.fileobj.accept()
                    with connection:
                        self._handle(connection)
        except OSError:
            # Application launch remains available if the private endpoint cannot start.
            return
        finally:
            if ready is not None and not ready.is_set():
                ready.set()
            listener.close()
            self.socket_path.unlink(missing_ok=True)
