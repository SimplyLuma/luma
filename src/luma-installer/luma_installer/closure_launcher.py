# SPDX-License-Identifier: Apache-2.0
"""Start an application from its tree with bubblewrap (ADR-052, Applications 4): prototype.

No container engine runs. The launcher puts together one namespace:

* the application's checkout as a read-only root, with the device's graphics
  drivers already laid into it (``device_graphics``);
* its private data folder as the home, at the person's real home path, with
  the person's own folders (Documents, Downloads, ...) at their real paths:
  the same policy as today's capsules, taken from ``launcher``;
* the session: Wayland, PipeWire and PulseAudio sockets, the session bus
  through ``xdg-dbus-proxy`` (the same proxy today's launcher starts, so its
  rules apply here as they change), the host's fonts, icon and cursor themes;
* the application's SELinux type with its own category pair, and its own
  systemd scope under ``app.slice``.

User namespaces stay available inside (Chromium's sandbox needs them). The
process is its own PID namespace with bubblewrap as its reaper.

A second start of a running application gets a second namespace over the same
home and the same per-application /tmp, so the application's own
single-instance handoff finds its first window.

Nothing in the shipped install path uses this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from . import capsule_runtime as runtime_policy
from . import closure, device_graphics
from . import launcher as capsule_launcher
from .app_store import Store, selinux_enabled
from .errors import InstallerError

INTEGRATION = "6"
APPLICATION_TYPE = "luma_application.process"
DATA_TYPE = "container_file_t"
RUNTIME_LINKS = {"etc/resolv.conf": "/run/luma/resolv.conf", "etc/hosts": "/run/luma/hosts",
                 "etc/localtime": "/run/luma/localtime", "etc/machine-id": "/run/luma/machine-id",
                 "etc/passwd": "/run/luma/passwd", "etc/group": "/run/luma/group"}

HOST_FONTS = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <!-- The device's fonts, bound by the launcher. -->
  <dir>/run/host/fonts</dir>
  <dir>/run/host/local-fonts</dir>
  <dir>/run/host/user-fonts</dir>
</fontconfig>
"""


def data_home(app_id: str) -> Path:
    """The application's one data folder for this person."""
    return Path.home() / ".local/share/luma/apps-data" / app_id


# -- The checkout an application runs from -------------------------------------------

