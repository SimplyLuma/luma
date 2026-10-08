from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .errors import RelayError
from . import fex
from .wine_integration import relay_bridge_bindings, wine_runtime_root


def _runtime_socket(arguments: list[str], path: Path, runtime: Path) -> None:
    if path.exists():
        if path.parent != runtime:
            arguments.extend(("--dir", str(path.parent)))
        arguments.extend(("--bind", str(path), str(path)))


def _runtime_authority(arguments: list[str], runtime: Path) -> Path | None:
    value = os.environ.get("XAUTHORITY")
    if not value:
        return None
    authority = Path(value).absolute()
    try:
        authority.relative_to(runtime.absolute())
    except ValueError:
        return None
    if not authority.is_file():
        return None
    if authority.parent != runtime:
        arguments.extend(("--dir", str(authority.parent)))
    arguments.extend(("--ro-bind", str(authority), str(authority)))
    return authority


def sandbox_command(
    capsule: Path,
    command: list[str],
    *,
    source: Path | None = None,
    network: bool = True,
    grants: list[dict[str, Any]] | None = None,
    notification_socket: str | None = None,
    notification_socket_host: Path | None = None,
    notifications_enabled: bool = True,
) -> tuple[list[str], dict[str, str]]:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RelayError("Relay's application sandbox is not installed.")
    uid = os.getuid()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
    capsule = capsule.absolute()
    arguments = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
    ]
    if network:
        arguments.append("--share-net")
    arguments.extend(
        (
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/etc", "/etc",
            "--ro-bind", "/sys", "/sys",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--dir", "/home",
            "--dir", "/home/luma",
            "--dir", "/run",
            "--dir", "/run/user",
            "--dir", f"/run/user/{uid}",
            "--bind", str(capsule), "/relay",
        )
    )
    bridge_bindings = relay_bridge_bindings(capsule)
    wine_root = wine_runtime_root()
    if wine_root is not None:
        for architecture in ("x86_64-windows", "i386-windows"):
            override = Path("/usr/libexec/luma-relay/wine") / architecture / "explorer.exe"
            stock = wine_root / architecture / "explorer.exe"
            if override.is_file() and stock.is_file():
                arguments.extend(("--ro-bind", str(override), str(stock)))
    for bridge_source, destination in bridge_bindings:
        arguments.extend(("--ro-bind", str(bridge_source), str(destination)))
    if notification_socket_host is not None and notification_socket_host.exists():
        arguments.extend(
            (
                "--ro-bind", str(notification_socket_host.parent), "/relay-notify",
            )
        )
    if Path("/dev/dri").exists():
        arguments.extend(("--dir", "/dev/dri", "--dev-bind", "/dev/dri", "/dev/dri"))
    if Path("/dev/ntsync").exists():
        arguments.extend(("--dev-bind", "/dev/ntsync", "/dev/ntsync"))
    if Path("/run/udev").exists():
        arguments.extend(("--ro-bind", "/run/udev", "/run/udev"))
    if Path("/tmp/.X11-unix").exists():
        arguments.extend(("--ro-bind", "/tmp/.X11-unix", "/tmp/.X11-unix"))

    wayland = os.environ.get("WAYLAND_DISPLAY")
    if wayland:
        _runtime_socket(arguments, runtime / wayland, runtime)
    for relative in ("pipewire-0", "pipewire-0-manager", "pulse/native"):
        _runtime_socket(arguments, runtime / relative, runtime)
    xauthority = _runtime_authority(arguments, runtime)

    if source is not None:
        arguments.extend(("--dir", "/source"))
        arguments.extend(("--ro-bind", str(source.absolute()), "/source/input"))

    if grants:
        arguments.extend(("--dir", "/shared"))
    for index, grant in enumerate(grants or [], start=1):
        host_path = Path(str(grant.get("path", ""))).expanduser().absolute()
        if not host_path.exists():
            continue
        option = "--bind" if grant.get("mode") == "read-write" else "--ro-bind"
        arguments.extend(("--dir", f"/shared/{index}"))
        arguments.extend((option, str(host_path), f"/shared/{index}"))

    translated = fex.available()
    if translated:
        rootfs = fex.prepare_rootfs()
        arguments.extend(("--ro-bind", str(rootfs), fex.SANDBOX_ROOTFS))
        command = [str(fex.INTERPRETER), *command]
    arguments.extend(("--chdir", "/relay", "--"))
    arguments.extend(command)
    environment = {
        "HOME": "/home/luma",
        "USER": os.environ.get("USER", "luma"),
        "LOGNAME": os.environ.get("LOGNAME", os.environ.get("USER", "luma")),
        "PATH": "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "XDG_RUNTIME_DIR": str(runtime),
        "XDG_CACHE_HOME": "/relay/cache",
        "WINEPREFIX": "/relay/prefix",
        "WINEARCH": "win64",
        "WINEDEBUG": "-all",
        "WINEDLLOVERRIDES": "winemenubuilder.exe=d",
        "LUMA_RELAY_NOTIFICATIONS": "1" if notifications_enabled else "0",
    }
    if notification_socket:
        environment["LUMA_RELAY_NOTIFY_SOCKET"] = notification_socket
    if translated:
        environment.update(fex.environment())
    if xauthority is not None:
        environment["XAUTHORITY"] = str(xauthority)
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "PULSE_SERVER"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    return arguments, environment
