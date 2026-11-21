# SPDX-License-Identifier: Apache-2.0
"""Start an application from its tree with bubblewrap (ADR-052, Applications 4).

No container engine runs. The launcher puts together one namespace:

* the application's checkout as a read-only root, with the device's graphics
  drivers already laid into it (``device_graphics``);
* its private data folder as the home, at the person's real home path, with
  the person's own folders (Documents, Downloads, ...) at their real paths:
  the same policy as today's capsules, taken from ``launcher``;
* the session exactly as a capsule gets it (``session_boundary``): Wayland,
  the play-only sound socket, the filtered session and accessibility buses,
  the keyring through the broker, an X socket without the session's cookie;
  and the host's fonts, icon and cursor themes;
* the application's SELinux type with the category pair allocated for it
  (``capsule_identity``), its own systemd scope under ``app.slice``, the
  Landlock scope and the system-call filter (``sandbox_seccomp``).

User namespaces stay available inside (Chromium's sandbox needs them). The
process is its own PID namespace with bubblewrap as its reaper.

A second start of a running application gets a second namespace over the same
home and the same per-application /tmp, so the application's own
single-instance handoff finds its first window.

``launch_record`` is what ``luma-capsule-launch`` calls for an application
whose record names a tree in the store.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import shlex
import shutil
import signal
import socket
import subprocess
import fcntl
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import capsule_identity, capsule_display, launch_guard, session_boundary
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


def deployment(store: Store, app_id: str, graphics: list[str] | None = None, architecture: str = "",
               commit: str = "") -> tuple[Path, dict[str, object]]:
    """The read-only checkout the application runs from, and its record.

    ``commit`` names a tree other than the one in use: one waiting to be tried."""
    ref = store.app_ref(app_id, architecture)
    commit = commit or store.resolve(ref)
    if not commit:
        raise InstallerError(f"{app_id} is not in the application store.")
    record = store.read_record(commit)
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


def private_home(app_id: str, data: Path | None = None) -> Path:
    data = data or data_home(app_id)
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    for folder in (".config", ".cache", ".local/share", ".local/state"):
        (data / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
    return data


def sandbox_arguments(checkout: Path, record: dict[str, object], *, data: Path, instance: Path,
                      session: session_boundary.View, level: str = "", folders: list[Path] | None = None,
                      environment: dict[str, str] | None = None, network: bool = True) -> tuple[list[str], dict[str, str]]:
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
    if not network:
        arguments.append("--unshare-net")
    if not record.get("terminal"):
        arguments.append("--new-session")
    for relative in ("block", "bus", "class", "dev", "devices"):
        arguments.extend(("--ro-bind-try", f"/sys/{relative}", f"/sys/{relative}"))
    for node in device_graphics.device_nodes():
        arguments.extend(("--dev-bind", node, node))
    # The private home at the person's real home path; their own folders over it.
    arguments.extend(("--tmpfs", str(home.parent), "--bind", str(data), str(home)))
    if record.get("legacy_home") and home.name != "luma":
        # Settings written when a capsule's home was /home/luma name that path.
        arguments.extend(("--symlink", home.name, str(home.parent / "luma")))
    for folder in (capsule_launcher._person_folders() if folders is None else folders):
        arguments.extend(("--bind", str(folder), str(folder)))
    env = {"HOME": str(home), "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/games",
           "GTK_USE_PORTAL": "1", "BROWSER": "/usr/bin/xdg-open",
           # The person's dconf database stays outside, and the tree has no service to write one of
           # its own: GSettings keeps the application's settings in a file in its data folder.
           "GSETTINGS_BACKEND": "keyfile",
           "XDG_DATA_DIRS": "/usr/local/share:/usr/share:/run/host/share",
           "XCURSOR_PATH": f"{home}/.icons:/usr/share/icons:/run/host/share/icons",
           "LUMA_CAPSULE_DESKTOP_ID": capsule_launcher._desktop_id(record)}
    # The session: the same sockets and variables a capsule gets, and nothing else of the runtime folder.
    if any(mount.inside.parent == capsule_display.SOCKETS for mount in session.mounts):
        arguments.extend(("--dir", str(capsule_display.SOCKETS)))
    arguments.extend(session.bubblewrap())
    env.update(session.environment)
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
    # The application's arguments are passed as they are: no ${VARIABLE} expansion by systemd-run.
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--slice=app.slice", "--expand-environment=no",
            f"--unit=app-luma-{name}-{os.getpid()}.scope", "--"]


def guard_record(app_id: str, tree: dict[str, object], record: dict[str, object] | None = None) -> dict[str, object]:
    """One record for everything that asks about the application while it starts:
    what its tree exports, under what Valet recorded when it was installed."""
    exports = dict(tree.get("exports") or {})  # type: ignore[arg-type]
    described = {"application_id": app_id, "sha256": app_id, "name": exports.get("name") or app_id,
                 "icon": exports.get("icon") or "", "terminal": exports.get("terminal"), "chromium": exports.get("chromium"),
                 "desktop_path": exports.get("desktop_path") or "", "startup_wm_class": exports.get("startup_wm_class") or "",
                 "bus_names": exports.get("bus_names") or []}
    return {**described, **(record or {}), "format": tree.get("format"), "id": app_id,
            "bus_names": exports.get("bus_names") or (record or {}).get("bus_names") or []}


def application_level(record: dict[str, object]) -> str:
    """The application's category pair, from the one allocator; "" where the policy is not loaded."""
    if not (selinux_enabled() and Path("/usr/share/selinux/packages/luma_application.cil").exists()):
        return ""
    return capsule_identity.level(record)


