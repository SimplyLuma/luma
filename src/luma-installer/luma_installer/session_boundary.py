# SPDX-License-Identifier: Apache-2.0
"""What an application sees of the session: one description for every launcher.

An application runs in a podman capsule, in a bubblewrap sandbox on the
host's /usr (an AppImage that was not moved to the store), or in a bubblewrap
sandbox on its own tree from the application store. The three are built by
different commands and must not differ in what they let through, so what is
let through is decided once, here, and each launcher only spells it in its
engine's options:

* Wayland: the compositor's socket.
* Sound: the session's socket for applications, on which a client can only
  play, and the PulseAudio socket. Never PipeWire's own socket.
* The session bus: a filtered proxy, with the keyring answered by a broker
  that knows only this application's entries (capsule_secrets).
* The accessibility bus: a filtered proxy.
* X: a socket of the application's own that the launcher answers; never the
  session's cookie.

Everything else in the session's runtime folder stays outside. The pieces that
run while the application does (proxy, broker, relays) are started by
`launcher._start_bus_proxy` and `launcher._start_display`; this module turns
them into mounts and environment.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import capsule_display

# Sound, as (the session's socket, where the application finds it).
#
# PipeWire's own socket lets a client do anything: record the microphone, read
# a screen being shared, rewire other applications' sound. An application gets
# the socket the session keeps for applications instead, on which it can only
# play (data/pipewire/60-luma-application-socket.conf); cameras and screens
# reach it through the portal. In a session that has no such socket yet it
# gets none, and sound still works through the PulseAudio socket, which every
# application made before PipeWire uses anyway.
SOUND_SOCKETS = (("pipewire-0-application", "pipewire-0"), ("pulse/native", "pulse/native"))

# Never passed into an application, whatever the session has set.
WITHHELD = ("XAUTHORITY", "DISPLAY", "AT_SPI_BUS_ADDRESS", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "PULSE_SERVER",
            "PIPEWIRE_REMOTE", "PIPEWIRE_RUNTIME_DIR")


@dataclass(frozen=True)
class Mount:
    source: Path
    inside: Path
    writable: bool = False


@dataclass
class View:
    """The session as one application gets it: sockets to mount and variables to set."""
    mounts: list[Mount] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    wayland: bool = False

    def podman(self) -> list[str]:
        arguments: list[str] = []
        for mount in self.mounts:
            arguments.extend(("--volume", f"{mount.source}:{mount.inside}:rw"))
        for name, value in self.environment.items():
            arguments.extend(("--env", f"{name}={value}"))
        return arguments

    def bubblewrap(self) -> list[str]:
        arguments: list[str] = []
        for mount in self.mounts:
            arguments.extend(("--bind" if mount.writable else "--ro-bind", str(mount.source), str(mount.inside)))
        return arguments


def view(runtime: Path, inside: Path, environment: dict[str, str], *, accessibility: Path | None = None,
         bus_socket: Path | None = None, display: capsule_display.DisplayRelay | None = None,
         sound: bool = True) -> View:
    """`runtime` is the session's runtime folder, `inside` the application's own."""
    found = View(environment={"XDG_RUNTIME_DIR": str(inside)})
    wayland = environment.get("WAYLAND_DISPLAY", "")
    if wayland and "/" not in wayland and (runtime / wayland).exists():
        found.mounts.append(Mount(runtime / wayland, inside / wayland))
        found.environment["WAYLAND_DISPLAY"] = wayland
        found.wayland = True
    if sound:
        for source, relative in SOUND_SOCKETS:
            if (runtime / source).exists():
                found.mounts.append(Mount(runtime / source, inside / relative))
        # A PulseAudio client refuses a runtime folder it does not own; name the socket, as Flatpak does.
        if (runtime / "pulse/native").exists():
            found.environment["PULSE_SERVER"] = f"unix:{inside / 'pulse/native'}"
    # The accessibility bus, filtered: a toolkit reads its address from the
    # session bus and finds it at this path. Without it every application
    # starts by reporting that the desktop has no accessibility.
    if accessibility is not None and accessibility.exists():
        found.mounts.append(Mount(accessibility, inside / "at-spi/bus"))
        found.environment["AT_SPI_BUS_ADDRESS"] = f"unix:path={inside / 'at-spi/bus'}"
    if bus_socket is not None:
        found.mounts.append(Mount(bus_socket, inside / bus_socket.name, writable=True))
        found.environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={inside / bus_socket.name}"
    # X, for an application that still needs it: a socket of its own that the
    # launcher answers, and never the session's cookie.
    if display is not None:
        found.mounts.append(Mount(display.path, capsule_display.SOCKETS / f"X{capsule_display.NUMBER}", writable=True))
        found.environment["DISPLAY"] = f":{capsule_display.NUMBER}"
    return found


def cleaned(environment: dict[str, str]) -> dict[str, str]:
    """The launcher's environment without what `view` decides."""
    return {name: value for name, value in environment.items() if name not in WITHHELD}


