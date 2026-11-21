from __future__ import annotations

import ctypes
import os
import re
import shlex
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from . import capsule_display, capsule_identity, capsule_refresh, capsule_scope, capsule_secrets
from . import sandbox_seccomp, session_boundary
from . import capsule_runtime as runtime_policy
from . import launch_guard
from .desktop import read_record, safe_id
from .errors import InstallerError


def _socket_mount(arguments: list[str], source: Path, destination: Path) -> None:
    if source.exists():
        arguments.extend(("--volume", f"{source}:{destination}:rw"))


class _BusBoundary:
    """What stands between an application and the session bus while it runs."""

    def __init__(self, proxy: subprocess.Popen[bytes], sockets: list[Path],
                 relay: capsule_secrets.Relay | None = None, broker: capsule_secrets.Broker | None = None,
                 accessibility: Path | None = None):
        self.proxy, self.sockets, self.relay, self.broker = proxy, sockets, relay, broker
        # The application's filtered view of the accessibility bus, if there is one.
        self.accessibility = accessibility

    def stop(self) -> None:
        if self.relay is not None:
            self.relay.stop()
        if self.broker is not None:
            self.broker.stop()
        self.proxy.terminate()
        try:
            self.proxy.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proxy.kill()
        for path in self.sockets:
            path.unlink(missing_ok=True)


# What an application gets of the session is decided in session_boundary.py,
# for capsules and for applications from the store alike.
SOUND_SOCKETS = session_boundary.SOUND_SOCKETS

# What an application may ask of the accessibility bus: to put its own window
# tree under the desktop's and to learn which events and keys assistive tools
# listen for. These are the calls Flatpak lets through (flatpak-run-dbus.c).
# Every other call it starts is refused, so it cannot read or drive another
# application's interface; a screen reader's calls into it still arrive and
# are answered.
ACCESSIBILITY_CALLS = (
    "org.a11y.atspi.Socket.Embed@/org/a11y/atspi/accessible/root",
    "org.a11y.atspi.Socket.Unembed@/org/a11y/atspi/accessible/root",
    "org.a11y.atspi.Registry.GetRegisteredEvents@/org/a11y/atspi/registry",
    "org.a11y.atspi.DeviceEventController.GetKeystrokeListeners@/org/a11y/atspi/registry/deviceeventcontroller",
    "org.a11y.atspi.DeviceEventController.GetDeviceEventListeners@/org/a11y/atspi/registry/deviceeventcontroller",
    "org.a11y.atspi.DeviceEventController.NotifyListenersSync@/org/a11y/atspi/registry/deviceeventcontroller",
    "org.a11y.atspi.DeviceEventController.NotifyListenersAsync@/org/a11y/atspi/registry/deviceeventcontroller",
)


def _accessibility_arguments(runtime: Path, socket: Path) -> list[str]:
    """A second filtered bus in the same proxy, for the accessibility bus."""
    bus = runtime / "at-spi/bus"
    if not bus.exists():
        return []
    return [f"unix:path={bus}", str(socket), "--filter", "--sloppy-names",
            *(f"--call=org.a11y.atspi.Registry={call}" for call in ACCESSIBILITY_CALLS)]


_BUS_SOCKET = re.compile(r"-(\d+)\.(?:bus|app|a11y|x11)$")


