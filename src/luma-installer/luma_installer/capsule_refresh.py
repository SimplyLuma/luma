"""Security updates for the libraries inside an application's capsule.

A capsule is an image: the application's package and the distribution
packages it needs, as they were on the day it was installed. Nothing updated
them afterwards, so an application kept the OpenSSL, the image decoders and
the browser engine of its install date for as long as it stayed installed.

A refresh brings one capsule up to date and keeps the application working:

1. **Upgrade in a build container.** A container is started from the capsule's
   current image and the distribution's own upgrade runs in it (`apt-get
   dist-upgrade`, `dnf upgrade`), against the repositories the image carries.
   That includes any repository the application's package added for itself,
   so the vendor's own updates of the application arrive the same way. Those
   repositories are written to the record as part of the application's
   origin. A source apt cannot read does not keep the others out; it is
   noted. If no package changed, nothing else happens.
2. **Commit a new image.** The upgraded container is committed under a new
   tag. The image in use is not touched.
3. **Hidden start.** The application is started once from the new image where
   nobody sees it: a throwaway home, no network, and a private headless
   display. It has to stay up for a few seconds without failing. Its
   programs and libraries are also checked with the dynamic loader. An image
   that fails either is deleted and the capsule stays as it was.
4. **Swap between runs.** The record is moved to the new image only while the
   application is not running; otherwise the new image waits (`pending`) and
   the launcher makes the swap before the next start.
5. **Keep the previous image.** One previous image is kept. The first real
   start on a new image is watched: if the application fails while opening,
   the record goes back to the previous image at once, the application is
   started again from it, and that package set is not offered again.
   `rollback` does the same on request.
6. **Hold.** A held application is never refreshed until released.

State lives in the record under `refresh`, and `summary()` is what a list of
applications shows: "updated", "held", "rolled back", and so on.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from . import capsule_runtime, launch_guard, network
from .desktop import iter_records, read_record, records_root, safe_id, write_record
from .errors import InstallerError
from .safety import require_free_space

CHECK_SECONDS = 24 * 3600
SMOKE_SECONDS = 10.0
BUILD_TIMEOUT = 3600

# Reading the package indexes is its own step for apt: one source that cannot
# be read (a vendor's repository whose key was never installed) fails the
# whole command, and must not keep the distribution's security updates out.
# The sources that could be read are used; the failure is noted.
INDEX = {
    "deb": ["sh", "-c", "export DEBIAN_FRONTEND=noninteractive; apt-get update 2>&1"],
}
UPGRADE = {
    # Configuration files the application or the person changed are kept; a
    # question nobody is there to answer must not stop a security update.
    "deb": ["sh", "-c", "export DEBIAN_FRONTEND=noninteractive; apt-get -y "
            "-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "
            "--no-install-recommends dist-upgrade && apt-get clean"],
    "rpm": ["sh", "-c", "dnf5 upgrade -y --refresh --setopt=install_weak_deps=False && dnf5 clean all"],
}
PACKAGE_SET = {
    "deb": ["dpkg-query", "-W", "-f", "${Package} ${Version}\\n"],
    "rpm": ["rpm", "-qa", "--qf", "%{NAME} %{EPOCHNUM}:%{VERSION}-%{RELEASE}\\n"],
}
# Every source the package manager reads, as "file<TAB>line"; the ones the
# distribution's own image ships are told apart from what a package added.
SOURCES = {
    "deb": ["sh", "-c", "for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do [ -f \"$f\" ] || continue; "
            "grep -H -E '^(deb|URIs:)' \"$f\" 2>/dev/null; done; true"],
    "rpm": ["sh", "-c", "for f in /etc/yum.repos.d/*.repo; do [ -f \"$f\" ] || continue; "
            "rpm -qf \"$f\" >/dev/null 2>&1 && continue; grep -H -E '^(baseurl|metalink|mirrorlist)=' \"$f\"; done; true"],
}
_DISTRIBUTION_SOURCES = re.compile(r"^/etc/apt/sources\.list(\.d/(debian|ubuntu)\.sources)?$")
_URI = re.compile(r"(https?://[^\s\"'\]]+)")


# -- State -------------------------------------------------------------------


def kind_of(record: dict[str, object]) -> str:
    """The package manager inside this record's capsule, or '' if it has none."""
    if not record.get("image"):
        return ""
    kind = str(record.get("format", ""))
    if kind in {"deb", "rpm"}:
        return kind
    # A downloaded folder runs in a Fedora capsule that carries its libraries.
    return "rpm" if kind == "portable" else ""