# -- The desktop's settings schemas ---------------------------------------------------
#
# An application is told which desktop it runs on (XDG_CURRENT_DESKTOP), because
# Chromium picks its keyring by it. One that believes it may then open that
# desktop's settings: Zoom reads org.gnome.settings-daemon.plugins.media-keys on
# GNOME and GLib ends the process when the schema is not installed. No
# distribution's tree carries the device's settings daemon, so its schemas are
# compiled from the device's own files and shown at SCHEMAS_INSIDE, where
# XDG_DATA_DIRS (.../run/host/share) makes GSettings look. Schemas only: every
# value is the default, the person's own settings stay outside.
#
# org.gnome.settings-daemon.plugins.xsettings is left out. GTK 3's Wayland
# backend and Chromium read font settings from it when it exists; current
# releases of the daemon no longer define those keys, and with the schema
# present and the keys absent GTK fails to open its display (Discord then dies
# on its first window: "Can't create a GtkStyleContext without a display
# connection"). Absent, both use their defaults or the settings portal.
SCHEMAS_INSIDE = Path("/run/host/share/glib-2.0/schemas")
SCHEMA_SOURCE = Path("/usr/share/glib-2.0/schemas")
SCHEMA_DATA_DIRS = "/usr/local/share:/usr/share:/run/host/share"


def _desktop_schema_files(source: Path) -> list[Path]:
    try:
        return sorted(path for path in source.glob("org.gnome.settings-daemon.*.xml") if "xsettings" not in path.name)
    except OSError:
        return []


def desktop_schemas(holder: Path | None = None, source: Path = SCHEMA_SOURCE) -> Path | None:
    """A folder with the device's settings-daemon schemas, compiled; None when there are none.

    Kept beside the application store, whose file type every application may
    read; made once and again when the device's schema files change.
    """
    files = _desktop_schema_files(source)
    compiler = shutil.which("glib-compile-schemas")
    if not files or not compiler:
        return None
    if holder is None:
        from .app_store import default_root
        holder = default_root() / "desktop-schemas"
    digest = hashlib.sha256()
    try:
        for path in files:
            status = path.stat()
            digest.update(f"{path.name}\0{status.st_size}\0{status.st_mtime_ns}\n".encode())
        final = holder / digest.hexdigest()[:24]
        if (final / "gschemas.compiled").is_file():
            return final
        holder.mkdir(mode=0o755, parents=True, exist_ok=True)
        partial = Path(tempfile.mkdtemp(prefix=".partial-", dir=holder))
        try:
            for path in files:
                shutil.copyfile(path, partial / path.name)
            result = subprocess.run([compiler, str(partial)], capture_output=True, check=False, timeout=60)
            if result.returncode != 0 or not (partial / "gschemas.compiled").is_file():
                return None
            partial.chmod(0o755)
            try:
                partial.rename(final)
            except OSError:
                if not (final / "gschemas.compiled").is_file():
                    return None
        finally:
            shutil.rmtree(partial, ignore_errors=True)
        for stale in holder.iterdir():
            if stale != final:
                shutil.rmtree(stale, ignore_errors=True)
        return final
    except (OSError, subprocess.SubprocessError):
        return None

