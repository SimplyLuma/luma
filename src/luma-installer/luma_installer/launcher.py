from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import capsule_runtime as runtime_policy
from . import launch_guard
from .desktop import read_record, safe_id
from .errors import InstallerError


def _socket_mount(arguments: list[str], source: Path, destination: Path) -> None:
    if source.exists():
        arguments.extend(("--volume", f"{source}:{destination}:rw"))


def _runtime_authority(runtime: Path) -> Path | None:
    value = os.environ.get("XAUTHORITY")
    if not value:
        return None
    try:
        authority = Path(value).resolve(strict=True)
        runtime_root = runtime.resolve(strict=True)
    except OSError:
        return None
    if authority.is_file() and authority.is_relative_to(runtime_root):
        return authority
    return None


def _start_bus_proxy(runtime: Path, identity: str) -> tuple[subprocess.Popen[bytes] | None, Path | None]:
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not address:
        return None, None
    root = runtime / "luma-installer-bus"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    socket = root / f"{identity[:24]}-{os.getpid()}.bus"
    socket.unlink(missing_ok=True)
    try:
        process = subprocess.Popen([
            "xdg-dbus-proxy", address, str(socket), "--filter",
            "--talk=org.freedesktop.portal.Desktop",
            "--talk=org.freedesktop.Notifications",
            "--talk=org.a11y.Bus",
            runtime_policy.SECRET_SERVICE,
            "--own=org.mpris.MediaPlayer2.*",
        ], close_fds=True)
    except OSError as error:
        raise InstallerError(f"The application portal boundary could not start: {error}") from error
    for _attempt in range(100):
        if socket.exists():
            return process, socket
        if process.poll() is not None:
            raise InstallerError("The application portal boundary exited before launch.")
        time.sleep(0.01)
    process.terminate()
    raise InstallerError("The application portal boundary did not become ready.")


def _stop_bus_proxy(process: subprocess.Popen[bytes] | None, socket: Path | None) -> None:
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
    if socket is not None:
        socket.unlink(missing_ok=True)


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


def _capsule_level(identity: str) -> str:
    """The same private SELinux categories for an application on every launch.

    Podman otherwise picks new categories each run, and relabelling the
    application's whole private home to match (tens of thousands of files for
    an Electron app) cost a quarter to half a second of every launch. Two
    applications still never share a pair.
    """
    digest = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    first, second = digest % 1024, (digest >> 10) % 1023
    second += second >= first
    low, high = sorted((first, second))
    return f"s0:c{low},c{high}"


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
    proxy, bus_socket = _start_bus_proxy(runtime, str(record["sha256"]))
    arguments = ["podman", "run", "--rm", "--name", instance, "--network=host", "--userns=keep-id", *runtime_policy.launch_arguments(record),
                 "--security-opt", "label=type:luma_application.process",
                 "--security-opt", f"label=level:{_capsule_level(str(record['sha256']))}",
                 # A fresh proc can only be mounted where proc isn't partly hidden;
                 # without this, nested sandboxes (bubblewrap) cannot start.
                 "--security-opt", "unmask=/proc/*",
                 "--mount", f"type=tmpfs,destination={capsule_runtime},tmpfs-mode=0700,chown=true",
                 "--env", f"XDG_RUNTIME_DIR={capsule_runtime}",
                 "--volume", "/usr/libexec/luma-capsule-open:/usr/bin/xdg-open:ro",
                 "--env", "BROWSER=/usr/bin/xdg-open",
                 "--env", "GTK_USE_PORTAL=1",
                 "--env", f"LUMA_CAPSULE_DESKTOP_ID={_desktop_id(record)}"]
    # A console payload needs a terminal on both sides of the capsule boundary:
    # without one it reads EOF immediately and exits before drawing anything.
    if record.get("terminal") and sys.stdin.isatty() and sys.stdout.isatty():
        arguments.extend(("--interactive", "--tty"))
        if os.environ.get("TERM"):
            arguments.extend(("--env", f"TERM={os.environ['TERM']}"))
    wayland = os.environ.get("WAYLAND_DISPLAY")
    if wayland:
        _socket_mount(arguments, runtime / wayland, capsule_runtime / wayland)
        arguments.extend(("--env", f"WAYLAND_DISPLAY={wayland}", "--env", f"XDG_RUNTIME_DIR={capsule_runtime}"))
    # The accessibility bus alongside sound and video: a11y clients read its
    # address from the session bus and find it at this exact path, so a capsule
    # without it tells every toolkit the desktop has no accessibility.
    for relative in ("pipewire-0", "pulse/native", "at-spi/bus"):
        _socket_mount(arguments, runtime / relative, capsule_runtime / relative)
    # The mount point for the sound socket is created by the container runtime
    # and owned by root. PulseAudio clients refuse a runtime directory they do
    # not own and never connect: no microphone, and no sound, in any capsule.
    # Name the socket directly, as Flatpak does.
    if (runtime / "pulse/native").exists():
        arguments.extend(("--env", f"PULSE_SERVER=unix:{capsule_runtime / 'pulse/native'}"))
    if bus_socket is not None:
        capsule_bus = capsule_runtime / bus_socket.name
        _socket_mount(arguments, bus_socket, capsule_bus)
        arguments.extend(("--env", f"DBUS_SESSION_BUS_ADDRESS=unix:path={capsule_bus}"))
    if Path("/tmp/.X11-unix").exists() and os.environ.get("DISPLAY"):
        arguments.extend(("--volume", "/tmp/.X11-unix:/tmp/.X11-unix:ro"))
    authority = _runtime_authority(runtime)
    if authority is not None:
        capsule_authority = capsule_runtime / authority.name
        arguments.extend(("--volume", f"{authority}:{capsule_authority}:ro", "--env", f"XAUTHORITY={capsule_authority}"))
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
    for name in ("DISPLAY", "LANG", "DESKTOP_STARTUP_ID", "XDG_ACTIVATION_TOKEN", *runtime_policy.DESKTOP_IDENTITY):
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
    try:
        return _guarded(record, arguments)
    finally:
        _stop_bus_proxy(proxy, bus_socket)


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


