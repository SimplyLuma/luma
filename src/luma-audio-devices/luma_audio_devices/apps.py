# SPDX-License-Identifier: MPL-2.0
"""Which app a sound stream belongs to, and what to call it.

The key rule is shared with WirePlumber (data/wireplumber/scripts/lib/
luma-app-audio.lua): the mixer, the remembered per-app volumes and the
capture sessions must agree about which app a stream belongs to, so both are
checked against tests/luma-audio-devices/fixtures/app-keys.json.

``DesktopIndex`` turns a key and the stream's properties into a name and an
icon from the app's desktop entry, so the mixer shows "Firefox" with its
icon rather than "firefox" or "AudioStream".
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re

__all__ = (
    "KEY_PROPERTIES", "SCOPED_EVENT_APPS", "SYSTEM_KEY", "app_key", "is_system_sound", "is_app_playback",
    "is_portal_remote", "capture_includes", "cubic_to_linear", "linear_to_cubic",
    "stream_volume", "AppIdentity", "DesktopIndex",
)

log = logging.getLogger("luma-audio-devices")

KEY_PROPERTIES = (
    "pipewire.access.portal.app_id",
    "application.id",
    "application.name",
    "application.process.binary",
)
SYSTEM_ROLES = frozenset(("notification", "event"))
SYSTEM_KEY = "@system"
# Apps whose own event sounds have their own level: an event stream that
# carries one of these app IDs (application.id or the portal's app ID, never
# a name or a binary) is that app's, not System sounds.
SCOPED_EVENT_APPS = frozenset(("org.projectluma.Messages",))


def _nonempty(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def app_key(props: dict, client: dict | None = None) -> tuple[str | None, str | None]:
    client = client or {}
    for name in KEY_PROPERTIES:
        value = _nonempty(props.get(name))
        if value is None and name in ("pipewire.access.portal.app_id", "application.process.binary"):
            value = _nonempty(client.get(name))
        if value is not None:
            if name == "application.process.binary":
                value = value.rsplit("/", 1)[-1]
            return value, name
    return None, None


def scoped_event_app(props: dict, client: dict | None = None) -> str | None:
    """The app ID an event sound carries when that app levels its own event
    sounds (SCOPED_EVENT_APPS), else None."""
    client = client or {}
    for value in (props.get("pipewire.access.portal.app_id"), client.get("pipewire.access.portal.app_id"),
                  props.get("application.id")):
        value = _nonempty(value)
        if value is not None:
            return value if value in SCOPED_EVENT_APPS else None
    return None


def is_system_sound(props: dict, client: dict | None = None) -> bool:
    role = _nonempty(props.get("media.role"))
    if role is None or role.lower() not in SYSTEM_ROLES:
        return False
    return scoped_event_app(props, client) is None


def _truthy(value) -> bool:
    return value is True or value in ("true", "1")


def is_app_playback(props: dict) -> bool:
    if props.get("media.class") != "Stream/Output/Audio":
        return False
    if _truthy(props.get("stream.monitor")):
        return False
    if props.get("luma.audio-levels") or props.get("luma.audio-capture.id"):
        return False
    # The playback half of a loopback or filter (WirePlumber's role sinks, an
    # equalizer): plumbing between an app and an output, not an app.
    if props.get("node.link-group"):
        return False
    return True


def is_portal_remote(client: dict) -> bool:
    if client.get("pipewire.access.portal.media_roles") is None:
        return False
    return client.get("pipewire.access.portal.is_portal") != "yes"


def capture_includes(capture: dict, key: str | None) -> bool:
    if not capture or not key:
        return False
    if capture.get("mode") == "apps":
        return key in set(capture.get("apps") or ())
    if capture.get("mode") == "system":
        requester = capture.get("requester") or ""
        if not capture.get("include_self") and requester and key == requester:
            return False
        return True
    return False


def cubic_to_linear(value: float) -> float:
    value = max(0.0, float(value))
    return value ** 3


def linear_to_cubic(value: float) -> float:
    value = float(value)
    return 0.0 if value <= 0 else value ** (1.0 / 3.0)


def stream_volume(channel_volumes) -> float | None:
    values = [float(v) for v in channel_volumes or () if isinstance(v, (int, float))]
    return max(values) if values else None


@dataclass(frozen=True)
class AppIdentity:
    key: str
    name: str
    icon: str
    desktop_id: str


# A web app a Chromium-family browser installed: <browser>-<32 letters a-p>-<profile>.desktop.
WEB_APP_ID = re.compile(r"(?:^|\.)[a-z][a-z0-9_-]*-[a-p]{32}-[^.]+(\.desktop)?$", re.IGNORECASE)

# The bell is Prairie's Lucide bell (prairie-icon-theme), as in Notifications.
SYSTEM_IDENTITY = AppIdentity(SYSTEM_KEY, "System sounds", "lumaui-bell-symbolic", "")
FALLBACK_ICON = "application-x-executable"


def _exec_basename(commandline: str | None) -> str | None:
    """The program a desktop entry runs: past env, flatpak run and paths."""
    if not commandline:
        return None
    words = commandline.split()
    while words and ("=" in words[0] or words[0] in ("env", "flatpak")):
        if words[0] == "flatpak":
            # flatpak run [options] <app id>
            rest = [w for w in words[1:] if not w.startswith("-") and w != "run"]
            return rest[0] if rest else None
        words = words[1:]
    return os.path.basename(words[0]) if words else None


class DesktopIndex:
    """Desktop entries by every name a sound stream may know an app by.

    Built lazily from Gio.AppInfo.get_all() and rebuilt when the installed
    apps change, so a lookup never scans the disk.
    """

    def __init__(self, gio=None) -> None:
        if gio is None:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio as gio
        self._gio = gio
        self._by_name: dict[str, object] | None = None
        self._monitor = None
        try:
            self._monitor = gio.AppInfoMonitor.get()
            self._monitor.connect("changed", lambda *_: self._invalidate())
        except Exception:  # an index without change tracking still works
            self._monitor = None

    def _invalidate(self) -> None:
        self._by_name = None

    def _index(self) -> dict[str, object]:
        if self._by_name is not None:
            return self._by_name
        index: dict[str, object] = {}

        def add(name, info) -> None:
            if name:
                index.setdefault(name.lower(), info)

        for info in self._gio.AppInfo.get_all():
            desktop_id = info.get_id() or ""
            stem = desktop_id[:-len(".desktop")] if desktop_id.endswith(".desktop") else desktop_id
            add(stem, info)
            add(stem.rsplit(".", 1)[-1], info)
            if hasattr(info, "get_startup_wm_class"):
                add(info.get_startup_wm_class(), info)
            add(_exec_basename(info.get_commandline()), info)
            executable = info.get_executable()
            add(os.path.basename(executable) if executable else None, info)
        self._by_name = index
        return index

    def lookup(self, *names) -> object | None:
        index = self._index()
        for name in names:
            if not name:
                continue
            info = index.get(str(name).lower())
            if info is not None:
                return info
        return None

    def is_browser(self, desktop_id: str) -> bool:
        """A web browser or one of its web apps: its player names a page, so
        the row's second line may be the site."""
        info = self.lookup(desktop_id[:-len(".desktop")] if desktop_id.endswith(".desktop") else desktop_id)
        if info is None:
            return False
        categories = (info.get_categories() or "") if hasattr(info, "get_categories") else ""
        if "WebBrowser" in categories.split(";"):
            return True
        return bool(WEB_APP_ID.search(desktop_id))

    def identify(self, key: str, props: dict, client: dict | None = None) -> AppIdentity:
        if key == SYSTEM_KEY:
            return SYSTEM_IDENTITY
        client = client or {}
        binary = _nonempty(props.get("application.process.binary")) or \
            _nonempty(client.get("application.process.binary"))
        info = self.lookup(
            props.get("pipewire.access.portal.app_id") or client.get("pipewire.access.portal.app_id"),
            props.get("application.id"),
            key,
            props.get("application.icon-name") or props.get("application.icon_name"),
            os.path.basename(binary) if binary else None,
            props.get("application.name"),
        )
        name = None
        icon = None
        desktop_id = ""
        if info is not None:
            name = info.get_display_name() or info.get_name()
            gicon = info.get_icon()
            icon = gicon.to_string() if gicon is not None else None
            desktop_id = info.get_id() or ""
        name = name or _nonempty(props.get("application.name")) or key
        icon = icon or _nonempty(props.get("application.icon-name")) or \
            _nonempty(props.get("application.icon_name")) or FALLBACK_ICON
        return AppIdentity(key, name, icon, desktop_id)