def _replace(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def integrate(root: Path, record: dict[str, object], layers: list[tuple[Path, dict[str, object]]]) -> dict[str, object]:
    """Finish a composed checkout before it is published.

    Only directory entries change: files from the store are replaced by new
    entries, never written through. The application's commit is untouched."""
    decisions = []
    extra_dirs: list[str] = []
    ldconfig = ""
    for layer, layer_record in layers:
        route = layer_record.get("route")
        if route == "mesa":
            decision = device_graphics.apply_mesa_layer(layer, layer_record, record, root)
        elif route == "host":
            decision = device_graphics.apply_host_layer(layer, layer_record, root)
        elif route == "nvidia":
            decision = device_graphics.apply_nvidia_layer(layer, layer_record, root)
        else:
            continue
        decisions.append(decision)
        extra_dirs += [entry for entry in decision.get("library_dirs") or [] if entry not in extra_dirs]
        ldconfig = str(decision.get("ldconfig") or ldconfig)
    for name in ("passwd", "group"):
        # The tree's own accounts, kept beside its record: the launcher adds the person to them.
        if (root / "etc" / name).is_file() and (root / "usr/share/luma").is_dir():
            (root / "usr/share/luma" / f"{name}.tree").write_text((root / "etc" / name).read_text())
    for relative, target in RUNTIME_LINKS.items():
        path = root / relative
        if not path.parent.is_dir():
            continue
        _replace(path)
        os.symlink(target, path)
    fonts = root / "etc/fonts/conf.d"
    if fonts.is_dir():
        _replace(fonts / "50-luma-host-fonts.conf")
        (fonts / "50-luma-host-fonts.conf").write_text(HOST_FONTS)
    for folder in ("home", "var/home", str(Path.home().resolve().parent).lstrip("/")):
        if folder:
            (root / folder).mkdir(parents=True, exist_ok=True)
    alias_home(root)
    opener = root / "usr/bin/xdg-open"
    if not opener.exists() and opener.parent.is_dir():
        opener.touch()
    if (root / "usr/share/zoneinfo").parent.is_dir():
        (root / "usr/share/zoneinfo").mkdir(exist_ok=True)
    if decisions:
        added = list(extra_dirs)
        configuration = root / "etc/ld.so.conf"
        existing = configuration.read_text() if configuration.is_file() else "include /etc/ld.so.conf.d/*.conf\n"
        _replace(configuration)
        configuration.write_text("".join(f"{entry}\n" for entry in added) + existing)
        _replace(root / "etc/ld.so.cache")
        command = [ldconfig] if ldconfig else (["/sbin/ldconfig.real"] if (root / "usr/sbin/ldconfig.real").exists()
                                               else ["/sbin/ldconfig"])
        closure._run(closure.sandbox_in_tree(root, [*command, "-X"], network=False), timeout=300)
        (root / "var/cache/ldconfig/aux-cache").unlink(missing_ok=True)
    description = {"graphics": decisions, "integration": INTEGRATION}
    (root / ".luma-deployment.json").write_text(json.dumps(description, indent=1) + "\n")
    return description


def alias_home(root: Path, host_home: Path = Path("/home")) -> None:
    """Where the device's /home is a link (to var/home), the tree's is the same link.

    File managers and portals hand over paths as the person's session spells
    them; both spellings have to reach the same file inside."""
    tree_home = root / "home"
    try:
        if host_home.is_symlink() and tree_home.is_dir() and not tree_home.is_symlink() and not any(tree_home.iterdir()):
            target = os.readlink(host_home)
            tree_home.rmdir()
            os.symlink(target, tree_home)
    except OSError:
        pass


def deployment(store: Store, app_id: str, graphics: list[str] | None = None, architecture: str = "") -> tuple[Path, dict[str, object]]:
    """The read-only checkout the application runs from, and its record."""
    ref = store.app_ref(app_id, architecture)
    commit = store.resolve(ref)
    if not commit:
        raise InstallerError(f"{app_id} is not in the application store.")
    record = store.read_record(ref)
    if graphics is None:
        graphics = device_graphics.routes_for(str(record["userland"]), store, architecture=architecture)
    layer_commits = [store.resolve(layer) for layer in graphics]
    if not all(layer_commits):
        raise InstallerError("A graphics layer named for this application is not in the store.")
    layers = [(store.compose([layer_commit]), store.read_record(layer_commit)) for layer_commit in layer_commits]
    salt = "|".join((INTEGRATION, *layer_commits, str(Path.home().resolve().parent)))
    checkout = store.compose([commit], salt, finish=lambda root: integrate(root, record, layers))
    return checkout, record


# -- The session ----------------------------------------------------------------------

def application_machine_id(app_id: str) -> str:
    """An identifier that is stable for this application on this device and
    tells it nothing about the device's own."""
    try:
        machine = Path("/etc/machine-id").read_text().strip()
    except OSError:
        machine = socket.gethostname()
    return hashlib.sha256(f"{machine}\0{app_id}".encode()).hexdigest()[:32]


def runtime_files(instance: Path, checkout: Path, app_id: str) -> None:
    """What /etc in the tree points at: written for each start."""
    instance.mkdir(mode=0o700, parents=True, exist_ok=True)
    (instance / "tmp").mkdir(mode=0o700, exist_ok=True)
    account = pwd.getpwuid(os.getuid())
    home = Path.home().resolve()
    passwd_lines = _tree_lines(checkout, "passwd", account.pw_name, str(account.pw_uid))
    passwd_lines.append(f"{account.pw_name}:x:{account.pw_uid}:{account.pw_gid}:{account.pw_gecos}:{home}:/bin/bash")
    group_lines = _tree_lines(checkout, "group", "", str(account.pw_gid))
    group_lines.append(f"{account.pw_name}:x:{account.pw_gid}:")
    files = {"passwd": "\n".join(passwd_lines) + "\n", "group": "\n".join(group_lines) + "\n",
             "machine-id": application_machine_id(app_id) + "\n"}
    for name, source in (("resolv.conf", "/etc/resolv.conf"), ("hosts", "/etc/hosts")):
        try:
            files[name] = Path(source).read_text()
        except OSError:
            files[name] = "127.0.0.1 localhost\n" if name == "hosts" else ""
    for name, text in files.items():
        partial = instance / f".{name}.partial"
        partial.write_text(text)
        partial.replace(instance / name)
    set_zone(instance, capsule_launcher.host_time_zone())


def _tree_lines(checkout: Path, name: str, account: str, number: str) -> list[str]:
    """The tree's own accounts, read from its commit (the checkout's file is a link to the runtime copy)."""
    source = checkout / "usr/share/luma" / f"{name}.tree"
    try:
        lines = source.read_text().splitlines()
    except OSError:
        lines = ["root:x:0:0:root:/root:/bin/bash"] if name == "passwd" else ["root:x:0:"]
    return [line for line in lines if line and line.split(":")[0] != account and (line.split(":") + ["", "", ""])[2] != number]


def set_zone(instance: Path, zone: str) -> None:
    """/etc/localtime in the tree is a link to this link, which names the zone."""
    link = instance / "localtime"
    partial = instance / ".localtime.partial"
    partial.unlink(missing_ok=True)
    os.symlink(f"/usr/share/zoneinfo/{zone or 'UTC'}", partial)
    partial.replace(link)


def x11_socket(display: str, folder: Path = Path("/tmp/.X11-unix")) -> Path | None:
    """The socket of a local X display (":0", ":1.0"), or None."""
    name = display.split(".", 1)[0]
    if not name.startswith(":") or not name[1:].isdigit():
        return None
    socket_path = folder / f"X{name[1:]}"
    return socket_path if socket_path.exists() else None


def exec_label(level: str) -> str:
    return f"system_u:system_r:{APPLICATION_TYPE}:{level}"


def file_label(level: str) -> str:
    return f"system_u:object_r:{DATA_TYPE}:{level}"


def label_data(data: Path, level: str) -> None:
    """The private home carries the application's categories; relabelled only when they differ."""
    wanted = file_label(level)
    try:
        current = os.getxattr(data, "security.selinux").rstrip(b"\0").decode()
    except OSError:
        current = ""
    if current != wanted:
        subprocess.run(["chcon", "-R", wanted, str(data)], capture_output=True, check=False)


def private_home(app_id: str) -> Path:
    data = data_home(app_id)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    for folder in (".config", ".cache", ".local/share", ".local/state"):
        (data / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
    return data


def sandbox_arguments(checkout: Path, record: dict[str, object], *, data: Path, instance: Path, runtime: Path,
                      bus_socket: Path | None, level: str = "", folders: list[Path] | None = None,
                      environment: dict[str, str] | None = None) -> tuple[list[str], dict[str, str]]:
    """The bubblewrap command (without the payload) and the environment inside."""
    host = dict(os.environ if environment is None else environment)
    uid = os.getuid()
    inside = Path(f"/run/user/{uid}")
    home = Path.home().resolve()
    arguments = ["bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
                 "--hostname", socket.gethostname(),
                 "--ro-bind", str(checkout), "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/sys",
                 "--tmpfs", "/run", "--dir", "/run/user", "--perms", "0700", "--dir", str(inside),
                 "--ro-bind", str(instance), "/run/luma", "--bind", str(instance / "tmp"), "/tmp",
                 "--tmpfs", "/var/tmp"]
    if not record.get("terminal"):
        arguments.append("--new-session")
    for relative in ("block", "bus", "class", "dev", "devices"):
        arguments.extend(("--ro-bind-try", f"/sys/{relative}", f"/sys/{relative}"))
    for node in device_graphics.device_nodes():
        arguments.extend(("--dev-bind", node, node))
    # The private home at the person's real home path; their own folders over it.
    arguments.extend(("--tmpfs", str(home.parent), "--bind", str(data), str(home)))
    for folder in (capsule_launcher._person_folders() if folders is None else folders):
        arguments.extend(("--bind", str(folder), str(folder)))
    env = {"HOME": str(home), "XDG_RUNTIME_DIR": str(inside), "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/games",
           "GTK_USE_PORTAL": "1", "BROWSER": "/usr/bin/xdg-open",
           "XDG_DATA_DIRS": "/usr/local/share:/usr/share:/run/host/share",
           "XCURSOR_PATH": f"{home}/.icons:/usr/share/icons:/run/host/share/icons",
           "LUMA_CAPSULE_DESKTOP_ID": capsule_launcher._desktop_id(record)}
    wayland = host.get("WAYLAND_DISPLAY", "")
    if wayland and (runtime / wayland).exists():
        arguments.extend(("--ro-bind", str(runtime / wayland), str(inside / wayland)))
        env["WAYLAND_DISPLAY"] = wayland
    else:
        wayland = ""
    for relative in ("pipewire-0", "pulse/native", "at-spi/bus"):
        if (runtime / relative).exists():
            arguments.extend(("--ro-bind", str(runtime / relative), str(inside / relative)))
    if (runtime / "pulse/native").exists():
        env["PULSE_SERVER"] = f"unix:{inside / 'pulse/native'}"
    if bus_socket is not None:
        arguments.extend(("--bind", str(bus_socket), str(inside / "bus")))
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={inside / 'bus'}"
    display_socket = x11_socket(host.get("DISPLAY", ""))
    if display_socket is not None:
        # The display's own socket, not the folder of every display's.
        arguments.extend(("--ro-bind", str(display_socket), str(display_socket)))
        env["DISPLAY"] = host["DISPLAY"]
        authority = capsule_launcher._runtime_authority(runtime)
        if authority is not None:
            arguments.extend(("--ro-bind", str(authority), str(inside / authority.name)))
            env["XAUTHORITY"] = str(inside / authority.name)
    # Fonts and themes are the device's; the tree's own stay visible beside them.
    for source, destination in (("/usr/share/fonts", "/run/host/fonts"), ("/usr/local/share/fonts", "/run/host/local-fonts"),
                                (str(Path.home() / ".local/share/fonts"), "/run/host/user-fonts"),
                                ("/usr/share/icons", "/run/host/share/icons"), ("/usr/share/themes", "/run/host/share/themes")):
        if Path(source).is_dir():
            arguments.extend(("--ro-bind", source, destination))
    if capsule_launcher.ZONEINFO.is_dir() and (checkout / "usr/share/zoneinfo").is_dir():
        arguments.extend(("--ro-bind", str(capsule_launcher.ZONEINFO), "/usr/share/zoneinfo"))
    if Path("/usr/libexec/luma-capsule-open").is_file() and (checkout / "usr/bin/xdg-open").exists():
        arguments.extend(("--ro-bind", "/usr/libexec/luma-capsule-open", "/usr/bin/xdg-open"))
    for name in ("LANG", "LC_ALL", "TZ", "TERM", "DESKTOP_STARTUP_ID", "XDG_ACTIVATION_TOKEN", "GTK_IM_MODULE",
                 "QT_IM_MODULE", "XMODIFIERS", "WAYLAND_DEBUG", *runtime_policy.DESKTOP_IDENTITY):
        if host.get(name):
            env[name] = host[name]
    try:
        account = pwd.getpwuid(uid).pw_name
    except KeyError:
        account = host.get("USER", "")
    env.update(dict(runtime_policy.login_environment(account)))
    if record.get("format") == "appimage":
        app_dir = f"/opt/{record['id']}"
        env.update({"APPIMAGE": f"{app_dir}/AppRun", "APPDIR": app_dir, "ARGV0": f"{app_dir}/AppRun", "OWD": str(home)})
    if level:
        arguments.extend(("--exec-label", exec_label(level)))
    arguments.extend(("--chdir", str(home)))
    if level:
        # The namespace's own temporary filesystems belong to the application too.
        # bubblewrap applies this to mounts made after the option, so it leads.
        arguments[1:1] = ["--file-label", file_label(level)]
    return arguments, env


def payload(record: dict[str, object], wayland: bool, extra: list[str]) -> list[str]:
    exports = dict(record.get("exports") or {})  # type: ignore[arg-type]
    command = str(exports.get("command") or "")
    if not command:
        raise InstallerError("The application's package did not declare a launcher.")
    flags = runtime_policy.payload_arguments({"chromium": exports.get("chromium"), "application_id": record.get("id")}, wayland)
    if flags:
        # Text input (the on-screen keyboard and input methods) for Chromium on Wayland.
        flags = [*flags, "--enable-wayland-ime"]
    return [*shlex.split(command), *flags, *extra]


def scope_prefix(app_id: str) -> list[str]:
    """Run in the application's own systemd scope, named the way the desktop expects."""
    if not shutil.which("systemd-run") or not os.environ.get("XDG_RUNTIME_DIR"):
        return []
    name = "".join(character if character.isalnum() else "_" for character in app_id)
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--slice=app.slice",
            f"--unit=app-luma-{name}-{os.getpid()}.scope", "--"]


def launch(store: Store, app_id: str, extra: list[str] | None = None, *, graphics: list[str] | None = None,
           command: list[str] | None = None) -> int:
    checkout, record = deployment(store, app_id, graphics)
    uid = os.getuid()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
    instance = runtime / "luma-apps" / app_id
    runtime_files(instance, checkout, app_id)
    data = private_home(app_id)
    level = ""
    if selinux_enabled() and Path("/usr/share/selinux/packages/luma_application.cil").exists():
        level = store.categories(app_id)
        label_data(data, level)
        label_data(instance, level)
    exports = dict(record.get("exports") or {})  # type: ignore[arg-type]
    guard_record = {"application_id": app_id, "name": exports.get("name") or app_id, "icon": exports.get("icon") or "",
                    "terminal": exports.get("terminal"), "chromium": exports.get("chromium"), "sha256": app_id,
                    "desktop_path": exports.get("desktop_path") or "", "startup_wm_class": exports.get("startup_wm_class") or "",
                    "format": record.get("format"), "id": app_id}
    proxy, bus_socket = capsule_launcher._start_bus_proxy(
        runtime, hashlib.sha256(app_id.encode()).hexdigest(), runtime_policy.own_bus_arguments(guard_record))
    try:
        arguments, env = sandbox_arguments(checkout, guard_record, data=data, instance=instance, runtime=runtime,
                                           bus_socket=bus_socket, level=level)
        program = command if command is not None else payload(record, "WAYLAND_DISPLAY" in env, list(extra or []))
        full = [*scope_prefix(app_id), *arguments, "--", *program]
        if command is not None:
            return subprocess.run(full, env=env, check=False).returncode
        return capsule_launcher._guarded(guard_record, full, env)
    finally:
        capsule_launcher._stop_bus_proxy(proxy, bus_socket)


def remove_data(app_id: str) -> None:
    shutil.rmtree(data_home(app_id), ignore_errors=True)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    shutil.rmtree(runtime / "luma-apps" / app_id, ignore_errors=True)
    for folder in (data_home(app_id).parent, runtime / "luma-apps"):
        try:
            folder.rmdir()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        print("usage: closure-launch APPLICATION_ID [ARGUMENT ...]", file=sys.stderr)
        return 2
    try:
        return launch(Store(), values[0], values[1:])
    except InstallerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