def _sweep_bus_sockets(root: Path) -> None:
    """Remove the sockets of launchers that were stopped before they could."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        match = _BUS_SOCKET.search(entry.name)
        if match and not Path(f"/proc/{match.group(1)}").exists():
            entry.unlink(missing_ok=True)


def _start_bus_proxy(runtime: Path, identity: str, own: list[str] | None = None,
                     record: dict[str, object] | None = None) -> tuple[_BusBoundary | None, Path | None]:
    """The application's session bus: filtered, with its own keyring entries behind the keyring's name."""
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not address:
        return None, None
    root = runtime / "luma-installer-bus"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _sweep_bus_sockets(root)
    socket = root / f"{identity[:24]}-{os.getpid()}.bus"
    socket.unlink(missing_ok=True)
    accessibility_socket = root / f"{identity[:24]}-{os.getpid()}.a11y"
    accessibility_socket.unlink(missing_ok=True)
    accessibility = _accessibility_arguments(runtime, accessibility_socket)
    # The Secret Service itself is never let through: it would hand over every
    # saved password. The application reaches a broker for its own entries,
    # which answers to a name of its own on the session bus (capsule_secrets).
    secrets_name, adopt = "", False
    if record is not None:
        record, adopt = capsule_identity.keyring_adoption(record)
        pair = capsule_identity.parse_level(capsule_identity.level(record))
        secrets_name = capsule_secrets.bus_name(pair) if pair else ""
    try:
        process = subprocess.Popen([
            "xdg-dbus-proxy", address, str(socket), "--filter",
            "--talk=org.freedesktop.portal.Desktop",
            "--talk=org.freedesktop.Notifications",
            # Where the accessibility bus is and whether it is in use; the
            # launcher of that bus offers nothing else an application needs.
            "--call=org.a11y.Bus=org.a11y.Bus.GetAddress@/org/a11y/bus",
            "--call=org.a11y.Bus=org.freedesktop.DBus.Properties.Get@/org/a11y/bus",
            *([f"--talk={secrets_name}"] if secrets_name else []),
            "--own=org.mpris.MediaPlayer2.*",
            *(own or []),
            *accessibility,
        ], close_fds=True)
    except OSError as error:
        raise InstallerError(f"The application portal boundary could not start: {error}") from error
    for _attempt in range(100):
        if socket.exists() and (not accessibility or accessibility_socket.exists()):
            break
        if process.poll() is not None:
            raise InstallerError("The application portal boundary exited before launch.")
        time.sleep(0.01)
    else:
        process.terminate()
        raise InstallerError("The application portal boundary did not become ready.")
    boundary = _BusBoundary(process, [socket])
    if accessibility:
        boundary.accessibility = accessibility_socket
        boundary.sockets.append(accessibility_socket)
    if not secrets_name or record is None:
        return boundary, socket
    try:
        boundary.broker = capsule_secrets.Broker(secrets_name, capsule_identity.keyring_tag(record), adopt, address)
        if not boundary.broker.start():
            # No keyring to stand in front of; the application finds none.
            boundary.broker = None
        relay = capsule_secrets.Relay(root / f"{identity[:24]}-{os.getpid()}.app", socket,
                                      capsule_secrets.SERVICE, secrets_name)
        relay.start()
    except (OSError, ValueError) as error:
        boundary.stop()
        raise InstallerError(f"The application portal boundary could not start: {error}") from error
    boundary.relay = relay
    boundary.sockets.append(relay.path)
    return boundary, relay.path


def _stop_bus_proxy(boundary: _BusBoundary | None, socket: Path | None) -> None:
    if boundary is not None:
        boundary.stop()
    if socket is not None:
        socket.unlink(missing_ok=True)


HOST_ETC = Path("/etc")
ZONEINFO = Path("/usr/share/zoneinfo")
_ZONE_NAME = re.compile(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*")
# inotify(7): a link replaced by rename, or written, created, removed.
_ZONE_EVENTS = 0x00000008 | 0x00000080 | 0x00000100 | 0x00000200


def host_time_zone(etc: Path | None = None, zoneinfo: Path | None = None) -> str:
    """The host's zone name ("America/Chicago"), or "" when it has none.

    timedatectl keeps /etc/localtime as a link into the zone database; the
    link's target is the only place the name is written down.
    """
    try:
        target = os.readlink((etc or HOST_ETC) / "localtime")
    except OSError:
        return ""
    _head, marker, name = target.rpartition("zoneinfo/")
    if not marker or not _ZONE_NAME.fullmatch(name) or not ((zoneinfo or ZONEINFO) / name).is_file():
        return ""
    return name


def _time_zone_arguments(zone: str) -> list[str]:
    """The capsule's /etc/localtime: a link to the host's zone, by name.

    The image's own link names Etc/UTC, so every capsule ran on UTC. glibc
    reads the zone from the file; ICU (Chromium, Electron, V8's Intl) reads
    the name from the link's target, so a copied zone file would give the
    right offset under the wrong name. Podman writes the link when the image
    carries the zone; the host's database is mounted over the image's so the
    rules are the host's, not those of the day the capsule was built. Without
    a name Podman copies the host's file, which still gives the right offset.
    """
    arguments = [f"--tz={zone or 'local'}"]
    if ZONEINFO.is_dir():
        arguments.extend(("--volume", f"{ZONEINFO}:{ZONEINFO}:ro"))
    return arguments


def _host_zone_changes(etc: Path = HOST_ETC) -> Iterator[None]:
    """Yield each time the host's /etc/localtime changes; blocks in between."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        descriptor = libc.inotify_init1(os.O_CLOEXEC)
    except (OSError, AttributeError):
        return
    if descriptor < 0:
        return
    try:
        if libc.inotify_add_watch(descriptor, os.fsencode(etc), _ZONE_EVENTS) < 0:
            return
        while True:
            data = os.read(descriptor, 4096)
            offset, changed = 0, False
            while offset + 16 <= len(data):
                length = struct.unpack_from("iIII", data, offset)[3]
                changed |= data[offset + 16:offset + 16 + length].rstrip(b"\0") == b"localtime"
                offset += 16 + length
            if changed:
                yield
    finally:
        os.close(descriptor)


def _set_capsule_zone(instance: str, zone: str) -> bool:
    result = subprocess.run(["podman", "exec", "--user", "0", instance,
                             "ln", "-sfn", f"../usr/share/zoneinfo/{zone}", "/etc/localtime"],
                            capture_output=True, check=False, timeout=30)
    return result.returncode == 0


def _keep_zone(instance: str, zone: str, etc: Path | None = None,
               changes: Callable[[Path], Iterator[None]] = _host_zone_changes,
               apply: Callable[[str, str], bool] = _set_capsule_zone) -> None:
    etc = etc or HOST_ETC
    for _change in changes(etc):
        now = host_time_zone(etc)
        if now and now != zone and apply(instance, now):
            zone = now


def _follow_time_zone(instance: str, zone: str) -> None:
    """Move the running capsule to the host's zone whenever the host moves.

    A laptop that changes zone replaces the host's /etc/localtime link; the
    capsule's link is replaced to match, and the application follows as host
    applications do: glibc rereads /etc/localtime, Chromium watches it and
    tells its renderers. TZ stays unset in the capsule for the same reason:
    with TZ set, neither looks at the file again.
    """
    threading.Thread(target=_keep_zone, args=(instance, zone), daemon=True,
                     name="luma-capsule-zone").start()


def _desktop_id(record: dict[str, object]) -> str:
    # The Shell reads this from the window's process to put the window under
    # the launcher Luma wrote, whatever window class the application chose.
    return f"org.projectluma.Installed.{safe_id(str(record.get('application_id', '')))}.desktop"


def _running_capsule(name: str) -> bool:
    try:
        state = subprocess.run(["podman", "container", "inspect", "--format", "{{.State.Running}}", name],
                               capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return False
    if state.returncode != 0:
        return False
    if state.stdout.strip() == "true":
        return True
    subprocess.run(["podman", "rm", "-f", "--time=0", name], capture_output=True, check=False)
    return False


def _launch_deb(record: dict[str, object], extra: list[str] | None = None) -> int:
    # One running capsule per application. A second launch — a link the
    # browser hands back after sign-in, a file opened from Filer, a click on
    # the dock while it is open — joins the instance that is already running,
    # in its namespaces, so the application's own single-instance handoff sees
    # its first window. A second capsule would find the first one's lock in a
    # different /tmp and PID namespace, treat it as stale, and start apart
    # from the window the person is looking at.
    instance = "luma-run-" + str(record.get("application_id") or record["sha256"])[:60]
    if _running_capsule(instance):
        wayland_now = bool(os.environ.get("WAYLAND_DISPLAY"))
        primary = runtime_policy.primary_argument_count(instance) if record.get("chromium") else 0
        return _guarded(record, ["podman", "exec", instance, *shlex.split(str(record["command"])),
                                 *runtime_policy.handoff_arguments(record, wayland_now, primary, list(extra or []))])
    uid = os.getuid()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
    capsule_runtime = Path(f"/run/user/{uid}")
    data = _private_home(record)
    proxy, bus_socket = _start_bus_proxy(runtime, str(record["sha256"]), runtime_policy.own_bus_arguments(record),
                                         record)
    zone = host_time_zone()
    arguments = ["podman", "run", "--rm", "--name", instance, "--network=host", "--userns=keep-id",
                 *_runtime_arguments(record), *runtime_policy.launch_arguments(record),
                 *_time_zone_arguments(zone),
                 "--security-opt", "label=type:luma_application.process",
                 # Its own category pair, allocated once and kept in its record:
                 # Podman otherwise picks new categories each run and relabels
                 # the whole private home to match on every launch.
                 "--security-opt", f"label=level:{capsule_identity.level(record)}",
                 # A fresh proc can only be mounted where proc isn't partly hidden;
                 # without this, nested sandboxes (bubblewrap) cannot start.
                 "--security-opt", "unmask=/proc/*",
                 "--mount", f"type=tmpfs,destination={capsule_runtime},tmpfs-mode=0700,chown=true",
                 "--env", f"XDG_RUNTIME_DIR={capsule_runtime}",
                 "--volume", "/usr/libexec/luma-capsule-open:/usr/bin/xdg-open:ro",
                 "--env", "BROWSER=/usr/bin/xdg-open",
                 "--env", "GTK_USE_PORTAL=1",
                 "--env", f"LUMA_CAPSULE_DESKTOP_ID={_desktop_id(record)}"]
    schemas = session_boundary.desktop_schemas()
    if schemas is not None:
        # The desktop's settings schemas, for an application that asks for them (session_boundary.py).
        arguments.extend(("--volume", f"{schemas}:{session_boundary.SCHEMAS_INSIDE}:ro",
                          "--env", f"XDG_DATA_DIRS={session_boundary.SCHEMA_DATA_DIRS}"))
    # A console payload needs a terminal on both sides of the capsule boundary:
    # without one it reads EOF immediately and exits before drawing anything.
    if record.get("terminal") and sys.stdin.isatty() and sys.stdout.isatty():
        arguments.extend(("--interactive", "--tty"))
        if os.environ.get("TERM"):
            arguments.extend(("--env", f"TERM={os.environ['TERM']}"))
    # Wayland, sound, the filtered buses and the application's own X socket:
    # the same for every way an application runs (session_boundary.py).
    wayland = os.environ.get("WAYLAND_DISPLAY")
    display = _start_display(runtime, str(record["sha256"]))
    session = session_boundary.view(runtime, capsule_runtime, dict(os.environ),
                                    accessibility=proxy.accessibility if proxy is not None else None,
                                    bus_socket=bus_socket, display=display)
    arguments.extend(session.podman())
    if Path("/dev/dri").exists():
        arguments.extend(("--device", "/dev/dri"))
    # The private home appears at the person's real home path, so paths mean the
    # same thing on both sides of a file chooser, and at /home/luma for settings
    # written before. Their own folders are shared over it at the same paths.
    home = Path.home().resolve()
    arguments.extend(("--volume", f"{data}:{home}:rw,Z", "--volume", f"{data}:/home/luma:rw",
                      "--env", f"HOME={home}", "--workdir", str(home)))
    for folder in _person_folders():
        arguments.extend(("--volume", f"{folder}:{folder}:rw"))
    # The desktop's identity tells toolkits which keyring, theme and portal
    # conventions apply; Chromium without it stores sign-ins in plain text and
    # forgets them, and GTK and Qt pick fallback themes.
    # TZ only when the person set one; the host's applications use it too.
    for name in ("LANG", "TZ", "DESKTOP_STARTUP_ID", "XDG_ACTIVATION_TOKEN", *runtime_policy.DESKTOP_IDENTITY):
        if os.environ.get(name):
            arguments.extend(("--env", f"{name}={os.environ[name]}"))
    import pwd
    try:
        account = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        account = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    for name, value in runtime_policy.login_environment(account):
        arguments.extend(("--env", f"{name}={value}"))
    if record.get("format") == "portable":
        arguments.extend(("--volume", f"{record['root']}:/app:ro,Z", "--workdir", "/app"))
    arguments.append(str(record["image"]))
    arguments.extend(shlex.split(str(record["command"])))
    arguments.extend(runtime_policy.payload_arguments(record, bool(wayland)))
    arguments.extend(extra or [])
    if zone and not os.environ.get("TZ"):
        _follow_time_zone(instance, zone)
    watched = capsule_refresh.unconfirmed(record)
    try:
        status = _guarded(record, arguments, watched=True) if watched else _guarded(record, arguments)
    finally:
        _stop_bus_proxy(proxy, bus_socket)
        if display is not None:
            display.stop()
    if watched:
        return _after_watched_start(record, status, extra)
    return status


def _after_watched_start(record: dict[str, object], status: int, extra: list[str] | None) -> int:
    """The first start on a refreshed capsule has ended: keep it, or go back.

    If the application failed while opening, the record returns to the capsule
    that worked and the application is started again from it, so the person
    sees their application a moment later rather than a failure they did not
    cause. That update is not taken again.
    """
    global _watched_failure
    failure, _watched_failure = _watched_failure, None
    if failure is None:
        try:
            capsule_refresh.confirm(record)
        except InstallerError:
            pass
        return status
    name = _display_name(record)
    try:
        previous = capsule_refresh.rollback(
            str(record["application_id"]), f"The updated capsule failed on its first start: {launch_guard.summary(name, failure)}")
    except (InstallerError, OSError, KeyError):
        launch_guard.report(name, str(record.get("application_id") or ""), str(record.get("icon") or ""), failure)
        return status
    return _launch_deb(previous, extra)


# crun, with the application scoped as its container is created; the name the
# engine knows the runtime by is the file's (capsule_scope.runtime_main).
CAPSULE_RUNTIME = Path("/usr/libexec/luma-capsule-runtime/crun")


def _scoped(record: dict[str, object]) -> bool:
    """Whether this application's abstract sockets and signals are scoped.

    The host's network namespace is shared, and with it every abstract Unix
    socket of the session; Landlock keeps the application to the ones it made
    itself, and to signalling its own processes (capsule_scope.py). A record
    can opt out with "scope": false, for an application that turns out to
    depend on a set-user-ID helper inside its capsule.
    """
    return record.get("scope") is not False and capsule_scope.supported()


def _scope(record: dict[str, object]) -> list[str]:
    """In front of bubblewrap: the scope, inherited by everything it starts."""
    return capsule_scope.prefix() if _scoped(record) else []


def _confinement(record: dict[str, object]) -> tuple[list[str], list[str]]:
    """For an application under bubblewrap: what goes in front of it, and its own options.

    The Landlock scope and the system-call filter (sandbox_seccomp.py). A
    record can opt out of the filter with "seccomp": false, for an application
    that turns out to need a call or a socket family it refuses.
    """
    scoped = _scoped(record)
    filtered = record.get("seccomp") is not False and bool(sandbox_seccomp.program())
    front = capsule_scope.prefix(abstract=scoped, signal=scoped, seccomp=filtered)
    return front, (capsule_scope.seccomp_arguments() if filtered and front else [])


def _runtime_arguments(record: dict[str, object]) -> list[str]:
    """For Podman: the runtime that scopes the application's container.

    Podman has to set up its user namespace before anything is scoped, so the
    scope is applied where the container's first process is made. A second
    start that joins a running capsule is made by the engine from outside and
    is not inside that scope.
    """
    if _scoped(record) and CAPSULE_RUNTIME.is_file():
        return ["--runtime", str(CAPSULE_RUNTIME)]
    return []


def _start_display(runtime: Path, identity: str) -> capsule_display.DisplayRelay | None:
    """The application's X socket (capsule_display.py), or None without X."""
    root = runtime / "luma-installer-bus"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        relay = capsule_display.relay_for_session(root / f"{identity[:24]}-{os.getpid()}.x11", runtime)
        if relay is not None:
            relay.start()
        return relay
    except OSError:
        return None


def _private_home(record: dict[str, object]) -> Path:
    """The application's own home, laid out like a person's home.

    A fresh home had no ~/.config: an application that creates only its own
    folder inside it (OrcaSlicer: boost::filesystem::create_directory) failed
    before its first window, though it runs in any real home.
    """
    data = Path.home() / ".local/share/luma/installer/data" / str(record["sha256"])
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    for folder in (".config", ".cache", ".local/share", ".local/state"):
        (data / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
    return data


def _display_name(record: dict[str, object], fallback: str = "") -> str:
    return str(record.get("name") or record.get("application_id") or fallback or "An application")


# Set by a watched start that failed while opening; read by whoever asked for
# the watch. A launcher process starts one application, once.
_watched_failure: launch_guard.Failure | None = None


def _guarded(record: dict[str, object], arguments: list[str], env: dict[str, str] | None = None,
             watched: bool = False) -> int:
    """Run the application; if it fails while opening, stop the spinner and say so.

    A watched start is the first on a refreshed capsule. Its failure is not
    reported and Luma's own switches are not blamed for it: the caller goes
    back to the capsule that worked and starts the application again.
    """
    global _watched_failure
    capture = launch_guard.should_capture() and not record.get("terminal")
    status, seconds, stderr = launch_guard.run(arguments, env=env, capture=capture)
    failure = launch_guard.classify(status, seconds, stderr)
    if failure is None:
        return status
    if watched:
        _watched_failure = failure
        return status

    application_id = str(record.get("application_id") or "")
    name = _display_name(record)

    # Luma may start an application with switches its publisher did not choose.
    # If it then fails on the way up, those switches are the first suspect, and
    # what matters is that the person's application opens -- not that they
    # learn a flag existed. So try again at once, the publisher's own way,
    # before saying anything to anybody. If that works they simply see their
    # application, a beat later than usual.
    ours = [a for a in arguments if a in runtime_policy.CHROMIUM_FLAGS]
    if ours:
        runtime_policy.record_flag_backoff(application_id)
        launch_guard.journal_backoff(name, application_id, ours, failure)
        publishers_own = [a for a in arguments if a not in runtime_policy.CHROMIUM_FLAGS]
        status, seconds, stderr = launch_guard.run(
            publishers_own, env=env, capture=capture)
        retried = launch_guard.classify(status, seconds, stderr)
        if retried is None:
            return status
        # It was not our doing after all. Report the real problem rather than
        # the one we went looking for.
        failure = retried

    launch_guard.report(name, application_id, str(record.get("icon") or ""), failure)
    return status


def _person_folders() -> list[Path]:
    """The folders a person saves into and opens from: Documents, Downloads, ...

    A file chooser runs outside the capsule and answers with a host path such
    as /var/home/nick/Documents/report.pdf. With only a private home inside,
    that path did not exist: saves failed silently and Documents stayed empty.
    These folders are shared at the same paths on both sides; the application's
    own settings and caches stay in its private home.
    """
    try:
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib
        directories = [GLib.get_user_special_dir(getattr(GLib.UserDirectory, name)) for name in (
            "DIRECTORY_DESKTOP", "DIRECTORY_DOCUMENTS", "DIRECTORY_DOWNLOAD", "DIRECTORY_MUSIC",
            "DIRECTORY_PICTURES", "DIRECTORY_PUBLIC_SHARE", "DIRECTORY_TEMPLATES", "DIRECTORY_VIDEOS")]
    except (ImportError, ValueError, AttributeError):
        directories = [str(Path.home() / name) for name in
                       ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Public", "Templates", "Videos")]
    home = Path.home().resolve()
    found = []
    for directory in directories:
        if not directory:
            continue
        path = Path(directory).resolve()
        if path != home and home in path.parents and path.is_dir() and path not in found:
            found.append(path)
    return found


def _launch_appimage(record: dict[str, object], extra: list[str] | None = None) -> int:
    root = Path(str(record["root"])).absolute()
    uid = os.getuid()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
    capsule_runtime = Path(f"/run/user/{uid}")
    data = _private_home(record)
    proxy, bus_socket = _start_bus_proxy(runtime, str(record["sha256"]), runtime_policy.own_bus_arguments(record),
                                         record)
    front, filter_options = _confinement(record)
    arguments = [
        *front,
        "bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--share-net",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc", "--ro-bind", "/sys", "/sys",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home",
        "--bind", str(data), "/home/luma", "--bind", str(data), str(Path.home().resolve()),
        "--ro-bind", str(root), "/app",
        "--dir", "/run", "--dir", "/run/user", "--dir", str(capsule_runtime),
        "--dir", str(capsule_runtime / "pulse"), "--dir", str(capsule_runtime / "at-spi"),
    ]
    for folder in _person_folders():
        arguments.extend(("--bind", str(folder), str(folder)))
    if Path("/dev/dri").exists():
        arguments.extend(("--dir", "/dev/dri", "--dev-bind", "/dev/dri", "/dev/dri"))
    # Wayland, sound, the filtered buses and the application's own X socket:
    # the same for every way an application runs (session_boundary.py).
    wayland = os.environ.get("WAYLAND_DISPLAY")
    display = _start_display(runtime, str(record["sha256"]))
    session = session_boundary.view(runtime, capsule_runtime, dict(os.environ),
                                    accessibility=proxy.accessibility if proxy is not None else None,
                                    bus_socket=bus_socket, display=display)
    if display is not None:
        arguments.extend(("--dir", str(capsule_display.SOCKETS)))
    arguments.extend(session.bubblewrap())
    arguments.extend(filter_options)
    payload = runtime_policy.payload_arguments(record, session.wayland)
    arguments.extend(("--chdir", "/app", "--", str(record["command"]), *payload, *(extra or [])))
    environment = {
        **session_boundary.cleaned(dict(os.environ)), **session.environment,
        "HOME": str(Path.home().resolve()), "PATH": "/usr/bin:/bin",
        "LUMA_CAPSULE_DESKTOP_ID": _desktop_id(record),
        # The desktop's font, text scale, theme and colour scheme reach the
        # sandbox through the settings portal, as they do for an application in
        # a package capsule. The settings store itself is not on this bus, so
        # without this a toolkit falls back to its built-in defaults and the
        # application draws in a font the desktop does not use.
        "GTK_USE_PORTAL": "1",
    }
    if record.get("format") == "appimage":
        # What the AppImage runtime sets. The common AppRun picks its program
        # from basename "$ARGV0" whenever APPIMAGE is set; without ARGV0 it ran
        # "$APPDIR/usr/bin/" and failed (PrusaSlicer: Is a directory).
        environment.update({"APPIMAGE": "/app/AppRun", "APPDIR": "/app", "ARGV0": "/app/AppRun",
                            "OWD": str(Path.home().resolve())})
    try:
        return _guarded(record, arguments, environment)
    finally:
        _stop_bus_proxy(proxy, bus_socket)
        if display is not None:
            display.stop()


def _upgrade_unless_running(record: dict[str, object]) -> dict[str, object]:
    # A link handed to an open window must not wait on rebuilding the capsule
    # underneath it; the upgrade happens the next time it starts.
    if _running_capsule("luma-run-" + str(record.get("application_id") or record["sha256"])[:60]):
        return record
    try:
        from .backends import upgrade_capsule_runtime
        return upgrade_capsule_runtime(record)
    except (InstallerError, OSError, KeyError):
        # Opening what is installed never waits on an upgrade that cannot
        # finish; it is tried again next time.
        return record


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        print("usage: luma-capsule-launch APPLICATION_ID [ARGUMENT ...]", file=sys.stderr)
        return 2
    # Arguments after the identity belong to the payload, so an exported console
    # command behaves like the tool it stands in for.
    identity, extra = values[0], values[1:]
    record: dict[str, object] = {}
    try:
        record = read_record(identity)
        if record.get("format") in {"deb", "rpm", "appimage", "portable"}:
            # Its category pair and what it may still find in the keyring are
            # settled once, before anything else rewrites the record.
            record = capsule_identity.keyring_adoption(capsule_identity.ensure_level(record))[0]
        waiting = record.get("migration")
        if record.get("store") or (isinstance(waiting, dict) and waiting.get("state") == "ready"):
            # An application in the application store, or one whose tree there
            # has been tried and waits for it to be closed.
            from . import closure_launcher, store_refresh
            record = store_refresh.before_start(record)
            if record.get("store"):
                try:
                    from .backends import adopt_own_schemes
                    record = adopt_own_schemes(record)
                except (InstallerError, OSError):
                    pass
                return closure_launcher.launch_record(record, extra)
        if capsule_refresh.state(record).get("pending_image"):
            # A refreshed capsule waits until the application is closed. If
            # it is closed now, this start is the first on the new one.
            try:
                record = capsule_refresh.apply_pending(record)
            except (InstallerError, OSError, KeyError):
                pass
        if record.get("format") == "portable" and record.get("image"):
            record = _upgrade_unless_running(record)
        if record.get("format") in {"deb", "rpm"}:
            from .backends import adopt_own_schemes, refresh_capsule_launcher
            try:
                record = refresh_capsule_launcher(record)
            except InstallerError:
                # Refreshing launcher metadata is housekeeping, not a condition
                # of running something already installed: an older record whose
                # package identity was never written still knows its own image
                # and command, and the person clicked Open.
                pass
            try:
                # An application claims its own link scheme the first time it
                # runs, so this is checked on the way into every run, not once.
                record = adopt_own_schemes(record)
            except (InstallerError, OSError):
                pass
            record = _upgrade_unless_running(record)
            return _launch_deb(record, extra)
        if record.get("format") == "portable" and record.get("image"):
            # Its folder, run inside the capsule that carries the libraries
            # this system lacks.
            return _launch_deb(record, extra)
        if record.get("format") in {"appimage", "portable"}:
            # A portable folder runs in the same sandbox as an AppImage.
            from .backends import adopt_own_schemes, refresh_folder_launcher
            try:
                record = adopt_own_schemes(refresh_folder_launcher(record))
            except (InstallerError, OSError):
                pass
            return _launch_appimage(record, extra)
        raise InstallerError("The application record uses an unsupported runtime.")
    except InstallerError as error:
        print(f"error: {error}", file=sys.stderr)
        launch_guard.report(_display_name(record, identity), identity, str(record.get("icon") or ""),
                            launch_guard.Failure("launcher", 1, 0.0, stderr=[str(error)]))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
