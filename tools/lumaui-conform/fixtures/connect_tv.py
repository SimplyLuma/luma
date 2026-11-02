# SPDX-License-Identifier: Apache-2.0
"""lumaui-conform fixture: Luma Connect (src/luma-continuity) with synthetic state, no daemon.

Run by the conform harness as `bin/run --module connect_tv` (scenario tv-connect) with
LUMA_POSTURE=tv; LUMA_CONFORM_CONNECT_STATE picks the state the Design Center's TV mockup
(mockups/luma-tv-connect.html) shows:

  account sync devices phones   signed in, that page open
  out                           signed out (the welcome)
  solo                          phones without an account
  nophones off                  signed in, the Phones page with no phones / Connect off
  unavailable                   the daemon did not answer

The data mirrors the mockup's (fictional names, a 555 number, the mockup's address), so the
comparison measures layout rather than content. Nothing here talks to D-Bus or the network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("LUMA_POSTURE", "tv")
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

# No session services: the window draws from the state set below.
Gio.DBusProxy.new_for_bus = staticmethod(lambda *a, **k: None)
from luma_continuity import application as A, cloud_sync  # noqa: E402

cloud_sync.CloudSync.status = lambda self, cb: None
cloud_sync.CloudSync.available = property(lambda self: True)

# The spec's clock (scenario spec.clock, 2026-11-01 21:12 America/Chicago): "Today" and "Yesterday"
# read as the mockup's do.
NOW = 1793589120
_AT = lambda h, m: NOW - ((21 - h) * 60 + 12 - m) * 60  # noqa: E731 - a time of the spec's day


class _DateTime:
    new_now_local = staticmethod(lambda: GLib.DateTime.new_from_unix_local(NOW))
    new_from_unix_local = staticmethod(GLib.DateTime.new_from_unix_local)
    new_local = staticmethod(GLib.DateTime.new_local)


class _GLib:
    DateTime = _DateTime

    def __getattr__(self, name):
        return getattr(GLib, name)


from luma_continuity import connect_view  # noqa: E402

connect_view.GLib = _GLib()
SERVICES = [("calendar", "Calendar", True), ("photos", "Photos", True), ("weather-places", "Weather Places", True),
            ("world-clocks", "World Clocks", True), ("tide-sources", "Tide Servers", True), ("contacts", "Contacts", False),
            ("notes", "Notes", False), ("leaf-books", "Books", False), ("messages", "Messages", False), ("calls", "Calls", False)]
CLOUD = {
    "signed_in": True, "reachable": True, "hub": "https://hub.simplyluma.com",
    "account": {"name": "Nick"},
    "profile": {"name": "Nick", "email": "nick@simplyluma.com", "email_verified": True, "email_verified_by": "Google",
                "phone": "+14155550199", "phone_verified": True, "discoverable_by_phone": True,
                "sign_in": {"method": "google", "name": "Google", "manage_url": "https://hub.simplyluma.com/account"}},
    "device": {"id": "tv"},
    "devices": [{"id": "tv", "name": "Living room", "model": "Raspberry Pi 5", "services": [{"last_success_at": _AT(20, 45)}]},
                {"id": "xps", "name": "Nick’s XPS", "services": [{"last_success_at": _AT(21, 2)}]},
                {"id": "thinkpad", "name": "ThinkPad", "services": [{"last_success_at": _AT(23, 40) - 86400}]},
                {"id": "fold", "name": "Nick’s Fold", "services": [{"last_success_at": _AT(20, 51)}]}],
    "services": [{"id": k, "name": n, "enabled": e, "last_success_at": _AT(20, 45) if e else None} for k, n, e in SERVICES],
}
FOLD = {"fingerprint": "fold", "name": "Nick’s Fold", "model": "Pixel 9 Pro Fold", "reachable": True,
        "status": {"battery": 78, "network": "wifi"},
        "outgoing": ["device.ring", "media.control", "screen.view", "camera.stream", "call.control"],
        "incoming": ["notifications.mirror", "device.status", "links.open", "media.mirror", "input.remote"]}
PIXEL = {"fingerprint": "p9", "name": "Pixel 9", "model": "Pixel 9", "reachable": False, "last_seen_at": _AT(8, 12),
         "outgoing": ["device.ring", "media.control", "screen.view"],
         "incoming": ["notifications.mirror", "device.status", "media.mirror"]}
SIGNED_OUT = {"account_status": "signed_out", "enabled": True, "companion_devices": []}


def build(app: Adw.Application) -> None:
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    theme.add_search_path(str(Path(A.__file__).resolve().parents[1] / "data" / "icons"))
    try:
        import luma_appkit
        theme.add_search_path(str(Path(luma_appkit.__file__).resolve().parent / "icons"))
    except Exception:  # noqa: BLE001 - icons only
        pass
    which = os.environ.get("LUMA_CONFORM_CONNECT_STATE", "account")
    window = A.ConnectWindow(app)
    window.set_default_size(1920, 1080)
    state, cloud, panel = dict(SIGNED_OUT), {"signed_in": False, "services": [], "devices": []}, None
    if which in ("account", "sync", "devices", "phones", "nophones", "off"):
        cloud = CLOUD
        state = dict(SIGNED_OUT, companion_devices=[FOLD, PIXEL])
        panel = "phones" if which in ("phones", "nophones", "off") else which
        if which == "nophones":
            state["companion_devices"] = []
        if which == "off":
            state["enabled"] = False
    elif which == "solo":
        state = dict(SIGNED_OUT, companion_devices=[FOLD, PIXEL])
        window.phones_only = True
    elif which == "unavailable":
        cloud = None
        window.cloud_problem = "Luma Connect didn’t answer."
    window.state, window.cloud = state, cloud
    window._update()
    if panel:
        window.select_panel(panel)
    window.present()


def main() -> int:
    app = Adw.Application(application_id="org.projectluma.Connect", flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", build)
    return app.run(sys.argv[:1])


if __name__ == "__main__":
    raise SystemExit(main())
