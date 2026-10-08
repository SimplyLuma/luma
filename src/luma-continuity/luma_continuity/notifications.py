"""Narrow client of the existing Shell owner's experimental export ABI.

The Connect broker must own its reviewed local bus identity. This adapter does
not own org.freedesktop.Notifications, scrape actors, or observe Notify traffic.
GNOME/Phosh owner integration and source consent are explicit runtime gates.
"""
import json
from .policy import IDENTIFIER
from .transport import encode


class NativeNotifications:
    BUS = "org.gnome.Shell"
    PATH = "/org/projectluma/Connect/Notifications"
    INTERFACE = "org.projectluma.Connect.NotificationExport1"

    def __init__(self, peer, session, *, authorized, caller):
        self.peer, self.session = peer, session
        self.authorized, self.caller = authorized, caller

    def __call__(self, capability, payload):
        if not self.authorized():
            raise PermissionError("notification grant revoked")
        if capability == "notifications.read" and payload == {"operation": "snapshot"}:
            raw = self.caller("Snapshot", "(ss)", (self.peer, self.session))
            if not isinstance(raw, str) or len(raw.encode()) > 700000:
                raise ValueError("invalid notification snapshot")
            result = json.loads(raw)
            if (not isinstance(result, dict) or set(result) != {"generation", "removed", "records"}
                    or type(result["generation"]) is not int or result["generation"] < 0
                    or not isinstance(result["records"], list) or len(result["records"]) > 100
                    or not isinstance(result["removed"], list) or len(result["removed"]) > 100):
                raise ValueError("invalid notification snapshot")
            for removed in result["removed"]:
                if not isinstance(removed, str) or not IDENTIFIER.fullmatch(removed):
                    raise ValueError("invalid removed identity")
            for record in result["records"]:
                if (not isinstance(record, dict) or set(record) != {"id", "title", "body", "actions"}
                        or not isinstance(record["id"], str) or not IDENTIFIER.fullmatch(record["id"])
                        or not isinstance(record["title"], str) or len(record["title"]) > 512
                        or not isinstance(record["body"], str) or len(record["body"]) > 4096
                        or not isinstance(record["actions"], list) or len(record["actions"]) > 4):
                    raise ValueError("invalid notification record")
                for action in record["actions"]:
                    if (not isinstance(action, dict) or set(action) != {"id", "label"}
                            or not isinstance(action["id"], str) or not IDENTIFIER.fullmatch(action["id"])
                            or not isinstance(action["label"], str) or len(action["label"]) > 128):
                        raise ValueError("invalid notification action")
            if not self.authorized():
                raise PermissionError("notification grant revoked")
            return result
        if capability == "notifications.act":
            if (set(payload) != {"action", "operation"}
                    or not all(isinstance(v, str) and IDENTIFIER.fullmatch(v) for v in payload.values())):
                raise ValueError("invalid notification action")
            state = self.caller("Invoke", "(ssss)",
                (self.peer, self.session, payload["action"], payload["operation"]))
            if state not in {"complete", "unknown"}:
                raise ValueError("invalid action receipt")
            return {"state": state}
        raise NotImplementedError("notification operation unavailable")

    @classmethod
    def gio_caller(cls, unique_owner):
        from gi.repository import Gio, GLib
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Caller must resolve and watch the authoritative Shell unique owner.
        # Calling its unique name prevents name-owner changes redirecting requests.
        if not isinstance(unique_owner, str) or not unique_owner.startswith(":"):
            raise ValueError("unique notification owner required")
        def call(method, signature, values):
            return connection.call_sync(unique_owner, cls.PATH, cls.INTERFACE,
                method, GLib.Variant(signature, values), None,
                Gio.DBusCallFlags.NO_AUTO_START, 5000, None).unpack()[0]
        return call
