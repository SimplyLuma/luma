from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


FP6_COMPATIBLE = b"fairphone,fp6"
DEFAULT_COMPATIBLE = Path("/proc/device-tree/compatible")
DEFAULT_KERNEL_NOTES = Path("/sys/kernel/notes")
DEFAULT_FP6_ALLOWLIST = Path(
    "/usr/share/luma/android/hardware/fairphone-fp6-kernel-notes.sha256"
)
FP6_GPU_FAULT_MARKER = Path("/run/luma-fp6-android-gpu-fault")
MAX_KERNEL_NOTES_BYTES = 1024 * 1024
FP6_SOFTWARE_PORT = 33990
FP6_SOFTWARE_SOCKET = "luma-android/luma-android-rdp"
FP6_SOFTWARE_WESTON_SOCKET = "luma-android-rdp"
FP6_SOFTWARE_CONFIG = "/usr/share/luma/android/fp6-software-weston.ini"
FP6_SOFTWARE_UNIT = "luma-android-fp6-software-compositor.service"
FP6_SOFTWARE_UNIT_FRAGMENT = (
    "/usr/lib/systemd/user/luma-android-fp6-software-compositor.service"
)
FP6_SOFTWARE_UNIT_DROPIN = "/usr/lib/systemd/user/service.d/10-timeout-abort.conf"


@dataclass(frozen=True)
class KernelAdmission:
    allowed: bool
    notes_sha256: str = ""
    reason: str = ""


def is_fp6(compatible_path: Path = DEFAULT_COMPATIBLE) -> bool:
    try:
        compatible = compatible_path.read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError, OSError):
        return False
    return FP6_COMPATIBLE in compatible


def kernel_notes_sha256(notes_path: Path = DEFAULT_KERNEL_NOTES) -> str:
    try:
        with notes_path.open("rb") as stream:
            notes = stream.read(MAX_KERNEL_NOTES_BYTES + 1)
    except (FileNotFoundError, PermissionError, OSError):
        return ""
    if not notes or len(notes) > MAX_KERNEL_NOTES_BYTES:
        return ""
    return hashlib.sha256(notes).hexdigest()


def admitted_kernel_notes(allowlist_path: Path = DEFAULT_FP6_ALLOWLIST) -> frozenset[str]:
    digests: set[str] = set()
    try:
        lines = allowlist_path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError, OSError, UnicodeError):
        return frozenset()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 1 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            return frozenset()
        digests.add(fields[0])
    return frozenset(digests)


def fp6_kernel_admission(
    *,
    compatible_path: Path = DEFAULT_COMPATIBLE,
    notes_path: Path = DEFAULT_KERNEL_NOTES,
    allowlist_path: Path = DEFAULT_FP6_ALLOWLIST,
    fault_marker: Path = FP6_GPU_FAULT_MARKER,
) -> KernelAdmission:
    if not is_fp6(compatible_path):
        return KernelAdmission(True, reason="not-fp6")
    if fault_marker.exists():
        return KernelAdmission(False, reason="gpu-fault-latched")
    digest = kernel_notes_sha256(notes_path)
    if not digest:
        return KernelAdmission(False, reason="kernel-identity-unavailable")
    if digest not in admitted_kernel_notes(allowlist_path):
        return KernelAdmission(False, digest, "kernel-not-admitted")
    return KernelAdmission(True, digest, "verified-kernel")


def _process_socket_inodes(process_root: Path) -> frozenset[str]:
    inodes: set[str] = set()
    try:
        descriptors = (process_root / "fd").iterdir()
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if match:
                inodes.add(match.group(1))
    except OSError:
        return frozenset()
    return frozenset(inodes)


def _has_exact_loopback_listener(process_root: Path, socket_inodes: frozenset[str]) -> bool:
    matches = 0
    try:
        lines = (process_root / "net" / "tcp").read_text(encoding="ascii").splitlines()[1:]
    except (OSError, UnicodeError):
        return False
    for line in lines:
        fields = line.split()
        if len(fields) < 10 or fields[9] not in socket_inodes:
            continue
        address, separator, port = fields[1].partition(":")
        if not separator or int(port, 16) != FP6_SOFTWARE_PORT or fields[3] != "0A":
            continue
        if address != "0100007F":
            return False
        matches += 1
    return matches == 1