def in_store(record: dict[str, object]) -> bool:
    """Whether the application is a tree in the application store (store_refresh.py) rather than a capsule."""
    return isinstance(record.get("store"), dict) and bool(record["store"].get("id"))  # type: ignore[union-attr]


def managed(record: dict[str, object]) -> bool:
    """Whether this application is updated here: a capsule, or a tree in the store."""
    return bool(kind_of(record)) or in_store(record)


def state(record: dict[str, object]) -> dict[str, object]:
    value = record.get("refresh")
    return dict(value) if isinstance(value, dict) else {}


def summary(record: dict[str, object]) -> dict[str, object]:
    """What a list of applications shows about this application's updates."""
    if not managed(record):
        return {}
    current = state(record)
    status = str(current.get("status") or "never checked")
    if current.get("held"):
        status = "held"
    elif current.get("pending_image") or current.get("pending_commit"):
        status = "update waiting"
    return {
        "status": status,
        "held": bool(current.get("held")),
        "checked_unix": int(current.get("checked_unix") or 0),
        "updated_unix": int(current.get("updated_unix") or 0),
        "packages_updated": int(current.get("packages_updated") or 0),
        "can_roll_back": bool(current.get("previous_image") or current.get("previous_commit")),
        "in_store": in_store(record),
        # Recorded when the application was installed; confirmed at every check.
        "vendor_repositories": list(current.get("vendor_repositories") or record.get("vendor_repositories") or []),
        "detail": str(current.get("detail") or ""),
    }


def label(record: dict[str, object]) -> str:
    """One or two words for a list row: "Updated", "Held", "Rolled back", ..."""
    status = str(summary(record).get("status", ""))
    return {"updated": "Updated", "held": "Held", "rolled back": "Rolled back", "current": "Up to date",
            "update waiting": "Update waiting", "update failed": "Update failed"}.get(status, "")