def running_lock(instance: Path):
    """Held, shared, by every running launcher of the application; a refresh asks for it alone."""
    instance.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = open(instance / ".running", "a")
    fcntl.flock(handle, fcntl.LOCK_SH)
    return handle


def is_running(app_id: str) -> bool:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    try:
        with open(runtime / "luma-apps" / app_id / ".running", "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return False


def launch(store: Store, app_id: str, extra: list[str] | None = None, *, graphics: list[str] | None = None,
           command: list[str] | None = None, record: dict[str, object] | None = None, watched: bool = False) -> int:
    """Start the application. ``record`` is what Valet keeps for it: identity, keyring state, data folder."""
    checkout, tree = deployment(store, app_id, graphics)
    uid = os.getuid()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
    inside = Path(f"/run/user/{uid}")
    instance = runtime / "luma-apps" / app_id
    runtime_files(instance, checkout, app_id)
    described = guard_record(app_id, tree, record)
    data = private_home(app_id, Path(str(described["data_home"])) if described.get("data_home") else None)
    level = application_level(described)
    if level:
        label_data(data, level)
        label_data(instance, level)
    identity = hashlib.sha256(app_id.encode()).hexdigest()
    held = running_lock(instance)
    proxy, bus_socket = capsule_launcher._start_bus_proxy(runtime, identity, runtime_policy.own_bus_arguments(described),
                                                         described)
    display = capsule_launcher._start_display(runtime, identity)

    def stopped(_signal: int, _frame: object) -> None:
        # Being told to stop is an ordinary way for an application to end: the
        # bus proxy and its socket go with it instead of being left behind.
        raise SystemExit(143)

    previous = signal.signal(signal.SIGTERM, stopped) if threading.current_thread() is threading.main_thread() else None
    try:
        session = session_boundary.view(runtime, inside, dict(os.environ),
                                        accessibility=proxy.accessibility if proxy is not None else None,
                                        bus_socket=bus_socket, display=display)
        arguments, env = sandbox_arguments(checkout, described, data=data, instance=instance, session=session, level=level)
        front, filter_options = capsule_launcher._confinement(described)
        program = command if command is not None else payload(tree, session.wayland, list(extra or []))
        full = [*scope_prefix(app_id), *front, *arguments, *filter_options, "--", *program]
        if command is not None:
            return subprocess.run(full, env=env, check=False).returncode
        return capsule_launcher._guarded(described, full, env, watched=watched)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
        capsule_launcher._stop_bus_proxy(proxy, bus_socket)
        if display is not None:
            display.stop()
        held.close()


SMOKE_SECONDS = 10.0


def hidden_start(store: Store, app_id: str, record: dict[str, object] | None = None, *, commit: str = "",
                 seconds: float = SMOKE_SECONDS) -> tuple[bool, str]:
    """Start a tree where nobody sees it: (it works, why or why not).

    The sandbox the launcher builds, minus the person: a home that is thrown
    away, no network, no session bus, no sound, none of the person's folders,
    and a headless display of its own. It has to stay up for a few seconds
    without failing. A new tree is tried this way before it is ever used.
    """
    from . import capsule_refresh
    checkout, tree = deployment(store, app_id, commit=commit)
    exports = dict(tree.get("exports") or {})  # type: ignore[arg-type]
    console = bool(exports.get("terminal")) or not tree.get("windowed", True)
    described = guard_record(app_id, tree, record)
    uid = os.getuid()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
    relay = None
    with tempfile.TemporaryDirectory(prefix="luma-smoke-", dir=capsule_refresh._scratch_root()) as directory, \
            capsule_refresh.hidden_display(runtime, x11=not console) as display:
        if display is None and not console:
            return True, "no hidden display on this system; libraries checked only"
        home, instance = Path(directory) / "home", Path(directory) / "run"
        for folder in (".config", ".cache", ".local/share", ".local/state"):
            (home / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
        runtime_files(instance, checkout, app_id)
        level = application_level(described)
        if level:
            label_data(home, level)
            label_data(instance, level)
        environment = session_boundary.cleaned(dict(os.environ))
        if display is not None and not console:
            environment.update({"WAYLAND_DISPLAY": display[1], "XDG_SESSION_TYPE": "wayland"})
            # An application that still needs X finds the hidden compositor's server, through the
            # same kind of socket of its own a real start gives it.
            server = capsule_refresh.hidden_x11(runtime, display[1])
            if server is not None:
                try:
                    sockets = runtime / "luma-installer-bus"
                    sockets.mkdir(mode=0o700, parents=True, exist_ok=True)
                    relay = capsule_display.relay_for_session(sockets / f"smoke-{os.getpid()}.x11", runtime, *server)
                    if relay is not None:
                        relay.start()
                except OSError:
                    relay = None
        try:
            session = session_boundary.view(runtime, Path(f"/run/user/{uid}"), environment, sound=False, display=relay)
            arguments, env = sandbox_arguments(checkout, described, data=home, instance=instance, session=session, level=level,
                                               folders=[], environment=environment, network=False)
            front, filter_options = capsule_launcher._confinement(described)
            command = str(exports.get("command") or "")
            if console:
                # A console program needs a terminal to stay up; finding it proves the tree is whole.
                program = ["sh", "-c", 'command -v "$1" >/dev/null', "sh", (shlex.split(command) or ["sh"])[0]]
            else:
                program = payload(tree, session.wayland, [])
            started = time.monotonic()
            try:
                process = subprocess.Popen([*front, *arguments, *filter_options, "--", *program], env=env,
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except OSError as error:
                return False, str(error)
            try:
                _out, errors = process.communicate(timeout=120 if console else seconds)
            except subprocess.TimeoutExpired:
                # Still up after the wait: that is the pass.
                process.terminate()
                try:
                    process.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                return (True, f"stayed up for {seconds:g} s") if not console else (False, "did not answer")
            elapsed = time.monotonic() - started
            tail = [line for line in errors.decode("utf-8", "replace").splitlines() if line.strip()][-launch_guard.STDERR_LINES:]
            failure = launch_guard.classify(process.returncode, elapsed, tail)
            if failure is None and process.returncode == 0:
                return True, f"exited cleanly after {elapsed:.1f} s"
            return False, (tail[-1] if tail else f"exited with status {process.returncode} after {elapsed:.1f} s")[:300]

        finally:
            if relay is not None:
                relay.stop()

def launch_record(record: dict[str, object], extra: list[str] | None = None) -> int:
    """Start an installed application whose record names a tree in the store."""
    from . import store_refresh
    app_id = str(dict(record["store"])["id"])  # type: ignore[arg-type]
    watched = store_refresh.unconfirmed(record)
    status = launch(Store(), app_id, extra, record=record, watched=watched)
    if not watched:
        return status
    failure, capsule_launcher._watched_failure = capsule_launcher._watched_failure, None
    return store_refresh.after_watched_start(record, status, failure, extra)


def remove_runtime(app_id: str) -> None:
    """What a start leaves in the session's runtime folder."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    shutil.rmtree(runtime / "luma-apps" / app_id, ignore_errors=True)
    try:
        (runtime / "luma-apps").rmdir()
    except OSError:
        pass


def remove_data(app_id: str, data: Path | None = None) -> None:
    shutil.rmtree(data or data_home(app_id), ignore_errors=True)
    remove_runtime(app_id)
    try:
        data_home(app_id).parent.rmdir()
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