def fp6_software_presentation_active(
    *,
    runtime_users_root: Path = Path("/run/user"),
    proc_root: Path = Path("/proc"),
) -> bool:
    """Verify the exact CPU-only compositor allowed to contain FP6 Android."""
    try:
        user_directories = tuple(runtime_users_root.iterdir())
    except OSError:
        return False
    for user_directory in user_directories:
        if not user_directory.name.isdecimal() or user_directory.is_symlink():
            continue
        uid = int(user_directory.name)
        runtime = user_directory / "luma-android"
        pid_file = runtime / "rdp-weston.pid"
        try:
            runtime_metadata = runtime.lstat()
            pid_metadata = pid_file.lstat()
            if (
                not stat.S_ISDIR(runtime_metadata.st_mode)
                or runtime_metadata.st_uid != uid
                or stat.S_IMODE(runtime_metadata.st_mode) & 0o077
                or not stat.S_ISREG(pid_metadata.st_mode)
                or pid_metadata.st_uid != uid
                or stat.S_IMODE(pid_metadata.st_mode) & 0o077
            ):
                continue
            pid_text = pid_file.read_text(encoding="ascii").strip()
            if not pid_text.isdecimal() or int(pid_text) < 2:
                continue
        except (OSError, UnicodeError):
            continue
        process_root = proc_root / pid_text
        try:
            process_metadata = process_root.stat()
            executable = os.path.realpath(process_root / "exe")
            arguments = (process_root / "cmdline").read_bytes().split(b"\0")
            if arguments and not arguments[-1]:
                arguments.pop()
        except OSError:
            continue
        if process_metadata.st_uid != uid or executable != "/usr/bin/weston":
            continue
        certificate = runtime / "rdp.crt"
        private_key = runtime / "rdp.key"
        expected = [
            "/usr/bin/weston",
            "--backend=rdp",
            "--renderer=pixman",
            "--shell=kiosk",
            f"--config={FP6_SOFTWARE_CONFIG}",
            f"--socket={FP6_SOFTWARE_WESTON_SOCKET}",
            "--address=127.0.0.1",
            f"--port={FP6_SOFTWARE_PORT}",
            "--width=558",
            "--height=1088",
            "--no-resizeable",
            f"--rdp-tls-cert={certificate}",
            f"--rdp-tls-key={private_key}",
            f"--log={runtime / 'fp6-software-weston.log'}",
        ]
        if arguments != [argument.encode() for argument in expected]:
            continue
        try:
            environment = (process_root / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        if f"XDG_RUNTIME_DIR={runtime}".encode() not in environment:
            continue
        try:
            for credential in (certificate, private_key):
                metadata = credential.lstat()
                if (
                    credential.is_symlink()
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != uid
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise ValueError
            wayland_socket = runtime / FP6_SOFTWARE_WESTON_SOCKET
            socket_metadata = wayland_socket.lstat()
            if not stat.S_ISSOCK(socket_metadata.st_mode) or socket_metadata.st_uid != uid:
                continue
        except (OSError, ValueError):
            continue
        socket_inodes = _process_socket_inodes(process_root)
        if not socket_inodes or not _has_exact_loopback_listener(process_root, socket_inodes):
            continue
        # PrivateDevices=yes is the primary boundary. This additional check
        # makes a packaging regression fail closed if Weston somehow gains DRM.
        try:
            for descriptor in (process_root / "fd").iterdir():
                try:
                    if os.readlink(descriptor).startswith("/dev/dri/"):
                        raise ValueError
                except OSError:
                    continue
        except (OSError, ValueError):
            continue
        return True
    return False


def fp6_software_service_admitted(
    *, session_user: str = "luma", timeout: int = 5
) -> bool:
    """Authenticate the immutable active user unit without cross-user ptrace.

    The root watchdog cannot traverse a protected user's proc/runtime tree and
    should not gain the capabilities to do so. The system manager can instead
    query that user's service manager over its existing AF_UNIX control plane.
    A user override or extra drop-in changes the reported identity and fails
    this check closed.
    """
    try:
        completed = subprocess.run(
            [
                "/usr/bin/systemctl",
                "--user",
                f"--machine={session_user}@.host",
                "show",
                FP6_SOFTWARE_UNIT,
                "-p", "ActiveState",
                "-p", "SubState",
                "-p", "MainPID",
                "-p", "FragmentPath",
                "-p", "DropInPaths",
                "-p", "ExecStart",
                "--no-pager",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key not in values:
            values[key] = value
    if values.get("ActiveState") != "active" or values.get("SubState") != "running":
        return False
    main_pid = values.get("MainPID", "")
    if not main_pid.isdecimal() or int(main_pid) < 2:
        return False
    if values.get("FragmentPath") != FP6_SOFTWARE_UNIT_FRAGMENT:
        return False
    if values.get("DropInPaths", "") not in {"", FP6_SOFTWARE_UNIT_DROPIN}:
        return False
    execution = values.get("ExecStart", "")
    return (
        "path=/usr/libexec/luma-waydroid-fp6-software-compositor" in execution
        and "argv[]=/usr/libexec/luma-waydroid-fp6-software-compositor" in execution
        and "ignore_errors=no" in execution
    )