@contextmanager
def _locked(application_id: str) -> Iterator[None]:
    root = records_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(root / f".refresh-{safe_id(application_id)}.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise InstallerError("This application is already being updated.") from error
        yield


def _save(application_id: str, **changes: object) -> dict[str, object]:
    """Merge `changes` into the stored record's refresh state; None removes a key."""
    record = read_record(application_id)
    current = state(record)
    for key, value in changes.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    updated = {**record, "refresh": current}
    write_record(application_id, updated)
    return updated


def hold(application_id: str, held: bool = True) -> dict[str, object]:
    record = read_record(application_id)
    if not managed(record):
        raise InstallerError("This application has no capsule to update.")
    return _save(application_id, held=bool(held) or None)


# -- Podman ------------------------------------------------------------------


def _podman(arguments: list[str], *, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(["podman", *arguments], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallerError(f"The capsule engine could not complete: {error}") from error
    if check and result.returncode != 0:
        lines = [line.strip() for line in (result.stderr + result.stdout).splitlines() if line.strip()]
        raise InstallerError(lines[-1] if lines else "The capsule engine failed.")
    return result


def instance_name(record: dict[str, object]) -> str:
    return "luma-run-" + str(record.get("application_id") or record["sha256"])[:60]


def is_running(record: dict[str, object]) -> bool:
    try:
        result = subprocess.run(["podman", "container", "inspect", "--format", "{{.State.Running}}",
                                 instance_name(record)], capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _image_exists(image: str) -> bool:
    return bool(image) and _podman(["image", "exists", image], check=False).returncode == 0


def _remove_image(image: object) -> None:
    if isinstance(image, str) and image:
        _podman(["image", "rm", image], check=False)


def images(record: dict[str, object]) -> list[str]:
    """Every image this record holds: the one in use, the previous, one waiting."""
    current = state(record)
    found = [str(record.get("image") or ""), str(current.get("previous_image") or ""),
             str(current.get("pending_image") or "")]
    return [image for index, image in enumerate(found) if image and image not in found[:index]]


def next_tag(image: str, now: float | None = None) -> str:
    """`repository:first-tag-rYYYYMMDDhhmmss`, from whichever tag is in use."""
    repository, _, tag = image.rpartition(":")
    stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(now if now is not None else time.time()))
    return f"{repository}:{tag.split('-r', 1)[0]}-r{stamp}"


# -- What is inside ------------------------------------------------------------


def parse_packages(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        name, _, version = line.strip().partition(" ")
        if name and version:
            found[name] = version
    return found


def changed_packages(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """"name old -> new" for every package whose version moved, added or went."""
    changes = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) != after.get(name):
            changes.append(f"{name} {before.get(name, 'absent')} -> {after.get(name, 'removed')}")
    return changes


def fingerprint(packages: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{n} {v}" for n, v in sorted(packages.items())).encode()).hexdigest()[:24]


def parse_vendor_repositories(kind: str, text: str) -> list[dict[str, str]]:
    """The repositories a package added to its own capsule, from SOURCES output."""
    found: list[dict[str, str]] = []
    for line in text.splitlines():
        path, _, rest = line.partition(":")
        if kind == "deb" and _DISTRIBUTION_SOURCES.fullmatch(path):
            continue
        for uri in _URI.findall(rest):
            entry = {"file": path, "uri": uri.rstrip("/")}
            if entry not in found:
                found.append(entry)
    return found[:16]


def _exec(container: str, command: list[str], *, timeout: int = 600, check: bool = True) -> str:
    return _podman(["exec", container, *command], timeout=timeout, check=check).stdout


# -- The hidden start ----------------------------------------------------------


@contextmanager
def hidden_display(runtime: Path | None = None, x11: bool = False) -> Iterator[tuple[Path, str] | None]:
    """A Wayland display nobody sees, for one start; None when there cannot be one.

    The session's own compositor runs a second time, headless, with one
    virtual monitor. It reaches the session bus through a filter that grants
    nothing, so it cannot answer for the real shell.

    With ``x11`` the hidden compositor also keeps an X server of its own, for
    an application that still needs one; `hidden_x11` says where it is.
    """
    runtime = runtime or Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    compositor = shutil.which("mutter")
    if not compositor or not address or not shutil.which("xdg-dbus-proxy") or not runtime.is_dir():
        yield None
        return
    name = f"luma-smoke-{os.getpid()}"
    bus = runtime / f"{name}.bus"
    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes.append(subprocess.Popen(["xdg-dbus-proxy", address, str(bus), "--filter"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _attempt in range(100):
            if bus.exists():
                break
            time.sleep(0.02)
        environment = {**os.environ, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}", "XDG_RUNTIME_DIR": str(runtime)}
        for variable in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY"):
            environment.pop(variable, None)
        report = runtime / f"{name}.x11"
        # The compositor tells what it starts where its X server is; that is how it is learned here.
        tell = ["--", "sh", "-c", 'printf "%s\\n%s\\n" "$DISPLAY" "$XAUTHORITY" > "$1.partial" && mv "$1.partial" "$1"; '
                'exec sleep infinity', "sh", str(report)] if x11 else []
        processes.append(subprocess.Popen(
            [compositor, "--headless", "--wayland", *([] if x11 else ["--no-x11"]), "--wayland-display", name,
             "--virtual-monitor", "1280x800", *tell], env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        socket = runtime / name
        for _attempt in range(1500):  # half a second when idle; a busy device takes longer
            if (socket.exists() and (not x11 or report.exists())) or processes[-1].poll() is not None:
                break
            time.sleep(0.02)
        yield (socket, name) if socket.exists() and processes[-1].poll() is None else None
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        for leftover in (bus, runtime / name, runtime / f"{name}.lock", runtime / f"{name}.x11",
                         runtime / f"{name}.x11.partial"):
            leftover.unlink(missing_ok=True)


def hidden_x11(runtime: Path, name: str) -> tuple[str, str] | None:
    """(DISPLAY, XAUTHORITY) of the hidden compositor's X server, or None when it has none."""
    try:
        display, authority = (runtime / f"{name}.x11").read_text().split("\n")[:2]
    except (OSError, ValueError):
        return None
    return (display, authority) if display else None


def smoke_arguments(record: dict[str, object], image: str, home: Path, display: tuple[Path, str] | None,
                    name: str) -> list[str]:
    """The hidden start: the capsule as the launcher runs it, minus the person.

    No network, no session bus, no sound, none of the person's folders, and a
    home that is thrown away, so a new version of the application cannot
    migrate real settings forward before it has been accepted.
    """
    import shlex
    uid = os.getuid()
    capsule_runtime_dir = f"/run/user/{uid}"
    arguments = ["podman", "run", "--rm", "--name", name, "--network=none", "--userns=keep-id",
                 *capsule_runtime.launch_arguments(record),
                 "--security-opt", "label=type:luma_application.process",
                 "--security-opt", "unmask=/proc/*",
                 "--mount", f"type=tmpfs,destination={capsule_runtime_dir},tmpfs-mode=0700,chown=true",
                 "--env", f"XDG_RUNTIME_DIR={capsule_runtime_dir}",
                 "--volume", f"{home}:{home}:rw,Z", "--env", f"HOME={home}", "--workdir", str(home)]
    if record.get("mcs_level"):
        arguments.extend(("--security-opt", f"label=level:{record['mcs_level']}"))
    if display is not None:
        socket, display_name = display
        arguments.extend(("--volume", f"{socket}:{capsule_runtime_dir}/{display_name}:rw",
                          "--env", f"WAYLAND_DISPLAY={display_name}", "--env", "XDG_SESSION_TYPE=wayland"))
    for variable in ("LANG", *capsule_runtime.DESKTOP_IDENTITY):
        if variable != "XDG_SESSION_TYPE" and os.environ.get(variable):
            arguments.extend(("--env", f"{variable}={os.environ[variable]}"))
    if Path("/dev/dri").exists():
        arguments.extend(("--device", "/dev/dri"))
    if record.get("format") == "portable":
        arguments.extend(("--volume", f"{record['root']}:/app:ro,Z", "--workdir", "/app"))
    arguments.append(image)
    arguments.extend(shlex.split(str(record["command"])))
    arguments.extend(capsule_runtime.payload_arguments(record, display is not None))
    return arguments


def hidden_start(record: dict[str, object], image: str, seconds: float = SMOKE_SECONDS) -> tuple[bool, str]:
    """Start the application from `image` out of sight. (it works, why not)."""
    console = bool(record.get("terminal"))
    name = "luma-smoke-" + safe_id(str(record.get("application_id") or "application"))[:50]
    _podman(["rm", "-f", "--time=0", name], check=False)
    with tempfile.TemporaryDirectory(prefix="luma-smoke-", dir=_scratch_root()) as directory, \
            hidden_display() as display:
        home = Path(directory)
        for folder in (".config", ".cache", ".local/share", ".local/state"):
            (home / folder).mkdir(parents=True, exist_ok=True)
        if display is None and not console:
            return True, "no hidden display on this system; libraries checked only"
        arguments = smoke_arguments(record, image, home, None if console else display, name)
        if console:
            # A console program needs a terminal to stay up; asking for its
            # version proves the program and its libraries load.
            arguments = [*arguments[:arguments.index(image)], "--entrypoint", "sh", image, "-c",
                         'command -v "$1" >/dev/null', "sh", str(record["command"]).split()[0]]
        started = time.monotonic()
        try:
            process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE)
        except OSError as error:
            return False, str(error)
        try:
            _out, errors = process.communicate(timeout=seconds if not console else 120)
            status = process.returncode
        except subprocess.TimeoutExpired:
            # Still up after the wait: that is the pass.
            _podman(["stop", "--time=2", name], check=False, timeout=60)
            try:
                process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
            return True, f"stayed up for {seconds:g} s"
        finally:
            _podman(["rm", "-f", "--time=0", name], check=False)
        elapsed = time.monotonic() - started
        tail = [line for line in errors.decode("utf-8", "replace").splitlines() if line.strip()][-launch_guard.STDERR_LINES:]
        failure = launch_guard.classify(status, elapsed, tail)
        if failure is None and status == 0:
            return True, f"exited cleanly after {elapsed:.1f} s"
        return False, (tail[-1] if tail else f"exited with status {status} after {elapsed:.1f} s")[:300]


def _scratch_root() -> str:
    root = Path.home() / ".local/share/luma/installer/staging"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return str(root)


# -- Refresh -------------------------------------------------------------------


def metered() -> bool:
    """Whether the connection is one a person pays for by the megabyte."""
    try:
        result = subprocess.run(["busctl", "--system", "get-property", "org.freedesktop.NetworkManager",
                                 "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager", "Metered"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # NM_METERED_YES = 1, NM_METERED_GUESS_YES = 3
    return result.returncode == 0 and result.stdout.split()[-1:] in (["1"], ["3"])


def due(record: dict[str, object], now: float | None = None) -> bool:
    if not managed(record):
        return False
    current = state(record)
    if current.get("held"):
        return False
    return (now if now is not None else time.time()) - float(current.get("checked_unix") or 0) >= CHECK_SECONDS


def refresh(application_id: str, *, force: bool = False,
            start: Callable[[dict[str, object], str], tuple[bool, str]] = hidden_start) -> str:
    """Bring one capsule up to date. Returns its status afterwards."""
    application_id = safe_id(application_id)
    if in_store(read_record(application_id)):
        from . import store_refresh
        return store_refresh.refresh(application_id, force=force)
    with _locked(application_id):
        record = read_record(application_id)
        kind = kind_of(record)
        if not kind:
            raise InstallerError("This application has no capsule to update.")
        current = state(record)
        if current.get("held") and not force:
            return "held"
        image = str(current.get("pending_image") or record["image"])
        if not _image_exists(image):
            raise InstallerError("The application's capsule image is missing.")
        require_free_space(Path.home() / ".local/share/containers", 2 * 1024 ** 3, "update this application's capsule")
        mount = ["--volume", f"{record['root']}:/app:ro,Z"] if record.get("format") == "portable" else []
        container = _podman(["create", "--network=slirp4netns", *mount, image, "sleep", "infinity"]).stdout.strip()
        new_image = next_tag(str(record["image"]))
        try:
            _podman(["start", container])
            before = parse_packages(_exec(container, PACKAGE_SET[kind]))
            note = ""
            if kind in INDEX:
                indexed = _podman(["exec", container, *INDEX[kind]], timeout=BUILD_TIMEOUT, check=False)
                if indexed.returncode != 0:
                    errors = [line.strip() for line in indexed.stdout.splitlines() if line.startswith("E:")]
                    note = ("A software source in this capsule could not be read; the others were used. "
                            + (errors[-1] if errors else ""))[:300]
            try:
                network.retrying(lambda: _exec(container, UPGRADE[kind], timeout=BUILD_TIMEOUT))
            except InstallerError as error:
                _save(application_id, checked_unix=int(time.time()), detail=str(error)[:300])
                raise
            after = parse_packages(_exec(container, PACKAGE_SET[kind]))
            repositories = parse_vendor_repositories(kind, _exec(container, SOURCES[kind], check=False))
            changes = changed_packages(before, after)
            checked = {"checked_unix": int(time.time()), "vendor_repositories": repositories}
            if not changes:
                _save(application_id, **checked, status=current.get("status") or "current", detail=note)
                return str(current.get("status") or "current")
            if current.get("refused") == fingerprint(after):
                # The same packages that already failed here; nothing new to try.
                _save(application_id, **checked)
                return str(current.get("status") or "current")
            files = (capsule_runtime.package_files(container, str(record.get("package") or ""), kind)
                     if record.get("package") else "")
            missing = capsule_runtime.missing_libraries(container, files) if files else []
            _podman(["stop", "--time", "5", container])
            _podman(["commit", container, new_image], timeout=BUILD_TIMEOUT)
        finally:
            _podman(["rm", "-f", "--time=0", container], check=False)

        new_missing = sorted(set(missing) - set(record.get("missing_libraries") or []))
        works, why = (False, "missing after the update: " + ", ".join(new_missing)) if new_missing \
            else start(record, new_image)
        if not works:
            _remove_image(new_image)
            _save(application_id, **checked, status="update failed", refused=fingerprint(after),
                  detail=f"The updated capsule did not start and was not used: {why}"[:400])
            return "update failed"
        waiting = str(current.get("pending_image") or "")
        if waiting and waiting != new_image:
            _remove_image(waiting)
        _save(application_id, **checked, pending_image=new_image, pending_packages=changes[:200],
              pending_fingerprint=fingerprint(after), detail=f"{why}. {note}".rstrip(". ") if note else why)
        return str(summary(apply_pending(read_record(application_id))).get("status"))


def apply_pending(record: dict[str, object], running: bool | None = None) -> dict[str, object]:
    """Move the record to the waiting image, if there is one and the application is closed."""
    current = state(record)
    pending = str(current.get("pending_image") or "")
    if not pending or not kind_of(record):
        return record
    if running if running is not None else is_running(record):
        return record
    application_id = str(record["application_id"])
    if not _image_exists(pending):
        return _save(application_id, pending_image=None, pending_packages=None, pending_fingerprint=None)
    stored = read_record(application_id)
    older = str(state(stored).get("previous_image") or "")
    previous = str(stored["image"])
    # The runtime rules the previous image was brought up to: a later
    # runtime upgrade rewrites only the image in use.
    refresh_state = {**state(stored), "previous_image": previous, "status": "updated", "confirmed": False,
                     "previous_runtime_version": int(stored.get("runtime_version", 0) or 0),
                     "updated_unix": int(time.time()),
                     "packages_updated": len(current.get("pending_packages") or []),
                     "packages": list(current.get("pending_packages") or [])[:200],
                     "fingerprint": current.get("pending_fingerprint")}
    for key in ("pending_image", "pending_packages", "pending_fingerprint"):
        refresh_state.pop(key, None)
    updated = {**stored, "image": pending, "refresh": refresh_state}
    write_record(application_id, updated)
    if older and older not in {previous, pending}:
        _remove_image(older)
    return {**record, "image": pending, "refresh": refresh_state}


def unconfirmed(record: dict[str, object]) -> bool:
    """Whether this start is the first real one on a refreshed image."""
    current = state(record)
    return current.get("status") == "updated" and current.get("confirmed") is False \
        and bool(current.get("previous_image") or current.get("previous_commit"))


def confirm(record: dict[str, object]) -> dict[str, object]:
    if not unconfirmed(record):
        return record
    return _save(str(record["application_id"]), confirmed=True)


def rollback(application_id: str, reason: str = "") -> dict[str, object]:
    """Go back to the previous image and do not take that update again."""
    record = read_record(safe_id(application_id))
    if in_store(record):
        from . import store_refresh
        return store_refresh.rollback(application_id, reason)
    current = state(record)
    previous = str(current.get("previous_image") or "")
    if not previous or not _image_exists(previous):
        raise InstallerError("There is no previous version of this application's capsule to go back to.")
    refused_image = str(record["image"])
    refresh_state = {**current, "status": "rolled back", "confirmed": True, "refused": current.get("fingerprint"),
                     "detail": (reason or "Rolled back to the previous capsule.")[:400],
                     "rolled_back_unix": int(time.time())}
    runtime_version = refresh_state.pop("previous_runtime_version", record.get("runtime_version", 0))
    for key in ("previous_image", "packages", "packages_updated", "fingerprint"):
        refresh_state.pop(key, None)
    updated = {**record, "image": previous, "runtime_version": runtime_version, "refresh": refresh_state}
    write_record(str(record["application_id"]), updated)
    if refused_image != previous and not is_running(record):
        _remove_image(refused_image)
    return updated


def forget(record: dict[str, object]) -> None:
    """Delete the images a removed application leaves besides the one in use."""
    for image in images(record)[1:]:
        _remove_image(image)


def refresh_all(now: float | None = None, log: Callable[[str], None] = print) -> int:
    """Refresh every capsule that is due. Returns how many could not be checked."""
    if metered():
        log("metered connection; capsules are not refreshed now")
        return 0
    failures = 0
    for record in iter_records():
        if not due(record, now):
            continue
        application_id = str(record.get("application_id"))
        try:
            log(f"{application_id}: {refresh(application_id)}")
        except InstallerError as error:
            failures += 1
            log(f"{application_id}: not checked: {error}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luma-capsule-refresh",
                                     description="Security updates for installed application capsules")
    parser.add_argument("application_id", nargs="?")
    parser.add_argument("--all", action="store_true", help="refresh every capsule that is due")
    parser.add_argument("--force", action="store_true", help="refresh now, even if held or checked recently")
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--status", action="store_true")
    values = parser.parse_args(argv)
    try:
        if values.all:
            # One line per capsule as it finishes, for the journal.
            return 1 if refresh_all(log=lambda line: print(line, flush=True)) else 0
        if values.status:
            rows = {str(r.get("application_id")): summary(r) for r in iter_records()
                    if managed(r) and values.application_id in (None, r.get("application_id"))}
            print(json.dumps(rows, indent=2, sort_keys=True))
            return 0
        if not values.application_id:
            parser.error("name an application or pass --all")
        if values.hold or values.release:
            hold(values.application_id, values.hold)
            print("held" if values.hold else "released")
        elif values.rollback:
            rollback(values.application_id, "Rolled back on request.")
            print("rolled back")
        else:
            print(refresh(values.application_id, force=values.force))
        return 0
    except InstallerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