def _guarded(record: dict[str, object], arguments: list[str], env: dict[str, str] | None = None) -> int:
    """Run the application; if it fails while opening, stop the spinner and say so."""
    capture = launch_guard.should_capture() and not record.get("terminal")
    status, seconds, stderr = launch_guard.run(arguments, env=env, capture=capture)
    failure = launch_guard.classify(status, seconds, stderr)
    if failure is None:
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
    proxy, bus_socket = _start_bus_proxy(runtime, str(record["sha256"]))
    arguments = [
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
    wayland = os.environ.get("WAYLAND_DISPLAY")
    if wayland and (runtime / wayland).exists():
        arguments.extend(("--ro-bind", str(runtime / wayland), str(capsule_runtime / wayland)))
    # The accessibility bus belongs in the capsule with sound and video: the
    # address a toolkit reads from the session bus names this exact path, and
    # without it every application starts by reporting that this desktop has
    # no accessibility (screen reader, magnifier, and every assistive tool).
    for relative in ("pipewire-0", "pulse/native", "at-spi/bus"):
        path = runtime / relative
        if path.exists():
            arguments.extend(("--ro-bind", str(path), str(capsule_runtime / relative)))
    if bus_socket is not None:
        arguments.extend(("--bind", str(bus_socket), str(capsule_runtime / bus_socket.name)))
    if Path("/tmp/.X11-unix").exists() and os.environ.get("DISPLAY"):
        arguments.extend(("--ro-bind", "/tmp/.X11-unix", "/tmp/.X11-unix"))
    authority = _runtime_authority(runtime)
    if authority is not None:
        arguments.extend(("--ro-bind", str(authority), str(capsule_runtime / authority.name)))
    payload = runtime_policy.payload_arguments(record, bool(wayland))
    arguments.extend(("--chdir", "/app", "--", str(record["command"]), *payload, *(extra or [])))
    environment = {
        **os.environ, "HOME": str(Path.home().resolve()), "XDG_RUNTIME_DIR": str(capsule_runtime), "PATH": "/usr/bin:/bin",
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
    if (runtime / "pulse/native").exists():
        environment["PULSE_SERVER"] = f"unix:{capsule_runtime / 'pulse/native'}"
    if bus_socket is not None:
        environment["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={capsule_runtime / bus_socket.name}"
    if authority is not None:
        environment["XAUTHORITY"] = str(capsule_runtime / authority.name)
    try:
        return _guarded(record, arguments, environment)
    finally:
        _stop_bus_proxy(proxy, bus_socket)


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
